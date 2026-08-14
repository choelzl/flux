"""`flux_calibrate_against_generated_rtl` — record a generated design's measurement as a real
calibration reference, but only where no reference already exists (docs/decisions.md D136).

The sequence this closes: D121 made a generated design measure what `evaluators/rtl` measures;
D125 then *measured* that feeding that back adds nothing — the residual is identical either way
(`+1.937618`), so recording it would duplicate evidence already in the store. D130 made ragged
K-groups generable, D134 measured one the reference refuses outright, and D135 showed such a
measurement collapsing a candidate's interval from **24.32x to 1.04x**. That is the case this node
exists for, and the guard below is what keeps it to that case.

**Why refusing the redundant case is a correctness matter, not tidiness.** A residual recorded
twice is counted twice: `n` grows, the spread does not, and the interval narrows on evidence that
was never independent. Silently double-counting is precisely the kind of statistical error this
repo keeps finding the hard way, so the redundant path is refused by name rather than merged.
"""

from __future__ import annotations

from flux_llm import default_local_model
from dataclasses import dataclass
from typing import Any

from chia.base.ChiaFunction import ChiaFunction
from flux_calibration import CalibrationStore, calibrate_result
from flux_cli.registry import make_evaluator
from flux_evaluator_abi import Budget, Candidate

from .generate_sequential_rtl import GemmRtlReport, flux_generate_gemm_rtl_for_architecture

_GENERATED_SOURCE = "generated_rtl@gemm-wrapper-v0.1"


@dataclass(frozen=True, slots=True)
class GeneratedReferenceReport:
    """Every step reported separately, so a caller can see why nothing was recorded when nothing
    was — the same no-opaque-ok-flag rule the other generation nodes follow."""

    generation: GemmRtlReport
    declared_value: float | None          # the fast model's raw, uncalibrated number
    measured_value: float | None          # the generated design's real measured cycle count
    recorded: bool
    skip_reason: str | None = None
    ci_width_before: float | None = None  # ci_high / ci_low, before this record existed
    ci_width_after: float | None = None   # ...and after

    def to_dict(self) -> dict[str, Any]:
        return {
            "generation": self.generation.to_dict(),
            "declared_value": self.declared_value,
            "measured_value": self.measured_value,
            "recorded": self.recorded,
            "skip_reason": self.skip_reason,
            "ci_width_before": self.ci_width_before,
            "ci_width_after": self.ci_width_after,
            "reference_source": _GENERATED_SOURCE,
        }


def _reference_can_express(workload: dict[str, Any], arch: dict[str, Any]) -> bool:
    """Does `evaluators/rtl` already measure this candidate? Asked by *running* its own
    translators rather than by re-deriving their rules here — a second copy of "K must divide
    LANES" would be one more thing to drift (docs/decisions.md D129's lesson about duplicated
    facts)."""
    from flux_evaluator_rtl import NotExpressibleError

    try:
        make_evaluator("rtl").evaluate(
            Candidate(workload=workload, arch=arch, mapping=None),
            Budget(), frozenset({"latency_cycles"}),
        )
        return True
    except NotExpressibleError:
        return False


def _ci_width(result, metric: str) -> float | None:
    est = result.metrics.get(metric)
    if est is None or not est.ci_low:
        return None
    return est.ci_high / est.ci_low


@ChiaFunction()
def flux_calibrate_against_generated_rtl(
    workload: dict[str, Any],
    arch: dict[str, Any],
    calibration_db_path: str,
    *,
    backend: str = "zigzag",
    metric: str = "latency_cycles",
    model: str = default_local_model(),
    allow_redundant: bool = False,
) -> GeneratedReferenceReport:
    """Generate a design for this candidate, measure it, and record the residual against
    `backend`'s own prediction — narrowing that candidate's calibrated interval by evidence.

    Refuses by default when `evaluators/rtl` can already measure the candidate: that residual is
    already obtainable, and recording it again would double-count it (`allow_redundant=True`
    overrides, for a caller who has a reason).

    Records nothing unless the generated design both verified *and* measured its predicted cycle
    count — an unverified design is not a reference, and a right answer at the wrong latency is
    exactly the failure this repo's harness reports separately (D118).
    """
    if not allow_redundant and _reference_can_express(workload, arch):
        return GeneratedReferenceReport(
            generation=None, declared_value=None, measured_value=None, recorded=False,
            skip_reason=(
                "evaluators/rtl already measures this candidate, so the residual is already "
                "obtainable; recording it again would count the same evidence twice "
                "(docs/decisions.md D125/D136). Pass allow_redundant=True to override."
            ),
        )

    report = flux_generate_gemm_rtl_for_architecture(workload, arch, model=model)
    if not report.success:
        return GeneratedReferenceReport(
            generation=report, declared_value=None, measured_value=None, recorded=False,
            skip_reason=(
                "the generated design did not both verify and measure its predicted latency; "
                "an unverified design is not a reference"
            ),
        )

    declared = make_evaluator(backend).evaluate(
        Candidate(workload=workload, arch=arch, mapping=None), Budget(), frozenset({metric}),
    )
    if metric not in declared.metrics:
        return GeneratedReferenceReport(
            generation=report, declared_value=None,
            measured_value=float(report.measured_cycles), recorded=False,
            skip_reason=f"backend {backend!r} does not emit {metric!r}; it returned "
                        f"{sorted(declared.metrics)}",
        )

    import flux_ir

    workload_hash, arch_hash = flux_ir.content_hash(workload), flux_ir.content_hash(arch)
    measured = float(report.measured_cycles)
    # The RAW declared value, never a calibrated one — D106 measured what recording corrected
    # values into the pool that produced the correction does to an interval.
    predicted = declared.value_of(metric)

    with CalibrationStore(calibration_db_path) as store:
        # `calibration/conformance.py` has always guarded its own `add_record` with this; this
        # node never did (docs/decisions.md D171). The derived design is deterministic (D117), so
        # re-running this node on the same candidate measures the identical value — a second row
        # carrying no new information. Recorded anyway, it inflated the pool the trust gate reads.
        if store.has_exact_match(declared.provenance.evaluator, metric, workload_hash, arch_hash):
            return GeneratedReferenceReport(
                generation=report, declared_value=declared.value_of(metric),
                measured_value=measured, recorded=False,
                skip_reason=(
                    f"this exact (workload, architecture) point is already calibrated for "
                    f"{declared.provenance.evaluator!r}/{metric!r} — re-recording a deterministic "
                    "measurement would add a duplicate row, not evidence"
                ),
            )
        before = _ci_width(
            calibrate_result(declared, store, workload_hash=workload_hash, arch_hash=arch_hash),
            metric,
        )
        store.add_record(
            workload_hash=workload_hash, arch_hash=arch_hash,
            evaluator=declared.provenance.evaluator, metric=metric,
            predicted_value=predicted, reference_value=measured,
            reference_source=_GENERATED_SOURCE,
        )
        after = _ci_width(
            calibrate_result(declared, store, workload_hash=workload_hash, arch_hash=arch_hash),
            metric,
        )

    return GeneratedReferenceReport(
        generation=report, declared_value=predicted, measured_value=measured, recorded=True,
        ci_width_before=before, ci_width_after=after,
    )
