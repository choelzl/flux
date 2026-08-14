"""`Result.refusal_for` / `Result.value_of` (docs/decisions.md D169): one place that answers "does
this Result carry the metric I asked for, and if not, what do I tell the user?"

The ABI permits an evaluator to return a Result without the metric that was requested — it is not
an error, and `evaluators/rtl` does it routinely (its metrics dict is populated only when
`latency_cycles` was asked for). So every consumer must handle it, and before D169 each had to
*remember* to: D112 fixed the resulting KeyError in `search/architecture/dse.py`, an independent
review fixed it in `workload_dynamism/sweep.py`, and the identical hole stayed open in the
annealing, agentic and exhaustive strategies (D168) and in `sweep.py`'s own sibling
`moe_routing.py` (D169).
"""

from __future__ import annotations

import pytest
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


def test_refusal_is_none_when_the_metric_is_present():
    assert _result(latency_cycles=42.0).refusal_for("latency_cycles") is None


def test_refusal_names_the_metric_and_what_was_actually_returned():
    refusal = _result(latency_cycles=42.0).refusal_for("energy_pj")
    assert refusal is not None
    assert "energy_pj" in refusal
    assert "latency_cycles" in refusal, "a refusal that doesn't say what IS available is a dead end"


def test_refusal_on_an_empty_metrics_dict():
    """`evaluators/rtl`'s real shape for any metric other than latency_cycles — the case that
    produced the crashes in D168."""
    refusal = _result().refusal_for("energy_pj")
    assert refusal is not None and "[]" in refusal


def test_value_of_returns_the_value():
    assert _result(latency_cycles=42.0).value_of("latency_cycles") == 42.0


def test_value_of_raises_carrying_the_same_message():
    """The two must not drift: a caller that checks with one and reads with the other should get
    the same explanation either way."""
    result = _result(latency_cycles=42.0)
    with pytest.raises(KeyError) as exc:
        result.value_of("energy_pj")
    assert result.refusal_for("energy_pj") in str(exc.value)


def test_a_metric_present_but_zero_is_not_a_refusal():
    """`0.0` is a legitimate measurement; a truthiness-based check would call it missing."""
    assert _result(energy_pj=0.0).refusal_for("energy_pj") is None
    assert _result(energy_pj=0.0).value_of("energy_pj") == 0.0
