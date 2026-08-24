"""The ABI `evaluate` of the mac-array harness backend (D467): the check sequence and the
`Result` shape; the backend supplies its refusal wording, runner, checker version, provenance
name and escalation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

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
    NotExpressibleError,
    Provenance,
    Result,
    SequentialBatch,
    Validity,
)

from .architecture_translator import architecture_ir_to_lanes
from .workload_translator import einsum_op_to_mac_array_shape

__all__ = ["MacArrayHarness"]

#: The lanes a candidate with no Architecture IR is measured at: what the reference design is.
DEFAULT_LANES = 8


class MacArrayHarness(SequentialBatch):
    """A backend that measures one einsum op on the fixed mac-array design."""

    name: str = "mac-array"
    checker_version: str = "mac-array-self-check-v0.1"
    provenance_name: str = "mac-array"
    reference_dir: Path = Path(".")

    # ---- what a subclass supplies -------------------------------------------------------
    def _run(self, shape: dict[str, int], lanes: int,
             workload_hash: str) -> tuple[int, bool, int]:
        """(cycles, ok, mismatches) from this backend's own harness."""
        raise NotImplementedError(f"{type(self).__name__}._run")

    def _escalation(self, ok: bool) -> Escalation:
        """Whether this backend's number is worth escalating, and to what. `ok` is the run's
        verdict: only a pass is worth a costlier confirmation."""
        return Escalation(recommended=False)

    # ---- the refusals, in this backend's own words --------------------------------------
    def _refuse_workload(self) -> str:
        return f"{self.name} requires an inline Workload IR dict as Candidate.workload."

    def _refuse_mapping(self) -> str:
        return f"{self.name} does not translate Mapping IR: leave Candidate.mapping as None."

    def _refuse_no_einsum(self, workload_id: Any) -> str:
        return f"workload {workload_id!r} has no 'einsum' ops; {self.name} cannot simulate it."

    def _refuse_many_einsum(self, workload_id: Any, count: int) -> str:
        return (f"workload {workload_id!r} has {count} einsum ops; {self.name} evaluates "
                f"exactly one op per call.")

    def _refuse_arch(self) -> str:
        return (f"{self.name} only accepts Candidate.arch as None or an inline Architecture "
                f"IR dict.")

    def _refuse_ragged(self, k: int, lanes: int) -> str:
        return (f"K={k} is not a multiple of LANES={lanes}; this harness has no support for a "
                f"ragged final K-group.")

    # ---- the shared body ----------------------------------------------------------------
    def evaluate(self, candidate: Candidate, budget: Budget,
                 metrics: frozenset[str]) -> Result:
        if not isinstance(candidate.workload, dict):
            raise NotExpressibleError(self._refuse_workload())
        if candidate.mapping is not None:
            raise NotExpressibleError(self._refuse_mapping())

        einsum_ops = [op for op in candidate.workload.get("ops", [])
                      if op.get("kind") == "einsum"]
        workload_id = candidate.workload.get("id")
        if not einsum_ops:
            raise NotExpressibleError(self._refuse_no_einsum(workload_id))
        if len(einsum_ops) > 1:
            raise NotExpressibleError(self._refuse_many_einsum(workload_id, len(einsum_ops)))
        shape = einsum_op_to_mac_array_shape(einsum_ops[0])
        workload_hash = flux_ir.content_hash(candidate.workload)

        if candidate.arch is None:
            lanes, arch_desc = DEFAULT_LANES, str(self.reference_dir)
        elif isinstance(candidate.arch, dict):
            lanes = architecture_ir_to_lanes(candidate.arch)
            arch_desc = f"translated:{flux_ir.content_hash(candidate.arch)}"
        else:
            raise NotExpressibleError(self._refuse_arch())

        if shape["K"] % lanes != 0:
            raise NotExpressibleError(self._refuse_ragged(shape["K"], lanes))

        cycles, ok, mismatches = self._run(shape, lanes, workload_hash)

        result_metrics: dict[str, Estimate] = {}
        if not metrics or "latency_cycles" in metrics:
            result_metrics["latency_cycles"] = Estimate(
                value=float(cycles), ci_low=float(cycles), ci_high=float(cycles),
                unit="cycles", method=Method.SIMULATED)
        # No energy_pj: neither harness has a power model, so it is omitted, not fabricated.
        violations = () if ok else (
            Constraint(kind="functional_mismatch",
                       detail=f"{mismatches} output mismatch(es) vs Python reference"),)
        return Result(
            metrics=result_metrics,
            validity=Validity(ok=ok, violations=violations,
                              checker_version=self.checker_version),
            domain=Domain(in_domain=False, nearest_calibration=None),
            bottleneck=Bottleneck(limiter=Limiter.COMPUTE, per_level_utilisation={}),
            provenance=Provenance(
                evaluator=self.provenance_name,
                inputs={"workload_hash": workload_hash, "accelerator": arch_desc,
                        "mapping": "rtl-fixed-schedule"}),
            escalation=self._escalation(ok),
        )
