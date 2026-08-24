"""Unit tests for flux_search_agentic.memory_strategy: propose/observe/done control flow, LLM-
response parsing/validation/fallback, and run_agentic_memory_size_search's driver loop — against
a scripted fake LLMProposer and a fake evaluator with a known landscape (mirroring D26's real
"below a floor it's infeasible, above it energy rises with size" shape), no real Ollama involved.
See tests/integration/test_search_agentic_memory_live.py for the real-LLM, real-evaluator version.
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
from flux_search_agentic import (
    AgenticMemorySizeStrategy,
    MemorySearchState,
    run_agentic_memory_size_search,
)

_ARCH = {
    "schema_version": "0.1.0",
    "id": "test/arch",
    "hierarchy": [
        {"level": "dram", "class": "memory", "attrs": {"size_kb": 1_000_000}},
        {"level": "gbuf", "class": "memory", "attrs": {"size_kb": 512}},
        {"level": "pe_array", "class": "compute", "attrs": {"dims": {"X": 8}}},
    ],
}
_WORKLOAD = {"schema_version": "0.1.0", "id": "test/wl", "tensors": [], "ops": []}
_VALID_SIZES = [1.0, 1.25, 2.0, 64.0]
_INFEASIBLE_FLOOR = 1.25  # sizes strictly below this are "too small to fit" in the fake evaluator


def _result(value: float) -> Result:
    return Result(
        metrics={"energy_pj": Estimate(value=value, ci_low=value, ci_high=value, unit="pJ", method=Method.ANALYTIC)},
        validity=Validity(ok=True, checker_version="test"),
        domain=Domain(in_domain=False),
        bottleneck=Bottleneck(limiter=Limiter.MEMORY),
        provenance=Provenance(evaluator="fake@0.0.0", inputs={}),
        escalation=Escalation(recommended=False),
    )


class _SmallestFeasibleWinsEvaluator:
    """Mirrors D26's real landscape: below _INFEASIBLE_FLOOR the mapper "rejects" the candidate;
    at and above it, cost rises monotonically with size — the smallest feasible size wins.
    """

    def evaluate(self, candidate: Candidate, budget: Budget, metrics: frozenset[str]) -> Result:
        gbuf = next(n for n in candidate.arch["hierarchy"] if n["level"] == "gbuf")
        size_kb = gbuf["attrs"]["size_kb"]
        if size_kb < _INFEASIBLE_FLOOR:
            raise ValueError("does not fit within the full memory hierarchy")
        return _result(size_kb)


class _AlwaysRefusesEvaluator:
    def evaluate(self, candidate: Candidate, budget: Budget, metrics: frozenset[str]) -> Result:
        raise ValueError("does not fit within the full memory hierarchy")


class _ScriptedProposer:
    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls = 0
        self.prompts: list[str] = []

    def propose(self, prompt: str) -> str:
        self.prompts.append(prompt)
        response = self._responses[min(self.calls, len(self._responses) - 1)]
        self.calls += 1
        return response


def _strategy(responses, **kwargs) -> AgenticMemorySizeStrategy:
    return AgenticMemorySizeStrategy(
        _WORKLOAD, _ARCH, _ScriptedProposer(responses),
        metric="energy_pj", level="gbuf", valid_sizes_kb=_VALID_SIZES, **kwargs,
    )


def test_propose_requires_k_equals_one():
    strategy = _strategy(['{"size_kb": 2.0}'])
    state = MemorySearchState(workload=_WORKLOAD, base_arch=_ARCH)
    with pytest.raises(ValueError):
        strategy.propose(state, k=2)


def test_propose_before_observe_raises():
    strategy = _strategy(['{"size_kb": 2.0}'])
    state = MemorySearchState(workload=_WORKLOAD, base_arch=_ARCH)
    strategy.propose(state, k=1)
    with pytest.raises(RuntimeError):
        strategy.propose(state, k=1)


def test_observe_before_propose_raises():
    strategy = _strategy(['{"size_kb": 2.0}'])
    with pytest.raises(RuntimeError):
        strategy.observe([_result(1.0)])


def test_valid_json_proposal_is_used_directly_without_fallback():
    strategy = _strategy(['{"size_kb": 2.0}'])
    state = MemorySearchState(workload=_WORKLOAD, base_arch=_ARCH)
    strategy.propose(state, k=1)
    strategy.observe([_result(42.0)])
    assert strategy.evaluated[0].used_fallback is False
    assert strategy.evaluated[0].candidate.size_kb == 2.0


def test_integer_size_proposal_is_accepted_as_a_float():
    """{"size_kb": 2} (a JSON integer, not 2.0) must match the float candidate 2.0."""
    strategy = _strategy(['{"size_kb": 2}'])
    state = MemorySearchState(workload=_WORKLOAD, base_arch=_ARCH)
    strategy.propose(state, k=1)
    strategy.observe([_result(1.0)])
    assert strategy.evaluated[0].used_fallback is False
    assert strategy.evaluated[0].candidate.size_kb == 2.0


def test_markdown_fenced_json_is_parsed():
    strategy = _strategy(['```json\n{"size_kb": 64.0}\n```'])
    state = MemorySearchState(workload=_WORKLOAD, base_arch=_ARCH)
    strategy.propose(state, k=1)
    strategy.observe([_result(1.0)])
    assert strategy.evaluated[0].used_fallback is False
    assert strategy.evaluated[0].candidate.size_kb == 64.0


def test_invalid_json_falls_back_to_random_unvisited():
    strategy = _strategy(["not json at all"], seed=1)
    state = MemorySearchState(workload=_WORKLOAD, base_arch=_ARCH)
    strategy.propose(state, k=1)
    strategy.observe([_result(1.0)])
    evaluated = strategy.evaluated[0]
    assert evaluated.used_fallback is True
    assert "not valid JSON" in evaluated.fallback_reason


def test_size_outside_candidate_set_falls_back():
    strategy = _strategy(['{"size_kb": 999.0}'], seed=1)
    state = MemorySearchState(workload=_WORKLOAD, base_arch=_ARCH)
    strategy.propose(state, k=1)
    strategy.observe([_result(1.0)])
    evaluated = strategy.evaluated[0]
    assert evaluated.used_fallback is True
    assert "is not one of the valid candidates" in evaluated.fallback_reason


def test_non_numeric_size_is_rejected():
    strategy = _strategy(['{"size_kb": "big"}'], seed=1)
    state = MemorySearchState(workload=_WORKLOAD, base_arch=_ARCH)
    strategy.propose(state, k=1)
    strategy.observe([_result(1.0)])
    assert strategy.evaluated[0].used_fallback is True


def test_boolean_size_is_rejected_not_coerced():
    """bool is an int subclass in Python — {"size_kb": true} must not silently become size 1.0."""
    strategy = _strategy(['{"size_kb": true}'], seed=1)
    state = MemorySearchState(workload=_WORKLOAD, base_arch=_ARCH)
    strategy.propose(state, k=1)
    strategy.observe([_result(1.0)])
    assert strategy.evaluated[0].used_fallback is True


def test_repeated_already_visited_size_falls_back():
    strategy = _strategy(['{"size_kb": 2.0}', '{"size_kb": 2.0}'], seed=1)
    state = MemorySearchState(workload=_WORKLOAD, base_arch=_ARCH)
    strategy.propose(state, k=1)
    strategy.observe([_result(1.0)])
    strategy.propose(state, k=1)
    strategy.observe([_result(2.0)])
    assert strategy.evaluated[0].used_fallback is False
    assert strategy.evaluated[1].used_fallback is True
    assert "already-evaluated" in strategy.evaluated[1].fallback_reason
    assert strategy.evaluated[1].candidate.size_kb != 2.0


def test_done_defaults_to_the_size_of_the_candidate_set():
    strategy = _strategy(["not json, forces fallback"], seed=2)
    state = MemorySearchState(workload=_WORKLOAD, base_arch=_ARCH)
    rounds = 0
    while not strategy.done() and rounds < 10:
        strategy.propose(state, k=1)
        strategy.observe([_result(1.0)])
        rounds += 1
    assert strategy.done()
    assert rounds == len(_VALID_SIZES)


def test_done_after_explicit_max_iterations():
    strategy = _strategy(['{"size_kb": 2.0}'], max_iterations=2)
    state = MemorySearchState(workload=_WORKLOAD, base_arch=_ARCH)
    for _ in range(2):
        assert not strategy.done()
        strategy.propose(state, k=1)
        strategy.observe([_result(1.0)])
    assert strategy.done()


def test_infeasible_candidate_is_recorded_as_a_rejected_move_not_a_crash():
    strategy = _strategy(['{"size_kb": 1.0}'], max_iterations=1)
    state = MemorySearchState(workload=_WORKLOAD, base_arch=_ARCH)
    strategy.propose(state, k=1)
    strategy.observe([ValueError("does not fit within the full memory hierarchy")])
    assert strategy.evaluated[0].result is None
    assert strategy.best is None


def test_prompt_includes_history_and_candidate_set_and_infeasible_marker():
    proposer = _ScriptedProposer(['{"size_kb": 1.0}', '{"size_kb": 2.0}'])
    strategy = AgenticMemorySizeStrategy(
        _WORKLOAD, _ARCH, proposer, metric="energy_pj", level="gbuf", valid_sizes_kb=_VALID_SIZES,
    )
    state = MemorySearchState(workload=_WORKLOAD, base_arch=_ARCH)
    strategy.propose(state, k=1)
    strategy.observe([ValueError("does not fit within the full memory hierarchy")])
    strategy.propose(state, k=1)
    assert "[1.0, 1.25, 2.0, 64.0]" in proposer.prompts[0]
    assert "(none yet)" in proposer.prompts[0]
    assert "'gbuf'" in proposer.prompts[1] or "gbuf" in proposer.prompts[1]
    assert "size_kb=1" in proposer.prompts[1]
    assert "INFEASIBLE" in proposer.prompts[1]


def test_run_agentic_memory_size_search_finds_the_true_minimum_on_the_known_landscape():
    """Full coverage of all 4 candidate sizes guarantees the true minimum is found: the
    smallest *feasible* size (1.25), not the numerically smallest tried (1.0, which fails) — the
    same deterministic-despite-LLM argument every other axis's own test file uses.
    """
    report = run_agentic_memory_size_search(
        _WORKLOAD, _ARCH, _SmallestFeasibleWinsEvaluator(),
        _ScriptedProposer(['{"size_kb": 1.0}', '{"size_kb": 1.25}', '{"size_kb": 2.0}', '{"size_kb": 64.0}']),
        metric="energy_pj", level="gbuf", valid_sizes_kb=_VALID_SIZES,
    )
    assert report.iterations == 4
    assert report.best is not None
    assert report.best.size_kb == 1.25
    assert report.skipped_infeasible == 1
    assert report.fallback_count == 0


def test_run_agentic_memory_size_search_treats_evaluator_refusal_as_skipped():
    report = run_agentic_memory_size_search(
        _WORKLOAD, _ARCH, _AlwaysRefusesEvaluator(), _ScriptedProposer(['{"size_kb": 2.0}']),
        metric="energy_pj", level="gbuf", valid_sizes_kb=_VALID_SIZES, max_iterations=2,
    )
    assert report.skipped_infeasible == 2
    assert report.best is None


def test_report_to_dict_is_json_safe():
    import json

    report = run_agentic_memory_size_search(
        _WORKLOAD, _ARCH, _SmallestFeasibleWinsEvaluator(), _ScriptedProposer(['{"size_kb": 1.25}']),
        metric="energy_pj", level="gbuf", valid_sizes_kb=_VALID_SIZES, max_iterations=1,
    )
    d = report.to_dict()
    json.dumps(d)  # raises if a dataclass leaked through unconverted
    assert d["best"]["size_kb"] == 1.25
    assert d["best_result"]["metrics"]["energy_pj"]["value"] == pytest.approx(1.25)
    assert d["evaluated"][0]["used_fallback"] is False


def test_zero_wall_clock_budget_stops_before_any_real_proposal():
    """docs/decisions.md D73 — the shared engine's wall-clock budget, confirmed reachable through
    this axis too (the core timing logic itself is exercised once, in
    test_search_agentic_strategy.py — this is a thin, per-axis confirmation)."""
    proposer = _ScriptedProposer(['{"size_kb": 1.25}'])
    report = run_agentic_memory_size_search(
        _WORKLOAD, _ARCH, _SmallestFeasibleWinsEvaluator(), proposer,
        metric="energy_pj", level="gbuf", valid_sizes_kb=_VALID_SIZES, max_iterations=10,
        wall_clock_budget_s=0.0,
    )
    assert proposer.calls == 0
    assert report.stopped_early is True
    assert report.iterations == 0
