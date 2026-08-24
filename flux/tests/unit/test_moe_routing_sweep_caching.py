"""Real, dependency-tracked re-evaluation for `flux_sweep_moe_routing` (docs/decisions.md D86,
generalizing D79's own pattern beyond `flux_characterize_memory_level`): a stub evaluator counting
real calls, so hit/miss behavior is checked directly, not inferred from timing. See
tests/integration/test_moe_routing_sweep_live.py for the real-evaluator version.
"""

from __future__ import annotations

import pytest
from flux_chia_nodes import moe_routing
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
from flux_store import ResultStore

_WORKLOAD = {
    "id": "w",
    "ops": [
        {"id": "expert0", "kind": "einsum", "expr": "A B, B C -> A C", "bounds": {"A": 2, "B": 4, "C": 8}},
        {"id": "expert1", "kind": "einsum", "expr": "A B, B C -> A C", "bounds": {"A": 2, "B": 4, "C": 12}},
        {"id": "expert2", "kind": "einsum", "expr": "A B, B C -> A C", "bounds": {"A": 2, "B": 4, "C": 16}},
        {
            "id": "moe.route",
            "kind": "data_dependent",
            "semantics": {"top_k": 2, "experts": 3, "candidate_ops": ["expert0", "expert1", "expert2"]},
        },
    ],
}


def _make_result(evaluator: str, value: float) -> Result:
    return Result(
        metrics={"latency_cycles": Estimate(value=value, ci_low=value, ci_high=value, unit="cycles", method=Method.ANALYTIC)},
        validity=Validity(ok=True, checker_version="test"),
        domain=Domain(in_domain=True),
        bottleneck=Bottleneck(limiter=Limiter.COMPUTE),
        provenance=Provenance(evaluator=evaluator, inputs={}),
        escalation=Escalation(recommended=False),
    )


class _CountingEvaluator:
    def __init__(self) -> None:
        self.calls: list[Candidate] = []

    def evaluate(self, candidate: Candidate, budget: Budget, metrics: frozenset[str]) -> Result:
        self.calls.append(candidate)
        # A stable, real function of exactly which experts survived resolution — different
        # routing decisions produce different op counts, so a wrong cache hit would be visible.
        return _make_result("zigzag@stub", float(len(candidate.workload["ops"])))

    def evaluate_batch(self, candidates, budget, metrics):  # pragma: no cover - unused here
        return [self.evaluate(c, budget, metrics) for c in candidates]


@pytest.fixture
def store(tmp_path):
    with ResultStore(tmp_path / "flux.db") as s:
        yield s


@pytest.fixture
def counting_evaluator(monkeypatch):
    stub = _CountingEvaluator()
    monkeypatch.setattr(moe_routing, "make_evaluator", lambda backend: stub)
    return stub


def test_without_result_db_path_every_call_is_real_unchanged_behavior(counting_evaluator):
    moe_routing.flux_sweep_moe_routing("zigzag", _WORKLOAD, "moe.route", [["expert0", "expert1"]])
    moe_routing.flux_sweep_moe_routing("zigzag", _WORKLOAD, "moe.route", [["expert0", "expert1"]])
    assert len(counting_evaluator.calls) == 2  # nothing cached without result_db_path


def test_a_duplicate_routing_sample_within_one_call_is_a_real_cache_hit(counting_evaluator, store):
    """The real, common case this decision targets: a Monte-Carlo-style caller draws many samples
    from a small discrete space of top_k combinations — real repeats are expected, not an edge
    case."""
    moe_routing.flux_sweep_moe_routing(
        "zigzag", _WORKLOAD, "moe.route",
        [["expert0", "expert1"], ["expert1", "expert2"], ["expert0", "expert1"]],
        result_db_path=str(store.db_path),
    )
    assert len(counting_evaluator.calls) == 2  # ["expert0","expert1"] evaluated once, not twice


def test_repeating_the_exact_same_sweep_across_calls_is_a_real_cache_hit(counting_evaluator, store):
    moe_routing.flux_sweep_moe_routing(
        "zigzag", _WORKLOAD, "moe.route", [["expert0", "expert1"], ["expert1", "expert2"]],
        result_db_path=str(store.db_path),
    )
    moe_routing.flux_sweep_moe_routing(
        "zigzag", _WORKLOAD, "moe.route", [["expert0", "expert1"], ["expert1", "expert2"]],
        result_db_path=str(store.db_path),
    )
    assert len(counting_evaluator.calls) == 2  # only the first call's 2 distinct routings were real


def test_a_different_routing_order_is_still_the_same_resolved_workload_and_hits_cache(counting_evaluator, store):
    """Real correctness check, not just a hit-rate check: resolve_moe_routing drops unselected
    experts by id, so ["expert1","expert0"] and ["expert0","expert1"] resolve to the identical
    static workload (same surviving ops) — a genuine cache hit, not coincidentally-equal counts."""
    moe_routing.flux_sweep_moe_routing(
        "zigzag", _WORKLOAD, "moe.route", [["expert0", "expert1"]], result_db_path=str(store.db_path),
    )
    moe_routing.flux_sweep_moe_routing(
        "zigzag", _WORKLOAD, "moe.route", [["expert1", "expert0"]], result_db_path=str(store.db_path),
    )
    assert len(counting_evaluator.calls) == 1
