"""Unit tests for flux_search_agentic.noc_strategy: propose/observe/done control flow,
LLM-response parsing/validation/fallback, and run_agentic_noc_topology_search's driver loop —
against a scripted fake LLMProposer and a fake evaluator with a known landscape, no real Ollama
or Booksim2 involved. See tests/integration/test_search_agentic_noc_live.py for the real-LLM,
real-Booksim2 version, validated against real Booksim2 numbers.
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
    AgenticNocTopologyStrategy,
    NocSearchState,
    run_agentic_noc_topology_search,
)

_BASE_ARCH = {
    "schema_version": "0.1.0",
    "id": "test/noc-arch",
    "hierarchy": [{"level": "router_fabric", "class": "interconnect", "attrs": {}}],
    "interconnect": {"noc": {"topology": "mesh", "dimensions": [8, 8], "routing_function": "dor"}},
}
_WORKLOAD = {"schema_version": "0.1.0", "id": "test/wl", "tensors": [], "ops": []}
_VALID_VARIANTS = [("mesh", [64]), ("mesh", [8, 8]), ("mesh", [4, 4, 4]), ("mesh", [2, 2, 2, 2, 2, 2])]


def _result(value: float) -> Result:
    return Result(
        metrics={"latency_cycles": Estimate(value=value, ci_low=value, ci_high=value, unit="cycles", method=Method.SIMULATED)},
        validity=Validity(ok=True, checker_version="test"),
        domain=Domain(in_domain=False),
        bottleneck=Bottleneck(limiter=Limiter.NOC),
        provenance=Provenance(evaluator="fake-noc@0.0.0", inputs={}),
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


class _DimensionalityProportionalEvaluator:
    """latency = 1000 / len(dims) — mirrors the real, monotonic Booksim2 landscape for this axis
    (docs/decisions.md D14's own module docstring: more dimensions is strictly faster at
    fixed total node count, no inversion)."""

    def evaluate(self, candidate: Candidate, budget: Budget, metrics: frozenset[str]) -> Result:
        dims = candidate.arch["interconnect"]["noc"]["dimensions"]
        return _result(1000.0 / len(dims))


class _AlwaysRefusesEvaluator:
    def evaluate(self, candidate: Candidate, budget: Budget, metrics: frozenset[str]) -> Result:
        raise ValueError("not_expressible_in: [fake]")


def _strategy(responses, **kwargs) -> AgenticNocTopologyStrategy:
    return AgenticNocTopologyStrategy(
        _WORKLOAD, _BASE_ARCH, _ScriptedProposer(responses),
        metric="latency_cycles", valid_variants=_VALID_VARIANTS, **kwargs,
    )


def test_propose_requires_k_equals_one():
    strategy = _strategy(['{"topology": "mesh", "dimensions": [8, 8]}'])
    state = NocSearchState(workload=_WORKLOAD, base_arch=_BASE_ARCH)
    with pytest.raises(ValueError):
        strategy.propose(state, k=2)


def test_propose_before_observe_raises():
    strategy = _strategy(['{"topology": "mesh", "dimensions": [8, 8]}'])
    state = NocSearchState(workload=_WORKLOAD, base_arch=_BASE_ARCH)
    strategy.propose(state, k=1)
    with pytest.raises(RuntimeError):
        strategy.propose(state, k=1)


def test_observe_before_propose_raises():
    strategy = _strategy(['{"topology": "mesh", "dimensions": [8, 8]}'])
    with pytest.raises(RuntimeError):
        strategy.observe([_result(1.0)])


def test_valid_json_proposal_is_used_directly_without_fallback():
    strategy = _strategy(['{"topology": "mesh", "dimensions": [4, 4, 4]}'])
    state = NocSearchState(workload=_WORKLOAD, base_arch=_BASE_ARCH)
    strategy.propose(state, k=1)
    strategy.observe([_result(42.0)])
    assert strategy.evaluated[0].used_fallback is False
    assert strategy.evaluated[0].candidate.dimensions == (4, 4, 4)


def test_markdown_fenced_json_is_parsed():
    strategy = _strategy(['```json\n{"topology": "mesh", "dimensions": [2, 2, 2, 2, 2, 2]}\n```'])
    state = NocSearchState(workload=_WORKLOAD, base_arch=_BASE_ARCH)
    strategy.propose(state, k=1)
    strategy.observe([_result(1.0)])
    assert strategy.evaluated[0].used_fallback is False
    assert strategy.evaluated[0].candidate.dimensions == (2, 2, 2, 2, 2, 2)


def test_invalid_json_falls_back_to_random_unvisited():
    strategy = _strategy(["not json at all"], seed=1)
    state = NocSearchState(workload=_WORKLOAD, base_arch=_BASE_ARCH)
    strategy.propose(state, k=1)
    strategy.observe([_result(1.0)])
    evaluated = strategy.evaluated[0]
    assert evaluated.used_fallback is True
    assert "not valid JSON" in evaluated.fallback_reason


def test_variant_outside_the_valid_set_is_rejected():
    """A proposal naming a real, well-formed (topology, dimensions) pair that the caller simply
    didn't include in valid_variants must fall back, the same as any other out-of-set proposal —
    generic behaviour, not specific to any one topology.
    """
    strategy = _strategy(['{"topology": "mesh", "dimensions": [16, 16]}'], seed=1)  # not in _VALID_VARIANTS
    state = NocSearchState(workload=_WORKLOAD, base_arch=_BASE_ARCH)
    strategy.propose(state, k=1)
    strategy.observe([_result(1.0)])
    evaluated = strategy.evaluated[0]
    assert evaluated.used_fallback is True
    assert "is not one of the valid candidates" in evaluated.fallback_reason


def test_torus_is_proposable_when_included_in_the_valid_set():
    """docs/decisions.md D15/D16: torus is no longer excluded at the strategy level — the
    Booksim2 bug that used to force restricting valid_variants to mesh is fixed. This module
    places no topology-specific restriction of its own; a caller can include torus variants and
    have them accepted directly, no fallback needed.
    """
    variants_with_torus = _VALID_VARIANTS + [("torus", [4, 4, 4])]
    strategy = AgenticNocTopologyStrategy(
        _WORKLOAD, _BASE_ARCH, _ScriptedProposer(['{"topology": "torus", "dimensions": [4, 4, 4]}']),
        metric="latency_cycles", valid_variants=variants_with_torus,
    )
    state = NocSearchState(workload=_WORKLOAD, base_arch=_BASE_ARCH)
    strategy.propose(state, k=1)
    strategy.observe([_result(1.0)])
    evaluated = strategy.evaluated[0]
    assert evaluated.used_fallback is False
    assert evaluated.candidate.topology == "torus"
    assert evaluated.candidate.dimensions == (4, 4, 4)


def test_non_integer_dimensions_falls_back():
    strategy = _strategy(['{"topology": "mesh", "dimensions": ["a", "b"]}'], seed=1)
    state = NocSearchState(workload=_WORKLOAD, base_arch=_BASE_ARCH)
    strategy.propose(state, k=1)
    strategy.observe([_result(1.0)])
    assert strategy.evaluated[0].used_fallback is True


def test_repeated_already_visited_variant_falls_back():
    same = '{"topology": "mesh", "dimensions": [8, 8]}'
    strategy = _strategy([same, same], seed=1)
    state = NocSearchState(workload=_WORKLOAD, base_arch=_BASE_ARCH)
    strategy.propose(state, k=1)
    strategy.observe([_result(1.0)])
    strategy.propose(state, k=1)
    strategy.observe([_result(2.0)])
    assert strategy.evaluated[0].used_fallback is False
    assert strategy.evaluated[1].used_fallback is True
    assert "already-evaluated" in strategy.evaluated[1].fallback_reason
    assert strategy.evaluated[1].candidate.dimensions != (8, 8)


def test_done_defaults_to_the_size_of_the_candidate_set():
    strategy = _strategy(["not json, forces fallback"], seed=2)
    state = NocSearchState(workload=_WORKLOAD, base_arch=_BASE_ARCH)
    rounds = 0
    while not strategy.done() and rounds < 10:
        strategy.propose(state, k=1)
        strategy.observe([_result(1.0)])
        rounds += 1
    assert strategy.done()
    assert rounds == len(_VALID_VARIANTS)


def test_refused_candidate_is_recorded_as_a_rejected_move_not_a_crash():
    strategy = _strategy(['{"topology": "mesh", "dimensions": [8, 8]}'], max_iterations=1)
    state = NocSearchState(workload=_WORKLOAD, base_arch=_BASE_ARCH)
    strategy.propose(state, k=1)
    strategy.observe([ValueError("not_expressible_in: [fake]")])
    assert strategy.evaluated[0].result is None
    assert strategy.best is None


def test_run_agentic_noc_search_finds_the_true_minimum_on_the_known_landscape():
    report = run_agentic_noc_topology_search(
        _WORKLOAD, _BASE_ARCH, _DimensionalityProportionalEvaluator(),
        _ScriptedProposer([
            '{"topology": "mesh", "dimensions": [64]}',
            '{"topology": "mesh", "dimensions": [8, 8]}',
            '{"topology": "mesh", "dimensions": [4, 4, 4]}',
            '{"topology": "mesh", "dimensions": [2, 2, 2, 2, 2, 2]}',
        ]),
        metric="latency_cycles", valid_variants=_VALID_VARIANTS,
    )
    assert report.iterations == 4
    assert report.best is not None
    assert report.best.dimensions == (2, 2, 2, 2, 2, 2)  # most dims -> lowest latency in this fake landscape
    assert report.fallback_count == 0


def test_run_agentic_noc_search_treats_evaluator_refusal_as_skipped():
    report = run_agentic_noc_topology_search(
        _WORKLOAD, _BASE_ARCH, _AlwaysRefusesEvaluator(),
        _ScriptedProposer(['{"topology": "mesh", "dimensions": [8, 8]}']),
        metric="latency_cycles", valid_variants=_VALID_VARIANTS, max_iterations=2,
    )
    assert report.skipped_not_expressible == 2
    assert report.best is None


def test_prompt_includes_history_and_candidate_set():
    proposer = _ScriptedProposer([
        '{"topology": "mesh", "dimensions": [8, 8]}',
        '{"topology": "mesh", "dimensions": [4, 4, 4]}',
    ])
    strategy = AgenticNocTopologyStrategy(
        _WORKLOAD, _BASE_ARCH, proposer, metric="latency_cycles", valid_variants=_VALID_VARIANTS,
    )
    state = NocSearchState(workload=_WORKLOAD, base_arch=_BASE_ARCH)
    strategy.propose(state, k=1)
    strategy.observe([_result(60.0)])
    strategy.propose(state, k=1)
    assert "(none yet)" in proposer.prompts[0]
    assert "dimensions=[8, 8]" in proposer.prompts[1]
    assert "60" in proposer.prompts[1]


def test_report_to_dict_is_json_safe():
    import json

    report = run_agentic_noc_topology_search(
        _WORKLOAD, _BASE_ARCH, _DimensionalityProportionalEvaluator(),
        _ScriptedProposer(['{"topology": "mesh", "dimensions": [2, 2, 2, 2, 2, 2]}']),
        metric="latency_cycles", valid_variants=_VALID_VARIANTS, max_iterations=1,
    )
    d = report.to_dict()
    json.dumps(d)  # raises if a dataclass/tuple leaked through unconverted
    assert d["best"]["topology"] == "mesh"
    assert d["best"]["dimensions"] == [2, 2, 2, 2, 2, 2]
    assert d["evaluated"][0]["used_fallback"] is False


def test_zero_wall_clock_budget_stops_before_any_real_proposal():
    """docs/decisions.md D73 — the shared engine's wall-clock budget, confirmed reachable through
    this axis too (the core timing logic itself is exercised once, in
    test_search_agentic_strategy.py — this is a thin, per-axis confirmation)."""
    proposer = _ScriptedProposer(['{"topology": "mesh", "dimensions": [8, 8]}'])
    report = run_agentic_noc_topology_search(
        _WORKLOAD, _BASE_ARCH, _DimensionalityProportionalEvaluator(), proposer,
        metric="latency_cycles", valid_variants=_VALID_VARIANTS, max_iterations=10,
        wall_clock_budget_s=0.0,
    )
    assert proposer.calls == 0
    assert report.stopped_early is True
    assert report.iterations == 0
