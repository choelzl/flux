"""Unit tests for flux_search_agentic.joint_strategy: propose/observe/done control flow, LLM-
response parsing/validation/fallback over a two-field (width, size_kb) proposal, and
run_agentic_joint_search's driver loop — against a scripted fake LLMProposer and a fake evaluator
with a known landscape (mirroring D26's real "smaller-but-feasible wins on energy, wider wins on
latency" shape), no real Ollama involved. See tests/integration/test_search_agentic_joint_live.py
for the real-LLM, real-evaluator version.
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
    AgenticJointStrategy,
    JointSearchState,
    run_agentic_joint_search,
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
_VALID_WIDTHS = [4, 32]
_VALID_SIZES = [1.0, 1.25, 64.0]
_INFEASIBLE_SIZE = 1.0  # any width, mirroring D26's real per-workload (not per-width) floor


def _result(value: float) -> Result:
    return Result(
        metrics={"energy_pj": Estimate(value=value, ci_low=value, ci_high=value, unit="pJ", method=Method.ANALYTIC)},
        validity=Validity(ok=True, checker_version="test"),
        domain=Domain(in_domain=False),
        bottleneck=Bottleneck(limiter=Limiter.MEMORY),
        provenance=Provenance(evaluator="fake@0.0.0", inputs={}),
        escalation=Escalation(recommended=False),
    )


class _SeparableEvaluator:
    """Mirrors D26's real, checked finding: the width and memory-size axes are separable — cost
    falls with width and rises with size, independently, with a size-only feasibility floor.
    """

    def evaluate(self, candidate: Candidate, budget: Budget, metrics: frozenset[str]) -> Result:
        pe_array = next(n for n in candidate.arch["hierarchy"] if n["level"] == "pe_array")
        gbuf = next(n for n in candidate.arch["hierarchy"] if n["level"] == "gbuf")
        width = pe_array["attrs"]["dims"]["X"]
        size_kb = gbuf["attrs"]["size_kb"]
        if size_kb < _INFEASIBLE_SIZE + 0.01:
            raise ValueError("does not fit within the full memory hierarchy")
        return _result(1000.0 / width + size_kb)


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


def _strategy(responses, **kwargs) -> AgenticJointStrategy:
    return AgenticJointStrategy(
        _WORKLOAD, _ARCH, _ScriptedProposer(responses),
        metric="energy_pj", level="gbuf", valid_widths=_VALID_WIDTHS,
        valid_sizes_kb=_VALID_SIZES, **kwargs,
    )


def test_propose_requires_k_equals_one():
    strategy = _strategy(['{"width": 32, "size_kb": 1.25}'])
    state = JointSearchState(workload=_WORKLOAD, base_arch=_ARCH)
    with pytest.raises(ValueError):
        strategy.propose(state, k=2)


def test_propose_before_observe_raises():
    strategy = _strategy(['{"width": 32, "size_kb": 1.25}'])
    state = JointSearchState(workload=_WORKLOAD, base_arch=_ARCH)
    strategy.propose(state, k=1)
    with pytest.raises(RuntimeError):
        strategy.propose(state, k=1)


def test_observe_before_propose_raises():
    strategy = _strategy(['{"width": 32, "size_kb": 1.25}'])
    with pytest.raises(RuntimeError):
        strategy.observe([_result(1.0)])


def test_valid_json_proposal_is_used_directly_without_fallback():
    strategy = _strategy(['{"width": 32, "size_kb": 1.25}'])
    state = JointSearchState(workload=_WORKLOAD, base_arch=_ARCH)
    strategy.propose(state, k=1)
    strategy.observe([_result(42.0)])
    assert strategy.evaluated[0].used_fallback is False
    assert strategy.evaluated[0].candidate.width == 32
    assert strategy.evaluated[0].candidate.size_kb == 1.25


def test_markdown_fenced_json_is_parsed():
    strategy = _strategy(['```json\n{"width": 4, "size_kb": 64.0}\n```'])
    state = JointSearchState(workload=_WORKLOAD, base_arch=_ARCH)
    strategy.propose(state, k=1)
    strategy.observe([_result(1.0)])
    assert strategy.evaluated[0].used_fallback is False
    assert strategy.evaluated[0].candidate.width == 4
    assert strategy.evaluated[0].candidate.size_kb == 64.0


def test_invalid_json_falls_back_to_random_unvisited():
    strategy = _strategy(["not json at all"], seed=1)
    state = JointSearchState(workload=_WORKLOAD, base_arch=_ARCH)
    strategy.propose(state, k=1)
    strategy.observe([_result(1.0)])
    evaluated = strategy.evaluated[0]
    assert evaluated.used_fallback is True
    assert "not valid JSON" in evaluated.fallback_reason


def test_pair_outside_candidate_set_falls_back():
    strategy = _strategy(['{"width": 999, "size_kb": 1.25}'], seed=1)
    state = JointSearchState(workload=_WORKLOAD, base_arch=_ARCH)
    strategy.propose(state, k=1)
    strategy.observe([_result(1.0)])
    evaluated = strategy.evaluated[0]
    assert evaluated.used_fallback is True
    assert "is not one of the valid candidates" in evaluated.fallback_reason


def test_valid_width_with_invalid_size_falls_back():
    """A width that IS in valid_widths paired with a size that ISN'T a valid combination for
    this candidate set must still be rejected -- exercises the (width, size) tuple check, not
    just each field independently."""
    strategy = _strategy(['{"width": 32, "size_kb": 999.0}'], seed=1)
    state = JointSearchState(workload=_WORKLOAD, base_arch=_ARCH)
    strategy.propose(state, k=1)
    strategy.observe([_result(1.0)])
    assert strategy.evaluated[0].used_fallback is True


def test_non_numeric_width_is_rejected():
    strategy = _strategy(['{"width": "wide", "size_kb": 1.25}'], seed=1)
    state = JointSearchState(workload=_WORKLOAD, base_arch=_ARCH)
    strategy.propose(state, k=1)
    strategy.observe([_result(1.0)])
    assert strategy.evaluated[0].used_fallback is True


def test_boolean_width_is_rejected_not_coerced():
    strategy = _strategy(['{"width": true, "size_kb": 1.25}'], seed=1)
    state = JointSearchState(workload=_WORKLOAD, base_arch=_ARCH)
    strategy.propose(state, k=1)
    strategy.observe([_result(1.0)])
    assert strategy.evaluated[0].used_fallback is True


def test_repeated_already_visited_pair_falls_back():
    strategy = _strategy(
        ['{"width": 32, "size_kb": 1.25}', '{"width": 32, "size_kb": 1.25}'], seed=1,
    )
    state = JointSearchState(workload=_WORKLOAD, base_arch=_ARCH)
    strategy.propose(state, k=1)
    strategy.observe([_result(1.0)])
    strategy.propose(state, k=1)
    strategy.observe([_result(2.0)])
    assert strategy.evaluated[0].used_fallback is False
    assert strategy.evaluated[1].used_fallback is True
    assert "already-evaluated" in strategy.evaluated[1].fallback_reason
    pair1 = (strategy.evaluated[1].candidate.width, strategy.evaluated[1].candidate.size_kb)
    assert pair1 != (32, 1.25)


def test_done_defaults_to_the_full_grid_size():
    strategy = _strategy(["not json, forces fallback"], seed=2)
    state = JointSearchState(workload=_WORKLOAD, base_arch=_ARCH)
    rounds = 0
    while not strategy.done() and rounds < 10:
        strategy.propose(state, k=1)
        strategy.observe([_result(1.0)])
        rounds += 1
    assert strategy.done()
    assert rounds == len(_VALID_WIDTHS) * len(_VALID_SIZES)


def test_done_after_explicit_max_iterations():
    strategy = _strategy(['{"width": 32, "size_kb": 1.25}'], max_iterations=2)
    state = JointSearchState(workload=_WORKLOAD, base_arch=_ARCH)
    for _ in range(2):
        assert not strategy.done()
        strategy.propose(state, k=1)
        strategy.observe([_result(1.0)])
    assert strategy.done()


def test_infeasible_candidate_is_recorded_as_a_rejected_move_not_a_crash():
    strategy = _strategy(['{"width": 32, "size_kb": 1.0}'], max_iterations=1)
    state = JointSearchState(workload=_WORKLOAD, base_arch=_ARCH)
    strategy.propose(state, k=1)
    strategy.observe([ValueError("does not fit within the full memory hierarchy")])
    assert strategy.evaluated[0].result is None
    assert strategy.best is None


def test_prompt_includes_both_axes_history_and_infeasible_marker():
    proposer = _ScriptedProposer(
        ['{"width": 32, "size_kb": 1.0}', '{"width": 32, "size_kb": 1.25}'],
    )
    strategy = AgenticJointStrategy(
        _WORKLOAD, _ARCH, proposer, metric="energy_pj", level="gbuf",
        valid_widths=_VALID_WIDTHS, valid_sizes_kb=_VALID_SIZES,
    )
    state = JointSearchState(workload=_WORKLOAD, base_arch=_ARCH)
    strategy.propose(state, k=1)
    strategy.observe([ValueError("does not fit within the full memory hierarchy")])
    strategy.propose(state, k=1)
    assert "[4, 32]" in proposer.prompts[0]
    assert "[1.0, 1.25, 64.0]" in proposer.prompts[0]
    assert "(none yet)" in proposer.prompts[0]
    assert "width=32, size_kb=1" in proposer.prompts[1]
    assert "INFEASIBLE" in proposer.prompts[1]


def test_run_agentic_joint_search_finds_the_true_minimum_on_the_known_landscape():
    """Full coverage of all 6 (width, size) pairs guarantees the true minimum is found: width=32
    (fastest/most energy-efficient) x size_kb=1.25 (smallest feasible) -- the same
    deterministic-despite-LLM argument every other axis's own test file uses.
    """
    report = run_agentic_joint_search(
        _WORKLOAD, _ARCH, _SeparableEvaluator(),
        _ScriptedProposer([
            '{"width": 4, "size_kb": 1.0}', '{"width": 4, "size_kb": 1.25}',
            '{"width": 4, "size_kb": 64.0}', '{"width": 32, "size_kb": 1.0}',
            '{"width": 32, "size_kb": 1.25}', '{"width": 32, "size_kb": 64.0}',
        ]),
        metric="energy_pj", level="gbuf", valid_widths=_VALID_WIDTHS, valid_sizes_kb=_VALID_SIZES,
    )
    assert report.iterations == 6
    assert report.best is not None
    assert report.best.width == 32
    assert report.best.size_kb == 1.25
    assert report.skipped_infeasible == 2  # size_kb=1.0 at both widths
    assert report.fallback_count == 0


def test_run_agentic_joint_search_treats_evaluator_refusal_as_skipped():
    report = run_agentic_joint_search(
        _WORKLOAD, _ARCH, _AlwaysRefusesEvaluator(), _ScriptedProposer(['{"width": 32, "size_kb": 1.25}']),
        metric="energy_pj", level="gbuf", valid_widths=_VALID_WIDTHS, valid_sizes_kb=_VALID_SIZES,
        max_iterations=2,
    )
    assert report.skipped_infeasible == 2
    assert report.best is None


def test_report_to_dict_is_json_safe():
    import json

    report = run_agentic_joint_search(
        _WORKLOAD, _ARCH, _SeparableEvaluator(), _ScriptedProposer(['{"width": 32, "size_kb": 1.25}']),
        metric="energy_pj", level="gbuf", valid_widths=_VALID_WIDTHS, valid_sizes_kb=_VALID_SIZES,
        max_iterations=1,
    )
    d = report.to_dict()
    json.dumps(d)  # raises if a dataclass leaked through unconverted
    assert d["best"]["width"] == 32
    assert d["best"]["size_kb"] == 1.25
    assert d["evaluated"][0]["used_fallback"] is False


def test_zero_wall_clock_budget_stops_before_any_real_proposal():
    """docs/decisions.md D73 — the shared engine's wall-clock budget, confirmed reachable through
    this axis too (the core timing logic itself is exercised once, in
    test_search_agentic_strategy.py — this is a thin, per-axis confirmation)."""
    proposer = _ScriptedProposer(['{"width": 32, "size_kb": 1.25}'])
    report = run_agentic_joint_search(
        _WORKLOAD, _ARCH, _SeparableEvaluator(), proposer,
        metric="energy_pj", level="gbuf", valid_widths=_VALID_WIDTHS, valid_sizes_kb=_VALID_SIZES,
        max_iterations=10, wall_clock_budget_s=0.0,
    )
    assert proposer.calls == 0
    assert report.stopped_early is True
    assert report.iterations == 0
