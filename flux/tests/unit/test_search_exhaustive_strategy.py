"""Unit tests for flux_search_exhaustive.strategy: the propose/observe/done Protocol and
run_exhaustive_search's driving logic, against a fake evaluator (no real ZigZag/Timeloop) — pure
control-flow correctness. See tests/integration/test_search_exhaustive_live.py for the real-
evaluator version.
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
from flux_search_exhaustive import ExhaustiveMappingStrategy, SearchState, run_exhaustive_search

_WORKLOAD = {
    "schema_version": "0.1.0",
    "id": "test/wl",
    "tensors": [{"name": t, "rank": ["B", "K"], "dtype": "int8"} for t in ("I", "W", "O")],
    "ops": [{"id": "test.op", "kind": "einsum", "expr": "unused", "bounds": {"B": 4, "K": 16}}],
}
_ARCH = {
    "schema_version": "0.1.0",
    "id": "test/arch",
    "tech": {"node": "n28", "pdk_class": "open"},
    "hierarchy": [
        {"level": "dram", "class": "memory", "attrs": {"size_kb": 1_000_000}},
        {"level": "gbuf", "class": "memory", "attrs": {"size_kb": 512}},
        {"level": "pe_array", "class": "compute", "attrs": {"dims": {"X": 8}}},
    ],
}
# 2 loop dims -> 2 spatial splits x 2! = 2 temporal orders = 4 candidates total.


def _result(value: float, evaluator: str = "fake@0.0.0") -> Result:
    return Result(
        metrics={"latency_cycles": Estimate(value=value, ci_low=value, ci_high=value, unit="cycles", method=Method.ANALYTIC)},
        validity=Validity(ok=True, checker_version="test"),
        domain=Domain(in_domain=False),
        bottleneck=Bottleneck(limiter=Limiter.COMPUTE),
        provenance=Provenance(evaluator=evaluator, inputs={}),
        escalation=Escalation(recommended=False),
    )


class _FakeEvaluator:
    """Deterministic fake: latency = 100 - len(candidate.mapping['id']) so different candidates
    score differently and the "best" selection logic is actually exercised, not coincidental."""

    def __init__(self):
        self.calls = 0

    def evaluate(self, candidate: Candidate, budget: Budget, metrics: frozenset[str]) -> Result:
        self.calls += 1
        assert candidate.mapping is not None
        return _result(float(100 - len(candidate.mapping["id"])))


class _RefusingEvaluator:
    def evaluate(self, candidate: Candidate, budget: Budget, metrics: frozenset[str]) -> Result:
        raise ValueError("not_expressible_in: [fake]")


def test_propose_serves_candidates_in_batches_of_k():
    strategy = ExhaustiveMappingStrategy(_WORKLOAD, _ARCH, for_op="test.op")
    state = SearchState(workload=_WORKLOAD, arch=_ARCH, for_op="test.op")
    batch = strategy.propose(state, k=2)
    assert len(batch) == 2
    assert not strategy.done()


def test_propose_before_observe_raises():
    strategy = ExhaustiveMappingStrategy(_WORKLOAD, _ARCH, for_op="test.op")
    state = SearchState(workload=_WORKLOAD, arch=_ARCH, for_op="test.op")
    strategy.propose(state, k=2)
    with pytest.raises(RuntimeError):
        strategy.propose(state, k=2)


def test_observe_wrong_length_raises():
    strategy = ExhaustiveMappingStrategy(_WORKLOAD, _ARCH, for_op="test.op")
    state = SearchState(workload=_WORKLOAD, arch=_ARCH, for_op="test.op")
    strategy.propose(state, k=2)
    with pytest.raises(ValueError):
        strategy.observe([_result(1.0)])  # 1 result for 2 proposed


def test_done_becomes_true_after_every_candidate_observed():
    strategy = ExhaustiveMappingStrategy(_WORKLOAD, _ARCH, for_op="test.op")
    state = SearchState(workload=_WORKLOAD, arch=_ARCH, for_op="test.op")
    assert strategy.total_candidates == 4
    assert not strategy.done()
    while not strategy.done():
        batch = strategy.propose(state, k=2)
        strategy.observe([_result(1.0) for _ in batch])
    assert len(strategy.evaluated) == 4


def test_observe_records_exceptions_as_errors_not_crashes():
    strategy = ExhaustiveMappingStrategy(_WORKLOAD, _ARCH, for_op="test.op")
    state = SearchState(workload=_WORKLOAD, arch=_ARCH, for_op="test.op")
    batch = strategy.propose(state, k=4)
    strategy.observe([ValueError("nope") for _ in batch])
    assert len(strategy.evaluated) == 4
    assert all(e.error == "nope" and e.result is None for e in strategy.evaluated)


def test_run_exhaustive_search_evaluates_every_candidate():
    evaluator = _FakeEvaluator()
    report = run_exhaustive_search(_WORKLOAD, _ARCH, evaluator, for_op="test.op", metric="latency_cycles")
    assert evaluator.calls == 4
    assert len(report.evaluated) == 4
    assert report.skipped_not_expressible == 0


def test_run_exhaustive_search_finds_the_true_minimum():
    evaluator = _FakeEvaluator()
    report = run_exhaustive_search(_WORKLOAD, _ARCH, evaluator, for_op="test.op", metric="latency_cycles", minimize=True)
    scored = [e for e in report.evaluated if e.result is not None]
    true_best = min(e.result.metrics["latency_cycles"].value for e in scored)
    assert report.best is not None
    assert report.best.result.metrics["latency_cycles"].value == true_best


def test_run_exhaustive_search_can_maximize_instead():
    evaluator = _FakeEvaluator()
    report = run_exhaustive_search(_WORKLOAD, _ARCH, evaluator, for_op="test.op", metric="latency_cycles", minimize=False)
    scored = [e for e in report.evaluated if e.result is not None]
    true_best = max(e.result.metrics["latency_cycles"].value for e in scored)
    assert report.best.result.metrics["latency_cycles"].value == true_best


def test_run_exhaustive_search_survives_a_fully_refusing_evaluator():
    evaluator = _RefusingEvaluator()
    report = run_exhaustive_search(_WORKLOAD, _ARCH, evaluator, for_op="test.op", metric="latency_cycles")
    assert report.skipped_not_expressible == 4
    assert report.best is None
