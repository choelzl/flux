"""Unit tests for flux_workload_dynamism.moe_routing: pure resolution/aggregation logic against a
synthetic fake evaluator, no real ZigZag/Timeloop call needed. See
tests/integration/test_moe_routing_sweep_live.py for the real-evaluator version.
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
from flux_workload_dynamism import MoeRoutingError, resolve_moe_routing, sweep_moe_routing

_WORKLOAD = {
    "id": "w",
    "ops": [
        {"id": "expert0", "kind": "einsum", "expr": "A B, B C -> A C", "bounds": {"A": 2, "B": 4, "C": 8}},
        {"id": "expert1", "kind": "einsum", "expr": "A B, B C -> A C", "bounds": {"A": 2, "B": 4, "C": 12}},
        {"id": "expert2", "kind": "einsum", "expr": "A B, B C -> A C", "bounds": {"A": 2, "B": 4, "C": 16}},
        {"id": "other.op", "kind": "einsum", "expr": "X Y, Y Z -> X Z", "bounds": {"X": 1, "Y": 1, "Z": 1}},
        {
            "id": "moe.route",
            "kind": "data_dependent",
            "semantics": {"top_k": 2, "experts": 3, "candidate_ops": ["expert0", "expert1", "expert2"]},
        },
    ],
}


def _op_ids(doc: dict) -> set[str]:
    return {op["id"] for op in doc["ops"]}


def test_resolve_drops_the_routing_op_and_unselected_experts():
    resolved = resolve_moe_routing(_WORKLOAD, "moe.route", ["expert0", "expert2"])
    assert _op_ids(resolved) == {"expert0", "expert2", "other.op"}


def test_resolve_does_not_mutate_the_original():
    original_ids = _op_ids(_WORKLOAD)
    resolve_moe_routing(_WORKLOAD, "moe.route", ["expert0", "expert1"])
    assert _op_ids(_WORKLOAD) == original_ids


def test_resolve_unknown_op_raises():
    with pytest.raises(MoeRoutingError, match="no op with id"):
        resolve_moe_routing(_WORKLOAD, "nope", ["expert0", "expert1"])


def test_resolve_non_data_dependent_op_raises():
    with pytest.raises(MoeRoutingError, match="not 'data_dependent'"):
        resolve_moe_routing(_WORKLOAD, "expert0", ["expert0"])


def test_resolve_selection_outside_candidates_raises():
    with pytest.raises(MoeRoutingError, match="not in its own declared candidate_ops"):
        resolve_moe_routing(_WORKLOAD, "moe.route", ["expert0", "not_a_real_expert"])


def test_resolve_wrong_selection_count_raises():
    with pytest.raises(MoeRoutingError, match="top_k=2"):
        resolve_moe_routing(_WORKLOAD, "moe.route", ["expert0"])


def test_resolve_op_with_no_candidate_ops_raises():
    workload = {
        "id": "w2",
        "ops": [{"id": "route", "kind": "data_dependent", "semantics": {"top_k": 1}}],
    }
    with pytest.raises(MoeRoutingError, match="no semantics.candidate_ops"):
        resolve_moe_routing(workload, "route", ["x"])


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
    """Cost = sum of selected experts' own C bound — a fake, but real-shaped, per-selection cost
    function, so the test can assert the aggregate reflects the exact real values it was given."""

    _C_BY_EXPERT = {"expert0": 8, "expert1": 12, "expert2": 16}

    def __init__(self) -> None:
        self.calls: list[frozenset[str]] = []

    def evaluate(self, candidate: Candidate, budget: Budget, metrics: frozenset[str]) -> Result:
        selected = {op["id"] for op in candidate.workload["ops"] if op["id"] in self._C_BY_EXPERT}
        self.calls.append(frozenset(selected))
        return _result(float(sum(self._C_BY_EXPERT[e] for e in selected)))


def test_sweep_empty_routing_samples_raises():
    with pytest.raises(MoeRoutingError, match="non-empty"):
        sweep_moe_routing(_WORKLOAD, "moe.route", [], _FakeEvaluator(), metric="latency_cycles")


def test_sweep_calls_evaluator_once_per_real_routing_sample():
    evaluator = _FakeEvaluator()
    sweep_moe_routing(
        _WORKLOAD, "moe.route",
        [["expert0", "expert1"], ["expert1", "expert2"]],
        evaluator, metric="latency_cycles",
    )
    assert evaluator.calls == [frozenset({"expert0", "expert1"}), frozenset({"expert1", "expert2"})]


def test_sweep_aggregates_mean_and_real_min_max_as_ci():
    evaluator = _FakeEvaluator()
    result = sweep_moe_routing(
        _WORKLOAD, "moe.route",
        [["expert0", "expert1"], ["expert1", "expert2"], ["expert0", "expert2"]],
        evaluator, metric="latency_cycles",
    )
    est = result.metrics["latency_cycles"]
    # costs: 8+12=20, 12+16=28, 8+16=24
    assert est.value == pytest.approx((20.0 + 28.0 + 24.0) / 3)
    assert est.ci_low == pytest.approx(20.0)
    assert est.ci_high == pytest.approx(28.0)


def test_sweep_single_sample_gives_a_degenerate_but_valid_estimate():
    evaluator = _FakeEvaluator()
    result = sweep_moe_routing(
        _WORKLOAD, "moe.route", [["expert0", "expert1"]], evaluator, metric="latency_cycles",
    )
    est = result.metrics["latency_cycles"]
    assert est.value == est.ci_low == est.ci_high == pytest.approx(20.0)


def test_sweep_validity_is_false_if_any_sample_is_invalid():
    class _MixedEvaluator:
        def evaluate(self, candidate, budget, metrics):
            selected = {op["id"] for op in candidate.workload["ops"] if op["id"].startswith("expert")}
            return _result(float(len(selected)), ok=("expert2" not in selected))

    result = sweep_moe_routing(
        _WORKLOAD, "moe.route",
        [["expert0", "expert1"], ["expert1", "expert2"]],
        _MixedEvaluator(), metric="latency_cycles",
    )
    assert result.validity.ok is False


def test_sweep_provenance_records_every_routing_sample_and_workload_hash():
    evaluator = _FakeEvaluator()
    result = sweep_moe_routing(
        _WORKLOAD, "moe.route",
        [["expert0", "expert1"], ["expert1", "expert2"]],
        evaluator, metric="latency_cycles",
    )
    assert result.provenance.inputs["routing_samples"] == [["expert0", "expert1"], ["expert1", "expert2"]]
    assert result.provenance.inputs["op_id"] == "moe.route"
    assert result.provenance.evaluator.startswith("moe-routing-sweep+")
    assert len(result.provenance.inputs["per_sample_workload_hashes"]) == 2


def test_evaluator_without_the_requested_metric_raises_a_typed_error():
    """`sweep_dynamic_shape` has guarded this since a review finding; its sibling never got the
    guard, so the sweep evaluated every routing sample and then died on a bare
    `KeyError: 'energy_pj'` naming neither the evaluator nor what it does emit
    (docs/decisions.md D169). Evaluators are free to ignore the requested-metrics set — zigzag
    does, and `evaluators/rtl` returns an empty metrics dict for anything but `latency_cycles`.
    """
    class _MetriclessEvaluator:
        def evaluate(self, candidate, budget, metrics):
            return Result(
                metrics={},
                validity=Validity(ok=True, checker_version="test"),
                domain=Domain(in_domain=False),
                bottleneck=Bottleneck(limiter=Limiter.COMPUTE),
                provenance=Provenance(evaluator="rtl@0.0.0", inputs={}),
                escalation=Escalation(recommended=False),
            )

    with pytest.raises(MoeRoutingError, match="does not emit metric 'energy_pj'"):
        sweep_moe_routing(
            _WORKLOAD, "moe.route", [["expert0", "expert1"], ["expert0", "expert2"]],
            _MetriclessEvaluator(), arch={}, metric="energy_pj",
        )
