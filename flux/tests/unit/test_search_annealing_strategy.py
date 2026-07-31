"""Unit tests for flux_search_annealing.strategy: propose/observe/done control flow and the
Metropolis accept/reject logic, against a fake evaluator with a known synthetic landscape (no
real ZigZag). See tests/integration/test_search_annealing_live.py for the real-evaluator version,
validated against exhaustive search's proven optimum.
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
from flux_search_annealing import SearchState, SimulatedAnnealingMappingStrategy, run_simulated_annealing

_WORKLOAD = {
    "schema_version": "0.1.0",
    "id": "test/wl",
    "tensors": [{"name": t, "rank": ["B", "C", "K"], "dtype": "int8"} for t in ("I", "W", "O")],
    "ops": [{"id": "test.op", "kind": "einsum", "expr": "unused", "bounds": {"B": 4, "C": 32, "K": 32}}],
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


def _result(value: float) -> Result:
    return Result(
        metrics={"latency_cycles": Estimate(value=value, ci_low=value, ci_high=value, unit="cycles", method=Method.ANALYTIC)},
        validity=Validity(ok=True, checker_version="test"),
        domain=Domain(in_domain=False),
        bottleneck=Bottleneck(limiter=Limiter.COMPUTE),
        provenance=Provenance(evaluator="fake@0.0.0", inputs={}),
        escalation=Escalation(recommended=False),
    )


class _KnownLandscapeEvaluator:
    """A deterministic, hand-designed scoring function over (spatial_dim, temporal_order): the
    true global minimum is spatial_dim='C', temporal_order=('K','B','C') at value 0.0 — every
    other point scores strictly higher, by construction, so "did the search find the true
    optimum" has an unambiguous, known-in-advance answer.
    """

    TRUE_OPTIMUM_VALUE = 0.0

    def __init__(self):
        self.calls = 0

    def evaluate(self, candidate: Candidate, budget: Budget, metrics: frozenset[str]) -> Result:
        self.calls += 1
        assert candidate.mapping is not None
        spatial_dim = candidate.mapping["spatial"][0]["dim"]
        order = tuple(
            loop["dim"]
            for loop in sorted(candidate.mapping["operands"]["I"][0]["loops"], key=lambda l: l["order"])
        )
        if spatial_dim == "C" and order == ("K", "B", "C"):
            return _result(self.TRUE_OPTIMUM_VALUE)
        # Distance-based penalty: further from the optimum's spatial dim / order = higher score.
        penalty = (0 if spatial_dim == "C" else 10) + sum(a != b for a, b in zip(order, ("K", "B", "C")))
        return _result(float(penalty))


class _AlwaysRefusesEvaluator:
    def evaluate(self, candidate: Candidate, budget: Budget, metrics: frozenset[str]) -> Result:
        raise ValueError("not_expressible_in: [fake]")


def test_propose_requires_k_equals_one():
    strategy = SimulatedAnnealingMappingStrategy(_WORKLOAD, _ARCH, for_op="test.op", metric="latency_cycles")
    state = SearchState(workload=_WORKLOAD, arch=_ARCH, for_op="test.op")
    with pytest.raises(ValueError):
        strategy.propose(state, k=2)


def test_propose_before_observe_raises():
    strategy = SimulatedAnnealingMappingStrategy(_WORKLOAD, _ARCH, for_op="test.op", metric="latency_cycles")
    state = SearchState(workload=_WORKLOAD, arch=_ARCH, for_op="test.op")
    strategy.propose(state, k=1)
    with pytest.raises(RuntimeError):
        strategy.propose(state, k=1)


def test_observe_before_propose_raises():
    strategy = SimulatedAnnealingMappingStrategy(_WORKLOAD, _ARCH, for_op="test.op", metric="latency_cycles")
    with pytest.raises(RuntimeError):
        strategy.observe([_result(1.0)])


def test_done_after_max_iterations():
    strategy = SimulatedAnnealingMappingStrategy(
        _WORKLOAD, _ARCH, for_op="test.op", metric="latency_cycles", max_iterations=3,
        min_temperature=0.0,  # so only max_iterations governs done()
    )
    state = SearchState(workload=_WORKLOAD, arch=_ARCH, for_op="test.op")
    for _ in range(3):
        assert not strategy.done()
        strategy.propose(state, k=1)
        strategy.observe([_result(1.0)])
    assert strategy.done()


def test_done_once_temperature_drops_below_floor():
    strategy = SimulatedAnnealingMappingStrategy(
        _WORKLOAD, _ARCH, for_op="test.op", metric="latency_cycles",
        initial_temperature=1.0, cooling_rate=0.1, min_temperature=0.05, max_iterations=1000,
    )
    state = SearchState(workload=_WORKLOAD, arch=_ARCH, for_op="test.op")
    iterations = 0
    while not strategy.done() and iterations < 10:
        strategy.propose(state, k=1)
        strategy.observe([_result(1.0)])
        iterations += 1
    assert strategy.done()
    assert iterations < 10  # cools below 0.05 well before max_iterations=1000


def test_refused_candidate_is_recorded_as_a_rejected_move_not_a_crash():
    strategy = SimulatedAnnealingMappingStrategy(
        _WORKLOAD, _ARCH, for_op="test.op", metric="latency_cycles", max_iterations=2,
    )
    state = SearchState(workload=_WORKLOAD, arch=_ARCH, for_op="test.op")
    strategy.propose(state, k=1)
    strategy.observe([ValueError("nope")])
    assert len(strategy.evaluated) == 1
    assert strategy.evaluated[0].error == "nope"
    assert strategy.evaluated[0].result is None


def test_run_simulated_annealing_finds_the_known_true_optimum():
    evaluator = _KnownLandscapeEvaluator()
    report = run_simulated_annealing(
        _WORKLOAD, _ARCH, evaluator, for_op="test.op", metric="latency_cycles",
        minimize=True, initial_temperature=5.0, cooling_rate=0.95, max_iterations=150, seed=42,
    )
    assert report.best_result is not None
    assert report.best_result.metrics["latency_cycles"].value == evaluator.TRUE_OPTIMUM_VALUE


def test_same_seed_is_fully_deterministic():
    def run():
        return run_simulated_annealing(
            _WORKLOAD, _ARCH, _KnownLandscapeEvaluator(), for_op="test.op", metric="latency_cycles",
            max_iterations=50, seed=7,
        )

    report_a, report_b = run(), run()
    values_a = [e.result.metrics["latency_cycles"].value for e in report_a.evaluated if e.result]
    values_b = [e.result.metrics["latency_cycles"].value for e in report_b.evaluated if e.result]
    assert values_a == values_b


def test_different_seeds_can_produce_different_trajectories():
    def run(seed):
        return run_simulated_annealing(
            _WORKLOAD, _ARCH, _KnownLandscapeEvaluator(), for_op="test.op", metric="latency_cycles",
            max_iterations=30, seed=seed,
        )

    trajectories = [
        tuple(e.candidate.spatial_dim for e in run(seed).evaluated) for seed in range(5)
    ]
    assert len(set(trajectories)) > 1  # not all 5 seeds produced the identical move sequence


def test_survives_an_evaluator_that_always_refuses():
    report = run_simulated_annealing(
        _WORKLOAD, _ARCH, _AlwaysRefusesEvaluator(), for_op="test.op", metric="latency_cycles",
        max_iterations=10,
    )
    assert report.skipped_not_expressible == 10
    assert report.best is None
    assert report.best_result is None
