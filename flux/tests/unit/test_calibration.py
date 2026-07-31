"""Unit tests for flux_calibration: store CRUD, residual statistics, and CI widening math — all
with synthetic numbers, no real evaluator involved. See tests/integration/test_calibration_live.py
for the real cross-model version (actual ZigZag vs Timeloop residuals).
"""

from __future__ import annotations

import pytest
from flux_calibration import CalibrationStore, ResidualStats, calibrate_estimate, calibrate_result
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


@pytest.fixture
def store(tmp_path):
    with CalibrationStore(tmp_path / "cal.db") as s:
        yield s


def _estimate(value: float) -> Estimate:
    return Estimate(value=value, ci_low=value, ci_high=value, unit="cycles", method=Method.ANALYTIC)


def _result(evaluator: str, metrics: dict[str, float]) -> Result:
    return Result(
        metrics={name: _estimate(value) for name, value in metrics.items()},
        validity=Validity(ok=True, checker_version="test"),
        domain=Domain(in_domain=False),
        bottleneck=Bottleneck(limiter=Limiter.COMPUTE),
        provenance=Provenance(evaluator=evaluator, inputs={}),
        escalation=Escalation(recommended=False),
    )


# --- store ---------------------------------------------------------------------------------


def test_add_record_computes_relative_residual(store):
    record_id = store.add_record(
        workload_hash="wh", arch_hash="ah", evaluator="zigzag@1", metric="latency_cycles",
        predicted_value=150.0, reference_value=100.0, reference_source="cross_model:timeloop@1",
    )
    records = store.records_for("zigzag@1", "latency_cycles")
    assert len(records) == 1
    assert records[0]["id"] == record_id
    assert records[0]["relative_residual"] == pytest.approx(0.5)  # (150-100)/100


def test_add_record_rejects_zero_reference(store):
    with pytest.raises(ValueError, match="non-zero"):
        store.add_record(
            workload_hash="wh", arch_hash="ah", evaluator="zigzag@1", metric="m",
            predicted_value=1.0, reference_value=0.0, reference_source="x",
        )


def test_residual_stats_none_when_no_records(store):
    assert store.residual_stats("zigzag@1", "latency_cycles") is None


def test_residual_stats_mean_and_std(store):
    for predicted, reference in [(110.0, 100.0), (90.0, 100.0), (130.0, 100.0)]:
        store.add_record(
            workload_hash="wh", arch_hash="ah", evaluator="zigzag@1", metric="latency_cycles",
            predicted_value=predicted, reference_value=reference, reference_source="cross_model:x",
        )
    stats = store.residual_stats("zigzag@1", "latency_cycles")
    assert stats.n == 3
    # residuals: 0.1, -0.1, 0.3 -> mean = 0.1
    assert stats.mean_relative_residual == pytest.approx(0.1)
    assert stats.std_relative_residual > 0


def test_caveated_records_excluded_by_default(store):
    store.add_record(
        workload_hash="wh", arch_hash="ah", evaluator="zigzag@1", metric="energy_pj",
        predicted_value=1.0, reference_value=2.0, reference_source="cross_model:x",
        caveat="known bad data",
    )
    assert store.residual_stats("zigzag@1", "energy_pj") is None
    assert store.residual_stats("zigzag@1", "energy_pj", exclude_caveated=False).n == 1


def test_has_exact_match(store):
    store.add_record(
        workload_hash="wh1", arch_hash="ah1", evaluator="zigzag@1", metric="latency_cycles",
        predicted_value=1.0, reference_value=1.0, reference_source="cross_model:x",
    )
    assert store.has_exact_match("zigzag@1", "latency_cycles", "wh1", "ah1")
    assert not store.has_exact_match("zigzag@1", "latency_cycles", "wh2", "ah1")
    assert not store.has_exact_match("zigzag@1", "latency_cycles", "wh1", "ah2")


# --- calibrate_estimate ----------------------------------------------------------------------


def test_calibrate_estimate_unchanged_when_no_stats():
    estimate = _estimate(100.0)
    assert calibrate_estimate(estimate, None) == estimate


def test_calibrate_estimate_widens_ci_multiplicatively():
    estimate = _estimate(100.0)
    stats = ResidualStats(n=3, mean_relative_residual=0.0, std_relative_residual=0.1, records_excluded_for_caveat=0)
    calibrated = calibrate_estimate(estimate, stats)
    # factor = 1 + 2.0 * (0 + 0.1) = 1.2
    assert calibrated.value == 100.0
    assert calibrated.ci_low == pytest.approx(100.0 / 1.2)
    assert calibrated.ci_high == pytest.approx(120.0)


def test_calibrate_estimate_ci_always_contains_the_point_value():
    estimate = _estimate(50.0)
    stats = ResidualStats(n=5, mean_relative_residual=2.0, std_relative_residual=3.0, records_excluded_for_caveat=0)
    calibrated = calibrate_estimate(estimate, stats)
    assert calibrated.ci_low <= calibrated.value <= calibrated.ci_high


def test_calibrate_estimate_ci_is_never_negative_even_with_a_huge_residual():
    """Regression test: an early version of this formula was additive (value ± half_width) and
    produced a negative ci_low for a cycle count the first time it was run against real data —
    a ~204% mean relative residual made half_width larger than the value itself. Metrics here
    (latency, energy, area) are all strictly non-negative and only ever scale, never shift."""
    estimate = _estimate(263.0)
    stats = ResidualStats(n=3, mean_relative_residual=2.0358, std_relative_residual=0.003, records_excluded_for_caveat=0)
    calibrated = calibrate_estimate(estimate, stats)
    assert calibrated.ci_low > 0


def test_calibrate_estimate_unchanged_for_zero_value():
    estimate = _estimate(0.0)
    stats = ResidualStats(n=3, mean_relative_residual=0.5, std_relative_residual=0.1, records_excluded_for_caveat=0)
    assert calibrate_estimate(estimate, stats) == estimate


# --- calibrate_result ------------------------------------------------------------------------


def test_calibrate_result_no_calibration_data_leaves_estimates_as_point_values(store):
    result = _result("zigzag@1", {"latency_cycles": 100.0})
    calibrated = calibrate_result(result, store, workload_hash="wh", arch_hash="ah")
    assert calibrated.metrics["latency_cycles"].ci_low == 100.0
    assert calibrated.metrics["latency_cycles"].ci_high == 100.0
    assert calibrated.domain.in_domain is False
    assert calibrated.domain.distance == float("inf")


def test_calibrate_result_exact_match_with_enough_records_is_in_domain(store):
    for i in range(3):
        store.add_record(
            workload_hash="wh", arch_hash="ah", evaluator="zigzag@1", metric="latency_cycles",
            predicted_value=100.0 + i, reference_value=100.0, reference_source="cross_model:x",
        )
    result = _result("zigzag@1", {"latency_cycles": 100.0})
    calibrated = calibrate_result(result, store, workload_hash="wh", arch_hash="ah")
    assert calibrated.domain.in_domain is True
    assert calibrated.domain.distance == 0.0
    assert calibrated.provenance.calibration is not None


def test_calibrate_result_extrapolating_point_is_not_in_domain_but_still_widened(store):
    for i in range(3):
        store.add_record(
            workload_hash="wh-calibrated", arch_hash="ah", evaluator="zigzag@1", metric="latency_cycles",
            predicted_value=110.0 + i, reference_value=100.0, reference_source="cross_model:x",
        )
    result = _result("zigzag@1", {"latency_cycles": 200.0})
    calibrated = calibrate_result(result, store, workload_hash="wh-different", arch_hash="ah")

    assert calibrated.domain.in_domain is False  # different workload_hash: not an exact match
    assert calibrated.domain.distance == 1.0  # but we do have *some* calibration data for this evaluator+metric
    assert calibrated.metrics["latency_cycles"].ci_low < 200.0 < calibrated.metrics["latency_cycles"].ci_high


def test_calibrate_result_does_not_mutate_the_original_result(store):
    store.add_record(
        workload_hash="wh", arch_hash="ah", evaluator="zigzag@1", metric="latency_cycles",
        predicted_value=110.0, reference_value=100.0, reference_source="cross_model:x",
    )
    original = _result("zigzag@1", {"latency_cycles": 100.0})
    original_dict = original.to_dict()
    calibrate_result(original, store, workload_hash="wh", arch_hash="ah")
    assert original.to_dict() == original_dict


def test_calibrate_result_uses_worst_domain_across_metrics(store):
    # latency_cycles is calibrated (exact match, enough records); energy_pj has no data at all.
    for i in range(3):
        store.add_record(
            workload_hash="wh", arch_hash="ah", evaluator="zigzag@1", metric="latency_cycles",
            predicted_value=100.0 + i, reference_value=100.0, reference_source="cross_model:x",
        )
    result = _result("zigzag@1", {"latency_cycles": 100.0, "energy_pj": 5.0})
    calibrated = calibrate_result(result, store, workload_hash="wh", arch_hash="ah")
    # Overall domain must reflect the WORST metric (energy_pj: no data at all), not the best.
    assert calibrated.domain.in_domain is False
    assert calibrated.domain.distance == float("inf")
