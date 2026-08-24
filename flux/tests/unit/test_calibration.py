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


def test_calibrate_estimate_propagates_the_residual_spread_through_the_reciprocal():
    """UPDATED EXPECTATION (docs/decisions.md D106). With an unbiased model (mean=0) the point
    value is unchanged, but the interval is now the honest propagation of `r in [-0.2, +0.2]`
    through `reference = predicted / (1 + r)` — `[100/1.2, 100/0.8]` = `[83.3, 125.0]`, not the
    old symmetric `[83.3, 120.0]`. The old form multiplied and divided by the same factor, which
    is symmetric in the wrong space: a residual band is symmetric in `r`, and `1/(1+r)` is not
    symmetric in `r`. 125.0 is what the stated residual actually implies.
    """
    estimate = _estimate(100.0)
    stats = ResidualStats(n=3, mean_relative_residual=0.0, std_relative_residual=0.1, records_excluded_for_caveat=0)
    calibrated = calibrate_estimate(estimate, stats)
    assert calibrated.value == pytest.approx(100.0)  # mean=0 -> no bias to correct
    assert calibrated.ci_low == pytest.approx(100.0 / 1.2)
    assert calibrated.ci_high == pytest.approx(100.0 / 0.8)


def test_calibrate_estimate_corrects_a_known_systematic_bias():
    """The core of D106: a model measured to run 100% high with a tight spread is *corrected*,
    not merely fenced. Before D106 this returned value=100 with a ~[33, 300] interval — treating
    a fully-characterised 2x overestimate as if it were uncertainty."""
    estimate = _estimate(100.0)
    stats = ResidualStats(n=4, mean_relative_residual=1.0, std_relative_residual=0.05, records_excluded_for_caveat=0)
    calibrated = calibrate_estimate(estimate, stats)
    assert calibrated.value == pytest.approx(50.0)                  # 100 / (1 + 1.0)
    assert calibrated.ci_low == pytest.approx(100.0 / 2.1)          # r = mean + 2*std
    assert calibrated.ci_high == pytest.approx(100.0 / 1.9)         # r = mean - 2*std
    assert calibrated.ci_high / calibrated.ci_low < 1.2             # a genuinely tight interval


def test_calibrate_estimate_below_the_trust_threshold_stays_conservative():
    """One or two residuals do not establish that a correction generalises — D101 measured that
    failure directly — so below `_MIN_TRUSTED_N` the pre-D106 widen-around-the-raw-value
    behaviour is kept deliberately."""
    estimate = _estimate(100.0)
    stats = ResidualStats(n=1, mean_relative_residual=1.0, std_relative_residual=0.0, records_excluded_for_caveat=0)
    calibrated = calibrate_estimate(estimate, stats)
    assert calibrated.value == 100.0                                # uncorrected
    # factor = 1 + Z*(|mean| + std), with std floored to `_MIN_RELATIVE_SPREAD * |1 + mean|`
    # = 0.02 (docs/decisions.md D112): a measured-zero spread is not evidence of certainty on
    # this path either, and with mean == std == 0 the conservative form would otherwise also
    # collapse to a zero-width interval.
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
    # UPDATED FIXTURE (docs/decisions.md D171). This used to record the SAME (wh, ah) point three
    # times with three different predicted values — which no deterministic evaluator can produce,
    # since its prediction is a function of (workload, arch, evaluator). The trust gate now counts
    # distinct measured points rather than rows, so "enough records" is expressed the only way it
    # can honestly occur: three different points, one of which is the exact match being queried.
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
    # Three distinct points, not one point recorded three times (docs/decisions.md D171) — see
    # the fixture note on test_calibrate_result_exact_match_with_enough_records_is_in_domain.
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

    # UPDATED EXPECTATION AGAIN (docs/decisions.md D122, superseding this test's own D106 note).
    # D106 had it assert the interval EXCLUDES the raw 200.0 — a narrow band around the corrected
    # value, on a point the store has never measured. A real held-out run then showed what that
    # costs: 86.6 in [84.9, 88.4] when the truth was 128.0. The correction and the *confidence in
    # the correction here* are different claims, and only the first survives extrapolation.
    #
    # So: still corrected (the point estimate is the best available guess at the bias), but the
    # interval is the conservative one, which necessarily spans the raw claim too — because
    # whether the correction transfers to this point is exactly what is unknown.
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


# --- Correction vs. confidence in the correction (docs/decisions.md D122) ---


def _pool(store, *, workload_hash: str, n: int = 3, predicted: float = 300.0,
          reference: float = 100.0) -> None:
    """A pool with essentially zero measured spread — the case that caused the failure: three
    architectures whose residuals agree, which reads as "this bias is precisely known" when it
    only ever meant "precisely known for points like these"."""
    for i in range(n):
        store.add_record(
            workload_hash=workload_hash, arch_hash=f"ah{i}", evaluator="zigzag@1",
            metric="latency_cycles", predicted_value=predicted, reference_value=reference,
            reference_source="cross_model:x",
        )


def test_a_degenerate_pool_spread_does_not_become_confidence_at_an_unmeasured_point(store):
    """The regression this fixes, in miniature. A zero-spread pool plus the 1% floor gives a ~2%
    interval; applied to a point in a different residual regime, that interval is confidently
    wrong. Extrapolation must not inherit the pool's internal agreement as certainty."""
    _pool(store, workload_hash="wh-calibrated")
    result = _result("zigzag@1", {"latency_cycles": 300.0})

    extrapolated = calibrate_result(result, store, workload_hash="wh-unseen", arch_hash="ah-unseen")
    est = extrapolated.metrics["latency_cycles"]

    assert extrapolated.domain.in_domain is False
    assert est.value == pytest.approx(100.0, rel=1e-6)          # still corrected
    # ...but the interval is wide enough to admit that the correction may not apply here. A 2%
    # band would be about 98-102; the conservative one spans an order of magnitude.
    assert est.ci_high / est.ci_low > 5.0


def test_the_same_pool_at_a_measured_point_keeps_its_tight_interval(store):
    """The other half of the contract: D106's win is preserved exactly where it was earned. An
    exact match is measured evidence about this point, so the tight interval is a real claim."""
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
    """A conservative band around the *raw* value can sit entirely above a strongly-corrected
    value — 3x correction, 1.2x band. Taking the union rather than the raw band keeps the interval
    from excluding the very number it is attached to."""
    _pool(store, workload_hash="wh-calibrated", predicted=1000.0, reference=100.0)
    result = _result("zigzag@1", {"latency_cycles": 1000.0})

    est = calibrate_result(
        result, store, workload_hash="wh-unseen", arch_hash="ah-unseen"
    ).metrics["latency_cycles"]

    assert est.ci_low <= est.value <= est.ci_high


def test_a_measurement_where_none_existed_narrows_the_interval_by_evidence(store):
    """The point of extending the reference frontier (docs/decisions.md D130/D134/D135): a
    candidate with no reference gets an honestly wide interval, and *measuring* it — not assuming
    anything — collapses that interval. Measured end to end at 24.32x -> 1.04x; the arithmetic is
    reproduced here without the evaluators."""
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
    # The *interval* collapses; the corrected value barely moves (~0.005% here) — the new record
    # agrees with the pool about the bias, and adds evidence about *this point* rather than
    # changing what the bias is. Pinned loosely on purpose: the exact shift depends on pool size,
    # and asserting it to 1e-6 would be measuring the arithmetic, not the property.
    assert before.value == pytest.approx(after.value, rel=1e-3)


def test_the_result_level_domain_reports_the_least_calibrated_metric(store):
    """A subtlety worth pinning (docs/decisions.md D135), because the interval and the domain flag
    can tell different stories about the same result.

    `validated_here` is decided *per metric*, so a latency with an exact match gets the tight
    corrected interval. `Result.domain` is deliberately the *worst* domain across metrics. Since
    neither the RTL reference nor a generated design produces `energy_pj`, any RTL-referenced
    candidate keeps an uncalibrated energy metric forever — so the result reports
    `in_domain=False` even when the metric a caller asked about is fully validated, and escalation
    keeps firing on that basis. Conservative and defensible; just not readable as a statement
    about the metric you care about.
    """
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
    """docs/decisions.md D140. The aggregate `domain` stays the worst across metrics — a result is
    only as calibrated as its least-calibrated number — but that cannot be read as a statement
    about any one metric. A latency measured directly reports `in_domain=True` on its own domain
    while the result still reports False, because nothing ever calibrates `energy_pj` (neither the
    RTL reference nor a generated design can produce it)."""
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
    """Empty rather than fabricated: a result that never went through `calibrate_result` has no
    per-metric domains to report, and every existing constructor keeps working untouched."""
    assert _result("zigzag@1", {"latency_cycles": 100.0}).metric_domains == {}


def test_repeating_one_point_does_not_buy_trust(store):
    """The same (workload, arch) point recorded three times used to satisfy `_MIN_TRUSTED_N` and
    switch calibration into its correcting regime — measured before the fix (docs/decisions.md
    D171): the interval collapsed from width 370.1 to 21.2 and the corrected value moved
    210.0 -> 529.0, on one real measurement repeated. Reachable by calling
    `flux_calibrate_against_generated_rtl` three times on the same candidate, which measures the
    identical value each time because the derived design is deterministic (D117).
    """
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
    """The control for the test above: the same three rows, spread across three real points, must
    still cross the gate — otherwise the fix would have broken calibration rather than tightened
    it."""
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
    """`ResidualStats` built by hand (as several tests here do) can't know the point count; the
    gate must keep reading `n` for those rather than silently treating them as untrusted."""
    from flux_calibration.store import ResidualStats

    stats = ResidualStats(
        n=3, mean_relative_residual=0.1, std_relative_residual=0.05, records_excluded_for_caveat=0,
    )
    assert stats.distinct_points is None

    est = Estimate(value=100.0, ci_low=100.0, ci_high=100.0, unit="cycles", method=Method.ANALYTIC)
    assert calibrate_estimate(est, stats).value != 100.0  # corrected: the pre-D171 behaviour
