"""`Result.metric`, the ABI's value-or-refusal accessor (D201).

An evaluator may legally omit a requested metric, so the accessor makes the handled case natural
and the unhandled case explain itself.
"""

from __future__ import annotations

import pytest
from flux_store.result import Bottleneck, Domain, Escalation, Estimate, Limiter, Method, MetricMap, MissingMetricError, Provenance, Result, Validity


def _result(**metrics: float) -> Result:
    return Result(
        metrics={
            name: Estimate(value=v, ci_low=v, ci_high=v, unit="u", method=Method.ANALYTIC)
            for name, v in metrics.items()
        },
        validity=Validity(ok=True, checker_version="test"),
        domain=Domain(in_domain=False),
        bottleneck=Bottleneck(limiter=Limiter.COMPUTE),
        provenance=Provenance(evaluator="fake@0.0.0", inputs={}),
        escalation=Escalation(recommended=False),
    )


def test_a_present_metric_is_ok_and_carries_its_value():
    outcome = _result(latency_cycles=42.0).metric("latency_cycles")

    assert outcome.ok is True
    assert outcome.value == 42.0
    assert outcome.reason is None
    assert outcome.estimate.unit == "u"


def test_an_absent_metric_is_not_ok_and_says_what_was_returned():
    outcome = _result(latency_cycles=42.0).metric("energy_pj")

    assert outcome.ok is False
    assert outcome.estimate is None
    assert "energy_pj" in outcome.reason and "latency_cycles" in outcome.reason


def test_reading_value_on_a_refusal_raises_with_the_reason():
    """For callers that already established presence: a missing metric raises rather than returning a sentinel."""
    outcome = _result().metric("energy_pj")

    with pytest.raises(MissingMetricError) as exc:
        outcome.value
    assert "energy_pj" in str(exc.value)


def test_value_or_gives_a_fallback_only_where_one_is_asked_for():
    assert _result().metric("energy_pj").value_or(-1.0) == -1.0
    assert _result(energy_pj=5.0).metric("energy_pj").value_or(-1.0) == 5.0


def test_a_zero_valued_metric_is_present_not_refused():
    """`0.0` is a real measurement; anything truthiness-based reports it as missing."""
    outcome = _result(energy_pj=0.0).metric("energy_pj")

    assert outcome.ok is True and outcome.value == 0.0


def test_direct_indexing_still_works_and_now_explains_itself():
    """`metrics` stays a dict, but a missing key raises with an actionable message."""
    result = _result(latency_cycles=1.0)

    assert result.metrics["latency_cycles"].value == 1.0
    assert isinstance(result.metrics, MetricMap)
    assert "latency_cycles" in result.metrics and len(result.metrics) == 1

    with pytest.raises(MissingMetricError) as exc:
        result.metrics["energy_pj"]
    assert "Result.metric(name)" in str(exc.value), "the message must name the handled path"


def test_missing_metric_error_is_still_a_keyerror():
    """The error is still a KeyError, so existing handlers keep working."""
    with pytest.raises(KeyError):
        _result().metrics["nope"]


def test_a_result_round_trips_through_to_dict_unchanged():
    """`MetricMap` is a dict subclass, so serialisation is untouched."""
    original = _result(latency_cycles=7.0, energy_pj=8.0)

    restored = Result.from_dict(original.to_dict())

    assert restored.to_dict() == original.to_dict()
    assert isinstance(restored.metrics, MetricMap)
    assert restored.metric("latency_cycles").value == 7.0
