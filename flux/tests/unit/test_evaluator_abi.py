"""The measurement record types (docs/measurement.md): a Result round-trips exactly."""

from __future__ import annotations

import pytest
from flux_store.result import Bottleneck, Domain, Escalation, Estimate, Limiter, Method, Provenance, Result, Validity


def _sample_result() -> Result:
    return Result(
        metrics={
            "latency_cycles": Estimate(
                value=1000, ci_low=900, ci_high=1100, unit="cycles", method=Method.ANALYTIC
            )
        },
        validity=Validity(ok=True, checker_version="0.1.0"),
        domain=Domain(in_domain=True, distance=0.02, nearest_calibration="cal-2026-07-a"),
        bottleneck=Bottleneck(limiter=Limiter.MEMORY, per_level_utilisation={"gbuf": 0.87}),
        provenance=Provenance(
            evaluator="flux-native@0.1.0", inputs={"workload_hash": "abc", "arch_hash": "def"}
        ),
        escalation=Escalation(recommended=False),
    )


def test_estimate_rejects_value_outside_its_own_confidence_interval():
    with pytest.raises(ValueError):
        Estimate(value=5, ci_low=10, ci_high=20, unit="x", method=Method.ANALYTIC)


def test_estimate_accepts_value_on_ci_boundary():
    Estimate(value=10, ci_low=10, ci_high=20, unit="x", method=Method.ANALYTIC)
    Estimate(value=20, ci_low=10, ci_high=20, unit="x", method=Method.ANALYTIC)


def test_result_to_dict_round_trips_enum_values_as_plain_strings():
    d = _sample_result().to_dict()
    assert d["metrics"]["latency_cycles"]["method"] == "analytic"
    assert d["bottleneck"]["limiter"] == "memory"
    assert d["validity"]["ok"] is True
    assert d["domain"]["in_domain"] is True
    assert d["escalation"]["recommended"] is False


def test_result_from_dict_is_the_exact_inverse_of_to_dict():
    """`Result.from_dict(r.to_dict()) == r`, which the store's caching relies on."""
    original = _sample_result()
    reconstructed = Result.from_dict(original.to_dict())

    assert reconstructed == original
    assert reconstructed.to_dict() == original.to_dict()


def test_result_from_dict_handles_a_violation_and_a_roofline():
    """A validity violation and a bottleneck roofline survive a to_dict/from_dict round trip."""
    from flux_store.result import Constraint, Roofline

    original = Result(
        metrics={
            "latency_cycles": Estimate(
                value=1000, ci_low=900, ci_high=1100, unit="cycles", method=Method.SIMULATED
            )
        },
        validity=Validity(
            ok=False, violations=(Constraint(kind="area_mm2", detail="12.0 > 10.0"),),
            checker_version="roofline-v0.1",
        ),
        domain=Domain(in_domain=True),
        bottleneck=Bottleneck(
            limiter=Limiter.COMPUTE,
            roofline=Roofline(arithmetic_intensity=2.0, peak=100.0, achieved=80.0),
        ),
        provenance=Provenance(evaluator="flux-native@0.1.0", inputs={}),
        escalation=Escalation(recommended=True, next_stage="rtl", reason="ci width > 25%"),
    )

    reconstructed = Result.from_dict(original.to_dict())
    assert reconstructed == original


def test_result_from_dict_keeps_metric_domains():
    """`metric_domains` survives a to_dict/from_dict round trip."""
    from flux_store.result import Domain

    original = Result(
        metrics={"latency_cycles": Estimate(value=10, ci_low=9, ci_high=11, unit="cycles",
                                            method=Method.ANALYTIC)},
        validity=Validity(ok=True, checker_version="t"),
        domain=Domain(in_domain=True),
        bottleneck=Bottleneck(limiter=Limiter.COMPUTE),
        provenance=Provenance(evaluator="t@1", inputs={}),
        escalation=Escalation(recommended=False),
        metric_domains={"latency_cycles": Domain(in_domain=False, distance=0.4,
                                                 nearest_calibration="cal-1")},
    )
    reconstructed = Result.from_dict(original.to_dict())
    assert reconstructed == original
    assert reconstructed.metric_domains["latency_cycles"].distance == 0.4
    # an older stored dict without the key still loads
    d = original.to_dict()
    del d["metric_domains"]
    assert Result.from_dict(d).metric_domains == {}
