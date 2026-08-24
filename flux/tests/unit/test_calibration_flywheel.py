"""Unit tests for the calibration flywheel (docs/decisions.md D98):
`flux_calibration.record_conformance_residuals` — every conformance check's own real
(predicted, reference) pairs feed the calibration store, so future calibrated CIs improve.
Synthetic Results throughout (this is store/aggregation logic); the same mechanism runs against
real backends via `flux_conformance_check(record_residuals=True)`.
"""

from __future__ import annotations

import pytest
from flux_calibration import (
    CalibrationStore,
    calibrate_result,
    check_conformance,
    record_conformance_residuals,
)
from flux_evaluator_abi import (
    Bottleneck,
    Domain,
    Escalation,
    Estimate,
    Limiter,
    Method,
    Provenance,
    Result,
    Validity,
)


def _result(evaluator: str, value: float, *, ci: tuple[float, float] | None = None) -> Result:
    lo, hi = ci if ci is not None else (value, value)
    return Result(
        metrics={"latency_cycles": Estimate(value=value, ci_low=lo, ci_high=hi, unit="cycles", method=Method.ANALYTIC)},
        validity=Validity(ok=True, checker_version="test"),
        domain=Domain(in_domain=True),
        bottleneck=Bottleneck(limiter=Limiter.COMPUTE),
        provenance=Provenance(evaluator=evaluator, inputs={}),
        escalation=Escalation(recommended=False),
    )


@pytest.fixture
def store(tmp_path):
    with CalibrationStore(tmp_path / "cal.db") as s:
        yield s


def test_records_the_real_residual_pair(store):
    report = check_conformance(_result("stub@1", 110.0, ci=(90.0, 130.0)), _result("ref@1", 100.0))
    inserted = record_conformance_residuals(report, store, workload_hash="w1", arch_hash="a1", raw_declared_result=report.declared_result)
    assert len(inserted) == 1
    records = store.records_for("stub@1", "latency_cycles")
    assert len(records) == 1
    assert records[0]["predicted_value"] == 110.0
    assert records[0]["reference_value"] == 100.0
    assert records[0]["relative_residual"] == pytest.approx(0.10)
    assert records[0]["reference_source"] == "ref@1"


def test_idempotent_for_the_same_workload_arch_pair(store):
    report = check_conformance(_result("stub@1", 110.0), _result("ref@1", 100.0))
    assert len(record_conformance_residuals(report, store, workload_hash="w1", arch_hash="a1", raw_declared_result=report.declared_result)) == 1
    # Re-running the identical check must not multiply-weight one observation.
    assert record_conformance_residuals(report, store, workload_hash="w1", arch_hash="a1", raw_declared_result=report.declared_result) == []
    assert len(store.records_for("stub@1", "latency_cycles")) == 1
    # A genuinely different candidate is a new observation.
    assert len(record_conformance_residuals(report, store, workload_hash="w2", arch_hash="a1", raw_declared_result=report.declared_result)) == 1


def test_zero_reference_is_skipped_not_raised(store):
    report = check_conformance(_result("stub@1", 5.0), _result("ref@1", 0.0))
    assert record_conformance_residuals(report, store, workload_hash="w1", raw_declared_result=report.declared_result) == []


def test_the_flywheel_effect_end_to_end(store):
    """The decisive test: an empty store gives a bare point estimate; one recorded conformance
    run makes the NEXT calibration of the same evaluator genuinely different — a real CI where
    there was none, and an exact-match domain for the same candidate.
    """
    raw = _result("stub@1", 110.0)

    # Before: no calibration data — calibrate_result returns the bare estimate, out of domain.
    before = calibrate_result(raw, store, workload_hash="w1", arch_hash="a1")
    est_before = before.metrics["latency_cycles"]
    assert (est_before.ci_low, est_before.ci_high) == (110.0, 110.0)
    assert before.domain.in_domain is False

    # One real conformance run, recorded: predicted 110 vs reference 100 (a real 10% residual).
    report = check_conformance(before, _result("ref@1", 100.0))
    assert len(record_conformance_residuals(report, store, workload_hash="w1", arch_hash="a1", raw_declared_result=report.declared_result)) == 1

    # After: the same calibration call now returns a genuinely widened CI that contains the
    # reference value the model missed, and the candidate is an exact calibration match.
    after = calibrate_result(raw, store, workload_hash="w1", arch_hash="a1")
    est_after = after.metrics["latency_cycles"]
    assert est_after.ci_low < 110.0 < est_after.ci_high
    assert est_after.ci_low <= 100.0  # the CI now covers the real reference value
    assert after.domain.distance == 0.0  # exact (workload, arch) match recorded
