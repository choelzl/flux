"""RTL conformance checking: does a declared (fast/analytic) model's *calibrated* confidence
interval for a candidate contain a slower, more-trusted evaluator's measurement?

Built on `calibrate_result()`/`apply_escalation_policy()` rather than a tolerance comparison:
"conformant" means what a calibrated CI promises. A reference outside the CI means the declared
model or its calibration is wrong for this candidate.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from flux_evaluator_abi import Result

if TYPE_CHECKING:
    from .store import CalibrationStore


@dataclass(frozen=True, slots=True)
class MetricConformance:
    declared_value: float
    declared_ci_low: float
    declared_ci_high: float
    reference_value: float
    within_calibrated_ci: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "declared_value": self.declared_value,
            "declared_ci_low": self.declared_ci_low,
            "declared_ci_high": self.declared_ci_high,
            "reference_value": self.reference_value,
            "within_calibrated_ci": self.within_calibrated_ci,
        }


@dataclass(frozen=True, slots=True)
class ConformanceReport:
    ok: bool
    declared_result: Result
    reference_result: Result
    per_metric: dict[str, MetricConformance]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "declared_result": self.declared_result.to_dict(),
            "reference_result": self.reference_result.to_dict(),
            "per_metric": {name: m.to_dict() for name, m in self.per_metric.items()},
        }


def check_conformance(declared_result: Result, reference_result: Result) -> ConformanceReport:
    """`declared_result` must already be calibrated — its `Estimate.ci_low`/`ci_high` need to
    reflect real residual statistics (see `calibrate_result()`), not raw model output, for
    `within_calibrated_ci` to mean anything. This function does not calibrate anything itself.

    `ok` is `False` (not `None`/vacuously `True`) when there is no metric in common between the
    two results — an empty comparison is not a passing one.
    """
    per_metric: dict[str, MetricConformance] = {}
    for name, declared_estimate in declared_result.metrics.items():
        if name not in reference_result.metrics:
            continue
        reference_value = reference_result.value_of(name)
        within = declared_estimate.ci_low <= reference_value <= declared_estimate.ci_high
        per_metric[name] = MetricConformance(
            declared_value=declared_estimate.value,
            declared_ci_low=declared_estimate.ci_low,
            declared_ci_high=declared_estimate.ci_high,
            reference_value=reference_value,
            within_calibrated_ci=within,
        )
    ok = bool(per_metric) and all(m.within_calibrated_ci for m in per_metric.values())
    return ConformanceReport(
        ok=ok, declared_result=declared_result, reference_result=reference_result,
        per_metric=per_metric,
    )


def record_conformance_residuals(
    report: ConformanceReport,
    store: "CalibrationStore",
    *,
    workload_hash: str,
    raw_declared_result: Result,
    arch_hash: str | None = None,
    caveat: str | None = None,
) -> list[int]:
    """Record a conformance check's (predicted, reference) pairs in the calibration store (D98),
    so each run improves future confidence intervals for the same (evaluator, metric).

    - `predicted_value` comes from `raw_declared_result` -- the **uncalibrated** model output.
      A calibrated `value` is bias-corrected (D106); recording it would correct by the corrected
      model's residual again, a compounding loop. Required, so no call site can skip it.
    - Idempotent: a (evaluator, metric, workload_hash, arch_hash) tuple that already
      has an exact-match record is skipped, so re-running the same check doesn't multiply-weight
      one observation. Returns the ids of the records actually inserted (possibly empty).
    - A zero reference value is skipped (a relative residual against zero is undefined —
      `add_record` would raise), not silently recorded as anything else.
    """
    evaluator = report.declared_result.provenance.evaluator
    reference_source = report.reference_result.provenance.evaluator
    inserted: list[int] = []
    for metric_name, mc in report.per_metric.items():
        if mc.reference_value == 0:
            continue
        raw_estimate = raw_declared_result.metrics.get(metric_name)
        if raw_estimate is None:
            continue  # the raw result doesn't carry this metric
        if store.has_exact_match(evaluator, metric_name, workload_hash, arch_hash):
            continue
        inserted.append(store.add_record(
            workload_hash=workload_hash,
            arch_hash=arch_hash,
            evaluator=evaluator,
            metric=metric_name,
            predicted_value=raw_estimate.value,
            reference_value=mc.reference_value,
            reference_source=reference_source,
            caveat=caveat,
        ))
    return inserted
