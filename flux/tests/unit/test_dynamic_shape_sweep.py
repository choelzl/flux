"""Unit tests for flux_workload_dynamism.sweep: pure resolution/aggregation logic against a
synthetic fake evaluator, no real ZigZag/Timeloop call needed. See
tests/integration/test_dynamic_shape_sweep_live.py for the real-evaluator version.
"""

from __future__ import annotations

import pytest
from flux_evaluator_abi import (
    Bottleneck,
    Budget,
    Candidate,
    Domain,
    Escalation,
    Estimate,
    Limiter,
    Method,
    Provenance,
    Result,
    Validity,
)
from flux_workload_dynamism import DynamicShapeError, resolve_dynamic_bound, sweep_dynamic_shape

_WORKLOAD = {
    "id": "w",
    "ops": [
        {"id": "op0", "kind": "einsum", "expr": "S D, T D -> S T", "bounds": {"S": 1, "D": 64, "T": {"dyn": [1, 256]}}},
        {"id": "op1", "kind": "einsum", "expr": "A B, B C -> A C", "bounds": {"A": 2, "B": 4, "C": 8}},
    ],
}


def test_resolve_dynamic_bound_replaces_only_the_named_dim():
    resolved = resolve_dynamic_bound(_WORKLOAD, "op0", "T", 16)
    assert resolved["ops"][0]["bounds"] == {"S": 1, "D": 64, "T": 16}
    assert resolved["ops"][1]["bounds"] == {"A": 2, "B": 4, "C": 8}  # untouched


def test_resolve_dynamic_bound_does_not_mutate_the_original():
    original_bounds = dict(_WORKLOAD["ops"][0]["bounds"])
    resolve_dynamic_bound(_WORKLOAD, "op0", "T", 16)
    assert _WORKLOAD["ops"][0]["bounds"] == original_bounds


def test_resolve_unknown_op_raises():
    with pytest.raises(DynamicShapeError, match="no op with id"):
        resolve_dynamic_bound(_WORKLOAD, "nope", "T", 16)


def test_resolve_unknown_dim_raises():
    with pytest.raises(DynamicShapeError, match="no bound named"):
        resolve_dynamic_bound(_WORKLOAD, "op0", "Z", 16)


def test_resolve_already_static_bound_raises():
    with pytest.raises(DynamicShapeError, match="not a dynamic"):
        resolve_dynamic_bound(_WORKLOAD, "op1", "A", 5)


def test_resolve_value_outside_declared_range_raises():
    with pytest.raises(DynamicShapeError, match="outside op"):
        resolve_dynamic_bound(_WORKLOAD, "op0", "T", 9999)


def _result(value: float, *, in_domain: bool = True, ok: bool = True) -> Result:
    return Result(
        metrics={"latency_cycles": Estimate(value=value, ci_low=value, ci_high=value, unit="cycles", method=Method.ANALYTIC)},
        validity=Validity(ok=ok, checker_version="test"),
        domain=Domain(in_domain=in_domain),
        bottleneck=Bottleneck(limiter=Limiter.COMPUTE),
        provenance=Provenance(evaluator="fake@0.0", inputs={"workload_hash": f"hash-{value}"}),
        escalation=Escalation(recommended=False),
    )


class _FakeEvaluator:
    """Returns a fixed value per T (looked up from the resolved workload's own bound), so the
    test can assert the aggregate reflects the exact real values it was given."""

    def __init__(self, value_by_t: dict[int, float]) -> None:
        self.value_by_t = value_by_t
        self.calls: list[int] = []

    def evaluate(self, candidate: Candidate, budget: Budget, metrics: frozenset[str]) -> Result:
        t = candidate.workload["ops"][0]["bounds"]["T"]
        self.calls.append(t)
        return _result(self.value_by_t[t])


def test_sweep_empty_sample_points_raises():
    evaluator = _FakeEvaluator({})
    with pytest.raises(DynamicShapeError, match="non-empty"):
        sweep_dynamic_shape(_WORKLOAD, "op0", "T", [], evaluator, metric="latency_cycles")


def test_sweep_calls_evaluator_once_per_real_sample_point():
    evaluator = _FakeEvaluator({1: 10.0, 8: 20.0, 32: 30.0})
    sweep_dynamic_shape(_WORKLOAD, "op0", "T", [1, 8, 32], evaluator, metric="latency_cycles")
    assert evaluator.calls == [1, 8, 32]


def test_sweep_aggregates_mean_and_real_min_max_as_ci():
    evaluator = _FakeEvaluator({1: 10.0, 8: 20.0, 32: 30.0})
    result = sweep_dynamic_shape(_WORKLOAD, "op0", "T", [1, 8, 32], evaluator, metric="latency_cycles")
    est = result.metrics["latency_cycles"]
    assert est.value == pytest.approx(20.0)  # mean(10, 20, 30)
    assert est.ci_low == pytest.approx(10.0)
    assert est.ci_high == pytest.approx(30.0)


def test_sweep_single_sample_point_gives_a_degenerate_but_valid_estimate():
    evaluator = _FakeEvaluator({8: 42.0})
    result = sweep_dynamic_shape(_WORKLOAD, "op0", "T", [8], evaluator, metric="latency_cycles")
    est = result.metrics["latency_cycles"]
    assert est.value == est.ci_low == est.ci_high == pytest.approx(42.0)


def test_sweep_validity_is_false_if_any_sample_is_invalid():
    class _MixedEvaluator:
        def evaluate(self, candidate, budget, metrics):
            t = candidate.workload["ops"][0]["bounds"]["T"]
            return _result(float(t), ok=(t != 32))

    result = sweep_dynamic_shape(_WORKLOAD, "op0", "T", [1, 8, 32], _MixedEvaluator(), metric="latency_cycles")
    assert result.validity.ok is False


def test_sweep_domain_is_false_if_any_sample_is_out_of_domain():
    class _MixedEvaluator:
        def evaluate(self, candidate, budget, metrics):
            t = candidate.workload["ops"][0]["bounds"]["T"]
            return _result(float(t), in_domain=(t != 32))

    result = sweep_dynamic_shape(_WORKLOAD, "op0", "T", [1, 8, 32], _MixedEvaluator(), metric="latency_cycles")
    assert result.domain.in_domain is False


def test_sweep_provenance_records_every_sample_point_and_workload_hash():
    evaluator = _FakeEvaluator({1: 10.0, 8: 20.0})
    result = sweep_dynamic_shape(_WORKLOAD, "op0", "T", [1, 8], evaluator, metric="latency_cycles")
    assert result.provenance.inputs["sample_points"] == [1, 8]
    assert result.provenance.inputs["op_id"] == "op0"
    assert result.provenance.inputs["dim"] == "T"
    assert result.provenance.evaluator.startswith("dynamic-shape-sweep+")
    assert len(result.provenance.inputs["per_sample_workload_hashes"]) == 2


# --- Review-driven fixes (docs/decisions.md D96) ---


def _workload_with_dyn(dyn_value):
    return {
        "id": "w-malformed",
        "ops": [
            {"id": "op0", "kind": "einsum", "expr": "S D, T D -> S T",
             "bounds": {"S": 1, "D": 64, "T": {"dyn": dyn_value}}},
        ],
    }


@pytest.mark.parametrize("bad_dyn", [[512], 512, [1, 2, 3], ["a", "b"], None])
def test_malformed_dyn_declaration_raises_typed_error_not_bare_unpacking(bad_dyn):
    """workload.schema.json leaves `bounds` unconstrained, so these are all schema-valid — they
    previously escaped as bare ValueError/TypeError from tuple unpacking (review finding)."""
    with pytest.raises(DynamicShapeError, match="malformed dyn"):
        resolve_dynamic_bound(_workload_with_dyn(bad_dyn), "op0", "T", 16)


def test_inverted_dyn_range_raises_instead_of_silently_collapsing_samples():
    """lo > hi previously validated nothing here and silently collapsed every quantile sample
    to hi downstream (review finding)."""
    with pytest.raises(DynamicShapeError, match="inverted"):
        resolve_dynamic_bound(_workload_with_dyn([100, 10]), "op0", "T", 16)


def test_metric_the_evaluator_never_emits_raises_after_exactly_one_evaluation():
    """The fake (like real ZigZag) ignores the requested-metrics set and always returns
    latency_cycles — an unknown `metric` previously ran ALL samples and then crashed with a raw
    KeyError during aggregation (review finding). Now: one real evaluation, one typed error."""
    evaluator = _FakeEvaluator({1: 10.0, 8: 20.0, 32: 30.0})
    with pytest.raises(DynamicShapeError, match="does not emit metric 'area_mm2'"):
        sweep_dynamic_shape(_WORKLOAD, "op0", "T", [1, 8, 32], evaluator, metric="area_mm2")
    assert evaluator.calls == [1]  # failed fast — not after the whole sweep


def test_a_repeated_sample_point_is_evaluated_once_but_still_weighted():
    """`quantile_sample_points` clips into a workload's declared bound, so every quantile above
    `hi` collapses onto it — measured with the repo's own kv-cache-len-v1 distribution, 7 of 8
    points land on the same value for a small `hi` (docs/decisions.md D194).

    Each duplicate is real probability weight and must stay in the aggregate, but re-running an
    identical resolved workload buys nothing, and this module exists to drive expensive
    evaluators. So: one call per distinct value, and an aggregate identical to the un-memoised
    one.
    """
    evaluator = _FakeEvaluator({1: 10.0, 32: 100.0})
    points = [1, 32, 32, 32, 32, 32, 32, 32]

    result = sweep_dynamic_shape(
        _WORKLOAD, "op0", "T", points, evaluator, metric="latency_cycles",
    )

    assert evaluator.calls == [1, 32], "each distinct sample point evaluated exactly once"

    estimate = result.metrics["latency_cycles"]
    assert estimate.value == pytest.approx((10.0 + 7 * 100.0) / 8), (
        "the duplicates must still carry their probability weight in the mean"
    )
    assert (estimate.ci_low, estimate.ci_high) == (10.0, 100.0)
