"""flux_calibration with synthetic numbers: store CRUD, residual statistics, CI widening math.

The real cross-model version is tests/integration/test_calibration_live.py.
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


def test_calibrate_estimate_propagates_the_residual_spread_through_the_reciprocal():
    """An unbiased model keeps its value; the interval propagates `r in [-0.2, +0.2]` through
    `reference = predicted / (1 + r)`, so it is asymmetric: [83.3, 125.0] (D106)."""
    estimate = _estimate(100.0)
    stats = ResidualStats(n=3, mean_relative_residual=0.0, std_relative_residual=0.1, records_excluded_for_caveat=0)
    calibrated = calibrate_estimate(estimate, stats)
    assert calibrated.value == pytest.approx(100.0)  # mean=0 -> no bias to correct
    assert calibrated.ci_low == pytest.approx(100.0 / 1.2)
    assert calibrated.ci_high == pytest.approx(100.0 / 0.8)


def test_calibrate_estimate_corrects_a_known_systematic_bias():
    """A model measured 100% high with a tight spread is corrected, not merely fenced (D106)."""
    estimate = _estimate(100.0)
    stats = ResidualStats(n=4, mean_relative_residual=1.0, std_relative_residual=0.05, records_excluded_for_caveat=0)
    calibrated = calibrate_estimate(estimate, stats)
    assert calibrated.value == pytest.approx(50.0)                  # 100 / (1 + 1.0)
    assert calibrated.ci_low == pytest.approx(100.0 / 2.1)          # r = mean + 2*std
    assert calibrated.ci_high == pytest.approx(100.0 / 1.9)         # r = mean - 2*std
    assert calibrated.ci_high / calibrated.ci_low < 1.2             # a genuinely tight interval


def test_calibrate_estimate_below_the_trust_threshold_stays_conservative():
    """Below `_MIN_TRUSTED_N` the value is not corrected; the interval widens around the raw value."""
    estimate = _estimate(100.0)
    stats = ResidualStats(n=1, mean_relative_residual=1.0, std_relative_residual=0.0, records_excluded_for_caveat=0)
    calibrated = calibrate_estimate(estimate, stats)
    assert calibrated.value == 100.0                                # uncorrected
    # factor = 1 + Z*(|mean| + std), std floored to `_MIN_RELATIVE_SPREAD * |1 + mean|` = 0.02
    # so a zero spread does not collapse to a zero-width interval (D112)
    assert calibrated.ci_low == pytest.approx(100.0 / 3.04)
    assert calibrated.ci_high == pytest.approx(304.0)


def test_calibrate_estimate_falls_back_when_the_spread_reaches_a_degenerate_denominator():
    """A spread wide enough to reach `1 + mean - Z*std <= 0` would make the upper bound infinite
    or negative — fall back to the conservative form rather than emit nonsense."""
    estimate = _estimate(50.0)
    stats = ResidualStats(n=5, mean_relative_residual=2.0, std_relative_residual=3.0, records_excluded_for_caveat=0)
    calibrated = calibrate_estimate(estimate, stats)
    assert calibrated.value == 50.0
    assert calibrated.ci_low <= calibrated.value <= calibrated.ci_high


def test_calibrate_estimate_ci_always_contains_the_point_value():
    estimate = _estimate(50.0)
    stats = ResidualStats(n=5, mean_relative_residual=2.0, std_relative_residual=3.0, records_excluded_for_caveat=0)
    calibrated = calibrate_estimate(estimate, stats)
    assert calibrated.ci_low <= calibrated.value <= calibrated.ci_high


def test_calibrate_estimate_ci_is_never_negative_even_with_a_huge_residual():
    """The interval is multiplicative, so ci_low stays non-negative even for a large mean residual."""
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
    # three distinct points, one of them the exact match: the trust gate counts distinct
    # measured points, not rows (D171)
    store.add_record(
        workload_hash="wh", arch_hash="ah", evaluator="zigzag@1", metric="latency_cycles",
        predicted_value=100.0, reference_value=100.0, reference_source="cross_model:x",
    )
    for i in range(2):
        store.add_record(
            workload_hash="wh", arch_hash=f"other{i}", evaluator="zigzag@1", metric="latency_cycles",
            predicted_value=101.0 + i, reference_value=100.0, reference_source="cross_model:x",
        )
    result = _result("zigzag@1", {"latency_cycles": 100.0})
    calibrated = calibrate_result(result, store, workload_hash="wh", arch_hash="ah")
    assert calibrated.domain.in_domain is True
    assert calibrated.domain.distance == 0.0
    assert calibrated.provenance.calibration is not None


def test_calibrate_result_extrapolating_point_is_corrected_but_conservatively_bounded(store):
    # three distinct points, not one point recorded three times (D171)
    for i in range(3):
        store.add_record(
            workload_hash="wh-calibrated", arch_hash=f"ah{i}", evaluator="zigzag@1",
            metric="latency_cycles",
            predicted_value=110.0 + i, reference_value=100.0, reference_source="cross_model:x",
        )
    result = _result("zigzag@1", {"latency_cycles": 200.0})
    calibrated = calibrate_result(result, store, workload_hash="wh-different", arch_hash="ah")

    assert calibrated.domain.in_domain is False  # different workload_hash: not an exact match
    assert calibrated.domain.distance == 1.0  # but we do have *some* calibration data for this evaluator+metric

    # Off an exact match the value is still corrected, but the interval is the conservative
    # one and spans the raw claim too: whether the correction transfers here is unknown (D122).
    est = calibrated.metrics["latency_cycles"]
    assert est.value == pytest.approx(200.0 / 1.11, rel=1e-3)
    assert est.ci_low < est.value < est.ci_high
    assert est.ci_high > 200.0


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
    # latency_cycles is calibrated (exact match, enough records); energy_pj has no data at all
    for i in range(3):
        store.add_record(
            workload_hash="wh", arch_hash="ah", evaluator="zigzag@1", metric="latency_cycles",
            predicted_value=100.0 + i, reference_value=100.0, reference_source="cross_model:x",
        )
    result = _result("zigzag@1", {"latency_cycles": 100.0, "energy_pj": 5.0})
    calibrated = calibrate_result(result, store, workload_hash="wh", arch_hash="ah")
    # the overall domain reflects the worst metric (energy_pj), not the best
    assert calibrated.domain.in_domain is False
    assert calibrated.domain.distance == float("inf")


# --- correction vs. confidence in the correction (D122) ---


def _pool(store, *, workload_hash: str, n: int = 3, predicted: float = 300.0,
          reference: float = 100.0) -> None:
    """A pool with essentially zero measured spread: agreement that holds only for points like these."""
    for i in range(n):
        store.add_record(
            workload_hash=workload_hash, arch_hash=f"ah{i}", evaluator="zigzag@1",
            metric="latency_cycles", predicted_value=predicted, reference_value=reference,
            reference_source="cross_model:x",
        )


def test_a_degenerate_pool_spread_does_not_become_confidence_at_an_unmeasured_point(store):
    """Extrapolating a zero-spread pool does not inherit its agreement as certainty."""
    _pool(store, workload_hash="wh-calibrated")
    result = _result("zigzag@1", {"latency_cycles": 300.0})

    extrapolated = calibrate_result(result, store, workload_hash="wh-unseen", arch_hash="ah-unseen")
    est = extrapolated.metrics["latency_cycles"]

    assert extrapolated.domain.in_domain is False
    assert est.value == pytest.approx(100.0, rel=1e-6)          # still corrected
    # ...but the interval admits the correction may not apply here (a 2% band would be ~98-102)
    assert est.ci_high / est.ci_low > 5.0


def test_the_same_pool_at_a_measured_point_keeps_its_tight_interval(store):
    """An exact match keeps the tight corrected interval: it is measured evidence about this point (D106)."""
    _pool(store, workload_hash="wh-calibrated")
    store.add_record(
        workload_hash="wh-here", arch_hash="ah-here", evaluator="zigzag@1",
        metric="latency_cycles", predicted_value=300.0, reference_value=100.0,
        reference_source="cross_model:x",
    )
    result = _result("zigzag@1", {"latency_cycles": 300.0})

    measured = calibrate_result(result, store, workload_hash="wh-here", arch_hash="ah-here")
    est = measured.metrics["latency_cycles"]

    assert measured.domain.in_domain is True
    assert est.value == pytest.approx(100.0, rel=1e-6)
    assert est.ci_high / est.ci_low < 1.1        # tight, because it was earned here


def test_an_extrapolated_interval_always_contains_its_own_point_estimate(store):
    """The interval is the union with the raw band, so it never excludes the corrected value."""
    _pool(store, workload_hash="wh-calibrated", predicted=1000.0, reference=100.0)
    result = _result("zigzag@1", {"latency_cycles": 1000.0})

    est = calibrate_result(
        result, store, workload_hash="wh-unseen", arch_hash="ah-unseen"
    ).metrics["latency_cycles"]

    assert est.ci_low <= est.value <= est.ci_high


def test_a_measurement_where_none_existed_narrows_the_interval_by_evidence(store):
    """Measuring a candidate with no reference collapses its wide interval (D130, D134)."""
    for i, (p, r) in enumerate([(1554.0, 529.0), (3106.0, 1058.0), (778.0, 265.0)]):
        store.add_record(workload_hash="wh", arch_hash=f"other-{i}", evaluator="zigzag@1",
                         metric="latency_cycles", predicted_value=p, reference_value=r,
                         reference_source="rtl_sim")
    result = _result("zigzag@1", {"latency_cycles": 1166.0})

    before = calibrate_result(result, store, workload_hash="wh", arch_hash="ah").metrics["latency_cycles"]
    assert before.ci_high / before.ci_low > 20         # no reference for this candidate

    store.add_record(workload_hash="wh", arch_hash="ah", evaluator="zigzag@1",
                     metric="latency_cycles", predicted_value=1166.0, reference_value=397.0,
                     reference_source="generated_rtl@gemm-wrapper-v0.1")
    after = calibrate_result(result, store, workload_hash="wh", arch_hash="ah").metrics["latency_cycles"]

    assert after.ci_high / after.ci_low < 1.1          # ...and now it is measured
    assert after.ci_low <= 397.0 <= after.ci_high
    # the interval collapses; the corrected value barely moves -- pinned loosely on purpose,
    # since the exact shift depends on pool size
    assert before.value == pytest.approx(after.value, rel=1e-3)


def test_the_result_level_domain_reports_the_least_calibrated_metric(store):
    """`validated_here` is per metric, but `Result.domain` is the worst across metrics, so an
    RTL-referenced result stays out of domain because energy_pj is never calibrated (D135)."""
    from flux_calibration.calibrate import _domain_for

    store.add_record(workload_hash="wh", arch_hash="ah", evaluator="zigzag@1",
                     metric="latency_cycles", predicted_value=1166.0, reference_value=397.0,
                     reference_source="generated_rtl@gemm-wrapper-v0.1")
    for i in range(2):
        store.add_record(workload_hash="wh", arch_hash=f"o{i}", evaluator="zigzag@1",
                         metric="latency_cycles", predicted_value=1554.0, reference_value=529.0,
                         reference_source="rtl_sim")

    assert _domain_for(store, "zigzag@1", "latency_cycles", "wh", "ah").in_domain is True
    assert _domain_for(store, "zigzag@1", "energy_pj", "wh", "ah").in_domain is False

    both = _result("zigzag@1", {"latency_cycles": 1166.0, "energy_pj": 5.0e5})
    calibrated = calibrate_result(both, store, workload_hash="wh", arch_hash="ah")
    assert calibrated.domain.in_domain is False                      # the worst metric wins
    assert calibrated.metrics["latency_cycles"].ci_high / calibrated.metrics["latency_cycles"].ci_low < 1.1


def test_each_metric_carries_its_own_domain_alongside_the_aggregate(store):
    """Per-metric domains report a directly measured latency as in domain while the aggregate stays False (D140)."""
    store.add_record(workload_hash="wh", arch_hash="ah", evaluator="zigzag@1",
                     metric="latency_cycles", predicted_value=1166.0, reference_value=397.0,
                     reference_source="generated_rtl@gemm-wrapper-v0.1")
    for i in range(2):
        store.add_record(workload_hash="wh", arch_hash=f"o{i}", evaluator="zigzag@1",
                         metric="latency_cycles", predicted_value=1554.0, reference_value=529.0,
                         reference_source="rtl_sim")

    result = _result("zigzag@1", {"latency_cycles": 1166.0, "energy_pj": 5.0e5})
    calibrated = calibrate_result(result, store, workload_hash="wh", arch_hash="ah")

    assert calibrated.domain.in_domain is False                          # the aggregate, unchanged
    assert calibrated.metric_domains["latency_cycles"].in_domain is True  # ...and the real story
    assert calibrated.metric_domains["energy_pj"].in_domain is False
    assert calibrated.to_dict()["metric_domains"]["latency_cycles"]["in_domain"] is True


def test_an_uncalibrated_result_has_no_metric_domains(store):
    """A result that never went through `calibrate_result` has empty per-metric domains."""
    assert _result("zigzag@1", {"latency_cycles": 100.0}).metric_domains == {}


def test_repeating_one_point_does_not_buy_trust(store):
    """One point recorded three times does not satisfy `_MIN_TRUSTED_N` (D171)."""
    for _ in range(3):
        store.add_record(
            workload_hash="wh", arch_hash="ah", evaluator="zigzag@1", metric="latency_cycles",
            predicted_value=210.0, reference_value=529.0, reference_source="generated-rtl",
        )
    stats = store.residual_stats("zigzag@1", "latency_cycles")

    assert stats.n == 3, "rows are still counted for mean/std"
    assert stats.distinct_points == 1, "but they cover one measured point"

    result = _result("zigzag@1", {"latency_cycles": 210.0})
    calibrated = calibrate_result(result, store, workload_hash="wh", arch_hash="ah")
    est = calibrated.metrics["latency_cycles"]

    assert est.value == 210.0, "not corrected: one point is not a trusted pool"
    assert calibrated.domain.in_domain is False


def test_three_distinct_points_do_buy_trust(store):
    """The same three rows spread across three distinct points still cross the gate (D171)."""
    for i in range(3):
        store.add_record(
            workload_hash="wh", arch_hash=f"ah{i}", evaluator="zigzag@1", metric="latency_cycles",
            predicted_value=210.0, reference_value=529.0, reference_source="generated-rtl",
        )
    stats = store.residual_stats("zigzag@1", "latency_cycles")

    assert stats.n == 3 and stats.distinct_points == 3

    result = _result("zigzag@1", {"latency_cycles": 210.0})
    calibrated = calibrate_result(result, store, workload_hash="wh", arch_hash="ah0")
    assert calibrated.metrics["latency_cycles"].value != 210.0
    assert calibrated.domain.in_domain is True


def test_hand_built_stats_without_distinct_points_fall_back_to_n(store):
    """Hand-built `ResidualStats` without a point count are gated on `n`, not treated as untrusted."""
    from flux_calibration.store import ResidualStats

    stats = ResidualStats(
        n=3, mean_relative_residual=0.1, std_relative_residual=0.05, records_excluded_for_caveat=0,
    )
    assert stats.distinct_points is None

    est = Estimate(value=100.0, ci_low=100.0, ci_high=100.0, unit="cycles", method=Method.ANALYTIC)
    assert calibrate_estimate(est, stats).value != 100.0  # corrected
