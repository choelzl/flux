"""Unit tests for flux_search_agentic.architecture_strategy: propose/observe/done control flow,
LLM-response parsing/validation/fallback, and run_agentic_architecture_search's driver loop —
against a scripted fake LLMProposer and a fake evaluator with a known landscape, no real Ollama
involved. See tests/integration/test_search_agentic_architecture_live.py for the real-LLM,
real-evaluator version, validated against real ZigZag numbers.
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
    AgenticArchitectureWidthStrategy,
    ArchitectureSearchState,
    run_agentic_architecture_search,
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
_VALID_WIDTHS = [4, 8, 16, 32]


def _result(value: float) -> Result:
    return Result(
        metrics={"latency_cycles": Estimate(value=value, ci_low=value, ci_high=value, unit="cycles", method=Method.ANALYTIC)},
        validity=Validity(ok=True, checker_version="test"),
        domain=Domain(in_domain=False),
        bottleneck=Bottleneck(limiter=Limiter.COMPUTE),
        provenance=Provenance(evaluator="fake@0.0.0", inputs={}),
        escalation=Escalation(recommended=False),
    )


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


class _WidthProportionalEvaluator:
    """latency = 1000 / width — mirrors the real, monotonic ZigZag landscape for this axis
    (docs/decisions.md D13's own module docstring: wider is strictly faster, no inversion)."""

    def evaluate(self, candidate: Candidate, budget: Budget, metrics: frozenset[str]) -> Result:
        width = candidate.arch["hierarchy"][-1]["attrs"]["dims"]["X"]
        return _result(1000.0 / width)


class _AlwaysRefusesEvaluator:
    def evaluate(self, candidate: Candidate, budget: Budget, metrics: frozenset[str]) -> Result:
        raise ValueError("not_expressible_in: [fake]")


def _strategy(responses, **kwargs) -> AgenticArchitectureWidthStrategy:
    return AgenticArchitectureWidthStrategy(
        _WORKLOAD, _ARCH, _ScriptedProposer(responses),
        metric="latency_cycles", valid_widths=_VALID_WIDTHS, **kwargs,
    )


def test_propose_requires_k_equals_one():
    strategy = _strategy(['{"width": 8}'])
    state = ArchitectureSearchState(workload=_WORKLOAD, base_arch=_ARCH)
    with pytest.raises(ValueError):
        strategy.propose(state, k=2)


def test_propose_before_observe_raises():
    strategy = _strategy(['{"width": 8}'])
    state = ArchitectureSearchState(workload=_WORKLOAD, base_arch=_ARCH)
    strategy.propose(state, k=1)
    with pytest.raises(RuntimeError):
        strategy.propose(state, k=1)


def test_observe_before_propose_raises():
    strategy = _strategy(['{"width": 8}'])
    with pytest.raises(RuntimeError):
        strategy.observe([_result(1.0)])


def test_valid_json_proposal_is_used_directly_without_fallback():
    strategy = _strategy(['{"width": 16}'])
    state = ArchitectureSearchState(workload=_WORKLOAD, base_arch=_ARCH)
    strategy.propose(state, k=1)
    strategy.observe([_result(42.0)])
    assert strategy.evaluated[0].used_fallback is False
    assert strategy.evaluated[0].candidate.width == 16


def test_markdown_fenced_json_is_parsed():
    strategy = _strategy(['```json\n{"width": 32}\n```'])
    state = ArchitectureSearchState(workload=_WORKLOAD, base_arch=_ARCH)
    strategy.propose(state, k=1)
    strategy.observe([_result(1.0)])
    assert strategy.evaluated[0].used_fallback is False
    assert strategy.evaluated[0].candidate.width == 32


def test_invalid_json_falls_back_to_random_unvisited():
    strategy = _strategy(["not json at all"], seed=1)
    state = ArchitectureSearchState(workload=_WORKLOAD, base_arch=_ARCH)
    strategy.propose(state, k=1)
    strategy.observe([_result(1.0)])
    evaluated = strategy.evaluated[0]
    assert evaluated.used_fallback is True
    assert "not valid JSON" in evaluated.fallback_reason


def test_width_outside_candidate_set_falls_back():
    strategy = _strategy(['{"width": 64}'], seed=1)
    state = ArchitectureSearchState(workload=_WORKLOAD, base_arch=_ARCH)
    strategy.propose(state, k=1)
    strategy.observe([_result(1.0)])
    evaluated = strategy.evaluated[0]
    assert evaluated.used_fallback is True
    assert "is not one of the valid candidates" in evaluated.fallback_reason


def test_boolean_width_is_rejected_not_coerced():
    """bool is an int subclass in Python — {"width": true} must not silently become width=1."""
    strategy = _strategy(['{"width": true}'], seed=1)
    state = ArchitectureSearchState(workload=_WORKLOAD, base_arch=_ARCH)
    strategy.propose(state, k=1)
    strategy.observe([_result(1.0)])
    assert strategy.evaluated[0].used_fallback is True


def test_repeated_already_visited_width_falls_back():
    strategy = _strategy(['{"width": 16}', '{"width": 16}'], seed=1)
    state = ArchitectureSearchState(workload=_WORKLOAD, base_arch=_ARCH)
    strategy.propose(state, k=1)
    strategy.observe([_result(1.0)])
    strategy.propose(state, k=1)
    strategy.observe([_result(2.0)])
    assert strategy.evaluated[0].used_fallback is False
    assert strategy.evaluated[1].used_fallback is True
    assert "already-evaluated" in strategy.evaluated[1].fallback_reason
    assert strategy.evaluated[1].candidate.width != 16


def test_done_defaults_to_the_size_of_the_candidate_set():
    strategy = _strategy(["not json, forces fallback"], seed=2)
    state = ArchitectureSearchState(workload=_WORKLOAD, base_arch=_ARCH)
    rounds = 0
    while not strategy.done() and rounds < 10:
        strategy.propose(state, k=1)
        strategy.observe([_result(1.0)])
        rounds += 1
    assert strategy.done()
    assert rounds == len(_VALID_WIDTHS)


def test_done_after_explicit_max_iterations():
    strategy = _strategy(['{"width": 8}'], max_iterations=2)
    state = ArchitectureSearchState(workload=_WORKLOAD, base_arch=_ARCH)
    for _ in range(2):
        assert not strategy.done()
        strategy.propose(state, k=1)
        strategy.observe([_result(1.0)])
    assert strategy.done()


def test_refused_candidate_is_recorded_as_a_rejected_move_not_a_crash():
    strategy = _strategy(['{"width": 8}'], max_iterations=1)
    state = ArchitectureSearchState(workload=_WORKLOAD, base_arch=_ARCH)
    strategy.propose(state, k=1)
    strategy.observe([ValueError("not_expressible_in: [fake]")])
    assert strategy.evaluated[0].result is None
    assert strategy.best is None


def test_run_agentic_architecture_search_finds_the_true_minimum_on_the_known_landscape():
    """Full coverage of all 4 candidate widths guarantees the true minimum (width=32, matching
    the real ZigZag landscape's own direction) is found, the same deterministic-despite-LLM
    argument test_search_agentic_live.py uses for the mapping axis.
    """
    report = run_agentic_architecture_search(
        _WORKLOAD, _ARCH, _WidthProportionalEvaluator(),
        _ScriptedProposer(['{"width": 4}', '{"width": 8}', '{"width": 16}', '{"width": 32}']),
        metric="latency_cycles", valid_widths=_VALID_WIDTHS,
    )
    assert report.iterations == 4
    assert report.best is not None
    assert report.best.width == 32
    assert report.fallback_count == 0


def test_run_agentic_architecture_search_treats_evaluator_refusal_as_skipped():
    report = run_agentic_architecture_search(
        _WORKLOAD, _ARCH, _AlwaysRefusesEvaluator(), _ScriptedProposer(['{"width": 8}']),
        metric="latency_cycles", valid_widths=_VALID_WIDTHS, max_iterations=2,
    )
    assert report.skipped_not_expressible == 2
    assert report.best is None


def test_prompt_includes_history_and_candidate_set():
    proposer = _ScriptedProposer(['{"width": 8}', '{"width": 16}'])
    strategy = AgenticArchitectureWidthStrategy(
        _WORKLOAD, _ARCH, proposer, metric="latency_cycles", valid_widths=_VALID_WIDTHS,
    )
    state = ArchitectureSearchState(workload=_WORKLOAD, base_arch=_ARCH)
    strategy.propose(state, k=1)
    strategy.observe([_result(125.0)])
    strategy.propose(state, k=1)
    assert "[4, 8, 16, 32]" in proposer.prompts[0]
    assert "(none yet)" in proposer.prompts[0]
    assert "width=8" in proposer.prompts[1]
    assert "125" in proposer.prompts[1]


def test_report_to_dict_is_json_safe():
    import json

    report = run_agentic_architecture_search(
        _WORKLOAD, _ARCH, _WidthProportionalEvaluator(), _ScriptedProposer(['{"width": 32}']),
        metric="latency_cycles", valid_widths=_VALID_WIDTHS, max_iterations=1,
    )
    d = report.to_dict()
    json.dumps(d)  # raises if a dataclass leaked through unconverted
    assert d["best"]["width"] == 32
    assert d["best_result"]["metrics"]["latency_cycles"]["value"] == pytest.approx(1000.0 / 32)
    assert d["evaluated"][0]["used_fallback"] is False


def test_zero_wall_clock_budget_stops_before_any_real_proposal():
    """docs/decisions.md D73 — the shared engine's wall-clock budget, confirmed reachable through
    this axis too (the core timing logic itself is exercised once, in
    test_search_agentic_strategy.py — this is a thin, per-axis confirmation)."""
    proposer = _ScriptedProposer(['{"width": 32}'])
    report = run_agentic_architecture_search(
        _WORKLOAD, _ARCH, _WidthProportionalEvaluator(), proposer,
        metric="latency_cycles", valid_widths=_VALID_WIDTHS, max_iterations=10,
        wall_clock_budget_s=0.0,
    )
    assert proposer.calls == 0
    assert report.stopped_early is True
    assert report.iterations == 0
