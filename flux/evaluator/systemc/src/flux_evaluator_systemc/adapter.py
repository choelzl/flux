"""SystemC coarse-grain backend adapter implementing the Flux Evaluator ABI (docs/evaluator-abi.md,
docs/calibration.md's fidelity ladder): a fast functional-correctness + timing pre-check for the exact same
`mac_array` design `evaluators/rtl` simulates cycle-accurately, escalating there for the
authoritative number.

Reuses `evaluators/rtl`'s shape/architecture translators directly (`einsum_op_to_mac_array_shape`,
`architecture_ir_to_lanes`) and its golden-reference generator (`generate_test_vectors`) — same
"adapters, not forks" discipline as every other evaluator here, and the only way both adapters
can honestly claim to model the *same* design. One wrinkle from that reuse: those translators
raise `flux_evaluator_rtl.NotExpressibleError` (both `ValueError` subclasses, so `except
ValueError` still catches either), not this package's own — reserved for validation failures
specific to this adapter (`Candidate.mapping`, multi-op workloads).

v0.1 status: identical scope to `evaluators/rtl` (`Candidate.workload` — one two-operand
`einsum` op; `Candidate.arch` — `None` or a single-spatial-dim Architecture IR dict;
`Candidate.mapping` must be `None`) — deliberately, since it targets the identical
`mac_array`/`mac_array_coarse` design, not a separate one.

Unlike `evaluators/rtl`, no recompilation per shape: `mac_array_coarse.cpp`'s B/C/K/LANES are
runtime arguments, not Verilog compile-time parameters — the binary is built once per process
(`_ensure_binary`, cached) and reused across every shape. That's the actual value of a coarse-
grain model: not just a faster *simulation*, but a faster *iteration loop*.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
import threading
from pathlib import Path

import flux_ir
from flux_evaluator_abi import (
    Bottleneck,
    Budget,
    Candidate,
    Constraint,
    Domain,
    Escalation,
    Estimate,
    Limiter,
    Method,
    Provenance,
    Result,
    Validity,
)
from flux_evaluator_rtl import architecture_ir_to_lanes, einsum_op_to_mac_array_shape, generate_test_vectors

from .errors import NotExpressibleError

_REFERENCE_DIR = Path(__file__).resolve().parent / "reference"
_RESULT_RE = re.compile(r"^RESULT (PASS|FAIL) cycles=(\d+)(?: errors=(\d+))?", re.MULTILINE)


class SystemCEvaluator:
    """Runs a real, compiled SystemC simulation of `mac_array_coarse.cpp` against a translated
    Workload/Architecture IR pair — the coarse-grain rung above analytic estimates and below
    `evaluators/rtl`'s cycle-accurate Verilator simulation.
    """

    name = "systemc"

    def __init__(self, *, timeout_s: float = 60.0, seed: int = 0) -> None:
        self.timeout_s = timeout_s
        self.seed = seed
        self._binary_path: Path | None = None
        self._build_lock = threading.Lock()

    def evaluate(self, candidate: Candidate, budget: Budget, metrics: frozenset[str]) -> Result:
        if not isinstance(candidate.workload, dict):
            raise NotExpressibleError(
                "SystemCEvaluator v0.1 requires an inline Workload IR dict as Candidate.workload "
                "(no result-store hash resolution yet)."
            )
        if candidate.mapping is not None:
            raise NotExpressibleError(
                "SystemCEvaluator v0.1 does not translate Mapping IR: mac_array_coarse models "
                "the same single, fixed loop schedule as evaluators/rtl's mac_array.sv, not a "
                "configurable one — leave Candidate.mapping as None."
            )

        ops = candidate.workload.get("ops", [])
        einsum_ops = [op for op in ops if op.get("kind") == "einsum"]
        if not einsum_ops:
            raise NotExpressibleError(
                f"workload {candidate.workload.get('id')!r} has no 'einsum' ops; SystemCEvaluator "
                "cannot simulate data_dependent or compute_kernel ops (docs/decisions.md D1)."
            )
        if len(einsum_ops) > 1:
            raise NotExpressibleError(
                f"workload {candidate.workload.get('id')!r} has {len(einsum_ops)} einsum ops; "
                "SystemCEvaluator v0.1 evaluates exactly one op per call, same limit as "
                "evaluators/rtl/zigzag/timeloop."
            )
        shape = einsum_op_to_mac_array_shape(einsum_ops[0])
        workload_hash = flux_ir.content_hash(candidate.workload)

        if candidate.arch is None:
            lanes = 8
            arch_desc = str(_REFERENCE_DIR)
        elif isinstance(candidate.arch, dict):
            lanes = architecture_ir_to_lanes(candidate.arch)
            arch_desc = f"translated:{flux_ir.content_hash(candidate.arch)}"
        else:
            raise NotExpressibleError(
                "SystemCEvaluator v0.1 only accepts Candidate.arch as None or an inline "
                "Architecture IR dict (translated via evaluators/rtl's architecture_translator.py)."
            )

        if shape["K"] % lanes != 0:
            raise NotExpressibleError(
                f"K={shape['K']} is not a multiple of LANES={lanes}; mac_array_coarse has no "
                "support for a ragged final K-group (same limit as evaluators/rtl)."
            )

        cycles, ok, error_count = self._run_systemc(shape, lanes, workload_hash)

        result_metrics: dict[str, Estimate] = {}
        if not metrics or "latency_cycles" in metrics:
            result_metrics["latency_cycles"] = Estimate(
                value=float(cycles),
                ci_low=float(cycles),
                ci_high=float(cycles),
                unit="cycles",
                method=Method.SIMULATED,
            )
        # No energy_pj: same as evaluators/rtl — no power/energy model, omitted rather than
        # fabricated.

        violations = (
            ()
            if ok
            else (Constraint(kind="functional_mismatch", detail=f"{error_count} output mismatch(es) vs Python reference"),)
        )

        return Result(
            metrics=result_metrics,
            validity=Validity(ok=ok, violations=violations, checker_version="systemc-coarse-self-check-v0.1"),
            domain=Domain(in_domain=False, nearest_calibration=None),
            bottleneck=Bottleneck(limiter=Limiter.COMPUTE, per_level_utilisation={}),
            provenance=Provenance(
                evaluator="systemc-coarse@mac_array-v0.1",
                inputs={
                    "workload_hash": workload_hash,
                    "accelerator": arch_desc,
                    "mapping": "rtl-fixed-schedule",
                },
            ),
            escalation=Escalation(
                recommended=False,
                next_rung="rtl",
                reason=(
                    "coarse-grain pre-check only; escalate to evaluators/rtl for a cycle-accurate,"
                    " independently-simulated confirmation" if ok else None
                ),
            ),
        )

    def evaluate_batch(
        self, candidates: list[Candidate], budget: Budget, metrics: frozenset[str]
    ) -> list[Result]:
        # v0.1: sequential, same note as every other adapter here — the ABI's batch *interface*
        # is satisfied; batch *performance* is a Phase 3 concern (docs/roadmap.md).
        return [self.evaluate(c, budget, metrics) for c in candidates]

    def _ensure_binary(self) -> Path:
        """Compile mac_array_coarse.cpp once per process and cache the binary — unlike
        evaluators/rtl, shape parameters are runtime arguments, so one build serves every shape.
        """
        with self._build_lock:
            if self._binary_path is not None and self._binary_path.exists():
                return self._binary_path
            build_dir = Path(tempfile.mkdtemp(prefix="flux-systemc-build-"))
            binary_path = build_dir / "mac_array_coarse"
            build_proc = subprocess.run(
                [
                    "g++", "-std=c++17", "-O2",
                    "-o", str(binary_path),
                    str(_REFERENCE_DIR / "mac_array_coarse.cpp"),
                    "-lsystemc", "-lm",
                ],
                capture_output=True, text=True, timeout=self.timeout_s,
            )
            if build_proc.returncode != 0:
                raise RuntimeError(
                    f"SystemC build failed (exit={build_proc.returncode}).\n"
                    f"--- stdout (tail) ---\n{build_proc.stdout[-4000:]}\n"
                    f"--- stderr (tail) ---\n{build_proc.stderr[-4000:]}"
                )
            self._binary_path = binary_path
            return binary_path

    def _run_systemc(
        self, shape: dict[str, int], lanes: int, workload_hash: str
    ) -> tuple[int, bool, int]:
        binary_path = self._ensure_binary()
        with tempfile.TemporaryDirectory(prefix=f"flux-systemc-{workload_hash[:12]}-") as tmp:
            work = Path(tmp)
            i_hex, w_hex, o_hex = generate_test_vectors(shape, self.seed)
            (work / "i_mem.hex").write_text(i_hex)
            (work / "w_mem.hex").write_text(w_hex)
            (work / "expected.hex").write_text(o_hex)

            sim_proc = subprocess.run(
                [
                    str(binary_path),
                    str(shape["B"]), str(shape["C"]), str(shape["K"]), str(lanes),
                    str(work),
                ],
                capture_output=True, text=True, timeout=self.timeout_s,
            )
            match = _RESULT_RE.search(sim_proc.stdout)
            if not match:
                raise RuntimeError(
                    "Could not find a 'RESULT PASS/FAIL cycles=N' line in simulation output.\n"
                    f"--- stdout (tail) ---\n{sim_proc.stdout[-4000:]}\n"
                    f"--- stderr (tail) ---\n{sim_proc.stderr[-4000:]}"
                )
            status, cycles_str, errors_str = match.groups()
            return int(cycles_str), status == "PASS", int(errors_str) if errors_str else 0
