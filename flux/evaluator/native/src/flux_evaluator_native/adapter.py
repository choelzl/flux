"""Native, in-repo roofline evaluator (docs/decisions.md D75): the first evaluator in this repo
whose cost model is genuinely native Rust, not a wrapped external tool. Reports the compute-bound
lower-bound `latency_cycles` (`total_macs / lanes`) — the exact same, already independently
validated formula `validity/src/flux_validity/roofline.py` checks every other evaluator's own
result against, computed here in real Rust instead of merely checked in Python.

**This is a theoretical lower bound, not a prediction of achievable latency** — said loudly here
and in the package README, not left implicit: no real accelerator design hits it exactly except
one with zero pipeline fill/drain and perfect reuse. Real Verilator RTL measures 529 cycles for
the exact same candidate this evaluator reports 512.0 for (docs/phase1-exit-criterion-report.md)
— 3% above the bound, the real pipeline-fill cost this evaluator does not model. Do not treat
this evaluator's own output as a substitute for ZigZag/Timeloop/RTL's own predictions; it exists
to give search strategies a fast, free, always-available sanity floor, and to give this repo a
real, working native/PyO3 core to build future, genuinely expensive native cost models on
(docs/architecture.md's own long-stated, never-before-built "Performance engineering" target).
"""

from __future__ import annotations

import importlib.util
import json
import threading
from types import ModuleType

import flux_ir
from flux_evaluator_abi import (
    Bottleneck,
    Budget,
    Candidate,
    Domain,
    Escalation,
    Estimate,
    Limiter,
    Method,
    Provenance,
    Result,
    Validity,
)

from .build import ensure_native_extension
from .errors import NotExpressibleError


class NativeEvaluator:
    """Runs the real, compiled `flux_core` Rust extension against a translated Workload/
    Architecture IR pair. v0.1 scope matches `validity/roofline.py`'s own: `Candidate.workload`
    must be an inline dict with exactly one two-operand `einsum` op with a 3-dim bound;
    `Candidate.arch` must be an inline dict with exactly one `class=="compute"` hierarchy node
    with exactly one spatial dimension; `Candidate.mapping` must be `None` (the bound is
    mapping-independent by construction).
    """

    name = "native"

    def __init__(self, *, timeout_s: float = 300.0) -> None:
        self.timeout_s = timeout_s
        self._module: ModuleType | None = None
        self._build_lock = threading.Lock()

    def evaluate(self, candidate: Candidate, budget: Budget, metrics: frozenset[str]) -> Result:
        if not isinstance(candidate.workload, dict):
            raise NotExpressibleError(
                "NativeEvaluator v0.1 requires an inline Workload IR dict as Candidate.workload "
                "(no result-store hash resolution yet)."
            )
        if not isinstance(candidate.arch, dict):
            raise NotExpressibleError(
                "NativeEvaluator v0.1 requires an inline Architecture IR dict as Candidate.arch "
                "(the roofline bound needs a real declared lane count)."
            )
        if candidate.mapping is not None:
            raise NotExpressibleError(
                "NativeEvaluator v0.1 reports a mapping-independent lower bound — leave "
                "Candidate.mapping as None, the same 'mapping must be None' scope "
                "evaluators/thermal/evaluators/dramsim3 already use for their own "
                "architecture/traffic-level quantities."
            )

        module = self._ensure_module()
        workload_json = json.dumps(candidate.workload)
        arch_json = json.dumps(candidate.arch)
        try:
            cycles = module.roofline_latency_cycles(workload_json, arch_json)
        except ValueError as exc:
            raise NotExpressibleError(str(exc)) from exc

        result_metrics: dict[str, Estimate] = {}
        if not metrics or "latency_cycles" in metrics:
            result_metrics["latency_cycles"] = Estimate(
                value=cycles, ci_low=cycles, ci_high=cycles, unit="cycles", method=Method.ANALYTIC,
            )

        return Result(
            metrics=result_metrics,
            validity=Validity(ok=True, checker_version="none-v0.1"),
            domain=Domain(in_domain=True),
            bottleneck=Bottleneck(
                limiter=Limiter.COMPUTE, per_level_utilisation={"lower_bound_cycles": cycles},
            ),
            provenance=Provenance(
                evaluator="flux-core@0.1",
                inputs={
                    "workload_hash": flux_ir.content_hash(candidate.workload),
                    "arch_hash": flux_ir.content_hash(candidate.arch),
                },
            ),
            escalation=Escalation(recommended=False),
        )

    def evaluate_batch(
        self, candidates: list[Candidate], budget: Budget, metrics: frozenset[str]
    ) -> list[Result]:
        # v0.1: sequential internally, same allowance the Evaluator ABI's own docstring makes
        # explicit ("Implementations are free to evaluate sequentially internally at v0.1 — the
        # *interface* being batched is what matters for now"). The real native-batching win this
        # decision demonstrates is measured directly against `flux_core`'s own
        # `roofline_latency_cycles_for_lane_sweep` (see tests/integration/
        # test_native_evaluator_live.py and core/benches/), not through this ABI method — that
        # numeric hot-loop shape doesn't fit `evaluate_batch`'s heterogeneous-candidate signature.
        return [self.evaluate(c, budget, metrics) for c in candidates]

    def _ensure_module(self) -> ModuleType:
        if self._module is not None:
            return self._module
        with self._build_lock:
            if self._module is not None:
                return self._module
            binary_path = ensure_native_extension(timeout_s=self.timeout_s)
            spec = importlib.util.spec_from_file_location("flux_core", binary_path)
            if spec is None or spec.loader is None:
                raise NotExpressibleError(f"could not load a module spec for {binary_path}")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            self._module = module
            return module
