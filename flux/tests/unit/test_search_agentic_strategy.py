"""Unit tests for flux_search_agentic.strategy: propose/observe/done control flow, LLM-response
parsing/validation/fallback, and `run_agentic_search`'s driver loop — against a scripted fake
`LLMProposer` and a fake evaluator with a known landscape, no real Ollama involved. See
tests/integration/test_search_agentic_live.py for the real-LLM, real-evaluator version, validated
against exhaustive search's proven optimum.
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
from flux_search_agentic import AgenticMappingStrategy, SearchState, run_agentic_search

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


class _ScriptedProposer:
    """Returns each of `responses` in order, then repeats the last one — deterministic, no real
    LLM call, so parsing/fallback behaviour is tested against exact known inputs.
    """

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls = 0
        self.prompts: list[str] = []

    def propose(self, prompt: str) -> str:
        self.prompts.append(prompt)
        response = self._responses[min(self.calls, len(self._responses) - 1)]
        self.calls += 1
        return response


class _KnownLandscapeEvaluator:
    """True global minimum: spatial_dim='C', temporal_order=('K','B','C') at value 0.0 — every
    other point scores strictly higher, same construction test_search_annealing_strategy.py uses,
    so "did the search find the true optimum" has an unambiguous answer.
    """

    TRUE_OPTIMUM_VALUE = 0.0

    def evaluate(self, candidate: Candidate, budget: Budget, metrics: frozenset[str]) -> Result:
        spatial_dim = candidate.mapping["spatial"][0]["dim"]
        order = tuple(
            loop["dim"]
            for loop in sorted(candidate.mapping["operands"]["I"][0]["loops"], key=lambda l: l["order"])
        )
        if spatial_dim == "C" and order == ("K", "B", "C"):
            return _result(self.TRUE_OPTIMUM_VALUE)
        penalty = (0 if spatial_dim == "C" else 10) + sum(a != b for a, b in zip(order, ("K", "B", "C")))
        return _result(float(penalty))


class _AlwaysRefusesEvaluator:
    def evaluate(self, candidate: Candidate, budget: Budget, metrics: frozenset[str]) -> Result:
        raise ValueError("not_expressible_in: [fake]")


def _strategy(responses, **kwargs) -> AgenticMappingStrategy:
    return AgenticMappingStrategy(
        _WORKLOAD, _ARCH, _ScriptedProposer(responses), for_op="test.op",
        metric="latency_cycles", **kwargs,
    )


def test_propose_requires_k_equals_one():
    strategy = _strategy(['{"spatial_dim": "C", "temporal_order": ["K", "B", "C"]}'])
    state = SearchState(workload=_WORKLOAD, arch=_ARCH, for_op="test.op")
    with pytest.raises(ValueError):
        strategy.propose(state, k=2)


def test_propose_before_observe_raises():
    strategy = _strategy(['{"spatial_dim": "C", "temporal_order": ["K", "B", "C"]}'])
    state = SearchState(workload=_WORKLOAD, arch=_ARCH, for_op="test.op")
    strategy.propose(state, k=1)
    with pytest.raises(RuntimeError):
        strategy.propose(state, k=1)


def test_observe_before_propose_raises():
    strategy = _strategy(['{"spatial_dim": "C", "temporal_order": ["K", "B", "C"]}'])
    with pytest.raises(RuntimeError):
        strategy.observe([_result(1.0)])


def test_valid_json_proposal_is_used_directly_without_fallback():
    strategy = _strategy(['{"spatial_dim": "C", "temporal_order": ["K", "B", "C"]}'])
    state = SearchState(workload=_WORKLOAD, arch=_ARCH, for_op="test.op")
    strategy.propose(state, k=1)
    strategy.observe([_result(42.0)])
    assert strategy.evaluated[0].used_fallback is False
    assert strategy.evaluated[0].fallback_reason is None
    assert strategy.evaluated[0].candidate.spatial_dim == "C"
    assert strategy.evaluated[0].candidate.temporal_order == ("K", "B", "C")


def test_markdown_fenced_json_is_parsed():
    strategy = _strategy(['```json\n{"spatial_dim": "B", "temporal_order": ["C", "K", "B"]}\n```'])
    state = SearchState(workload=_WORKLOAD, arch=_ARCH, for_op="test.op")
    strategy.propose(state, k=1)
    strategy.observe([_result(1.0)])
    assert strategy.evaluated[0].used_fallback is False
    assert strategy.evaluated[0].candidate.spatial_dim == "B"


def test_invalid_json_falls_back_to_random_unvisited():
    strategy = _strategy(["this is not json at all"], seed=1)
    state = SearchState(workload=_WORKLOAD, arch=_ARCH, for_op="test.op")
    strategy.propose(state, k=1)
    strategy.observe([_result(1.0)])
    evaluated = strategy.evaluated[0]
    assert evaluated.used_fallback is True
    assert "not valid JSON" in evaluated.fallback_reason


def test_missing_dim_in_temporal_order_falls_back():
    """The real failure mode found empirically: dropping spatial_dim out of temporal_order,
    leaving only 2 of the 3 required dimensions.
    """
    strategy = _strategy(['{"spatial_dim": "B", "temporal_order": ["C", "K"]}'], seed=1)
    state = SearchState(workload=_WORKLOAD, arch=_ARCH, for_op="test.op")
    strategy.propose(state, k=1)
    strategy.observe([_result(1.0)])
    evaluated = strategy.evaluated[0]
    assert evaluated.used_fallback is True
    assert "not a permutation" in evaluated.fallback_reason


def test_unknown_spatial_dim_falls_back():
    strategy = _strategy(['{"spatial_dim": "Z", "temporal_order": ["B", "C", "K"]}'], seed=1)
    state = SearchState(workload=_WORKLOAD, arch=_ARCH, for_op="test.op")
    strategy.propose(state, k=1)
    strategy.observe([_result(1.0)])
    evaluated = strategy.evaluated[0]
    assert evaluated.used_fallback is True
    assert "is not one of" in evaluated.fallback_reason


def test_repeated_already_visited_combination_falls_back():
    same = '{"spatial_dim": "C", "temporal_order": ["K", "B", "C"]}'
    strategy = _strategy([same, same], seed=1)
    state = SearchState(workload=_WORKLOAD, arch=_ARCH, for_op="test.op")
    strategy.propose(state, k=1)
    strategy.observe([_result(1.0)])
    strategy.propose(state, k=1)
    strategy.observe([_result(2.0)])
    assert strategy.evaluated[0].used_fallback is False
    assert strategy.evaluated[1].used_fallback is True
    assert "already-evaluated" in strategy.evaluated[1].fallback_reason
    # And the fallback must not repeat the same combination either.
    assert strategy.evaluated[1].candidate.spatial_dim != "C" or strategy.evaluated[1].candidate.temporal_order != ("K", "B", "C")


def test_done_after_max_iterations():
    strategy = _strategy(
        ['{"spatial_dim": "C", "temporal_order": ["K", "B", "C"]}'], max_iterations=3,
    )
    state = SearchState(workload=_WORKLOAD, arch=_ARCH, for_op="test.op")
    for i in range(3):
        assert not strategy.done()
        strategy.propose(state, k=1)
        strategy.observe([_result(float(i))])
    assert strategy.done()


def test_done_once_every_combination_visited():
    # 3 loop dims -> 3 spatial choices x 3! orders = 18 total combinations.
    strategy = _strategy(["not json, forces random fallback every round"], max_iterations=1000, seed=2)
    state = SearchState(workload=_WORKLOAD, arch=_ARCH, for_op="test.op")
    rounds = 0
    while not strategy.done() and rounds < 20:
        strategy.propose(state, k=1)
        strategy.observe([_result(1.0)])
        rounds += 1
    assert strategy.done()
    assert rounds == 18


def test_refused_candidate_is_recorded_as_a_rejected_move_not_a_crash():
    strategy = _strategy(
        ['{"spatial_dim": "C", "temporal_order": ["K", "B", "C"]}'], max_iterations=1,
    )
    state = SearchState(workload=_WORKLOAD, arch=_ARCH, for_op="test.op")
    strategy.propose(state, k=1)
    strategy.observe([ValueError("not_expressible_in: [fake]")])
    assert strategy.evaluated[0].result is None
    assert strategy.evaluated[0].error is not None
    assert strategy.best is None


def test_run_agentic_search_finds_the_true_optimum_with_perfect_proposals():
    """When the LLM always proposes the true optimum, the driver must report it as best —
    proves the full propose->evaluate->observe loop wiring, not just the parsing logic.
    """
    report = run_agentic_search(
        _WORKLOAD, _ARCH, _KnownLandscapeEvaluator(),
        _ScriptedProposer(['{"spatial_dim": "C", "temporal_order": ["K", "B", "C"]}']),
        for_op="test.op", metric="latency_cycles", max_iterations=1,
    )
    assert report.best is not None
    assert report.best_result.metrics["latency_cycles"].value == _KnownLandscapeEvaluator.TRUE_OPTIMUM_VALUE
    assert report.fallback_count == 0


def test_run_agentic_search_report_counts_fallbacks_and_failures():
    proposer = _ScriptedProposer([
        '{"spatial_dim": "C", "temporal_order": ["K", "B", "C"]}',  # valid
        "garbage",  # invalid -> fallback
        '{"spatial_dim": "C", "temporal_order": ["K", "B", "C"]}',  # already visited -> fallback
    ])
    report = run_agentic_search(
        _WORKLOAD, _ARCH, _KnownLandscapeEvaluator(), proposer,
        for_op="test.op", metric="latency_cycles", max_iterations=3, seed=3,
    )
    assert report.iterations == 3
    assert report.fallback_count == 2


def test_run_agentic_search_treats_evaluator_refusal_as_skipped_not_expressible():
    report = run_agentic_search(
        _WORKLOAD, _ARCH, _AlwaysRefusesEvaluator(),
        _ScriptedProposer(['{"spatial_dim": "C", "temporal_order": ["K", "B", "C"]}']),
        for_op="test.op", metric="latency_cycles", max_iterations=2,
    )
    assert report.skipped_not_expressible == 2
    assert report.best is None


def test_prompt_includes_history_of_prior_results():
    proposer = _ScriptedProposer([
        '{"spatial_dim": "C", "temporal_order": ["K", "B", "C"]}',
        '{"spatial_dim": "B", "temporal_order": ["C", "K", "B"]}',
    ])
    strategy = AgenticMappingStrategy(
        _WORKLOAD, _ARCH, proposer, for_op="test.op", metric="latency_cycles",
    )
    state = SearchState(workload=_WORKLOAD, arch=_ARCH, for_op="test.op")
    strategy.propose(state, k=1)
    strategy.observe([_result(7.0)])
    strategy.propose(state, k=1)  # triggers the second prompt, which must reference the first result
    assert "(none yet)" in proposer.prompts[0]
    assert "spatial_dim=C" in proposer.prompts[1]
    assert "7" in proposer.prompts[1]


def test_report_to_dict_is_json_safe():
    import json

    report = run_agentic_search(
        _WORKLOAD, _ARCH, _KnownLandscapeEvaluator(),
        _ScriptedProposer(['{"spatial_dim": "C", "temporal_order": ["K", "B", "C"]}']),
        for_op="test.op", metric="latency_cycles", max_iterations=1,
    )
    d = report.to_dict()
    json.dumps(d)  # raises if a dataclass/tuple/enum leaked through unconverted
    assert d["best"]["spatial_dim"] == "C"
    assert d["best_result"]["metrics"]["latency_cycles"]["value"] == 0.0
    assert d["evaluated"][0]["used_fallback"] is False


# --- real wall-clock budget, shared across all five agentic axes, docs/decisions.md D73 ---
# (this module is the representative, fully-covered case; the other four axes each get one
# thinner confirmation test in their own files, avoiding duplicating the core timing logic five
# times over — it's exercised once here, through the shared `_engine.py` driver every axis calls.)


def test_no_budget_runs_to_max_iterations_and_reports_not_stopped_early():
    report = run_agentic_search(
        _WORKLOAD, _ARCH, _KnownLandscapeEvaluator(),
        _ScriptedProposer(['{"spatial_dim": "C", "temporal_order": ["K", "B", "C"]}']),
        for_op="test.op", metric="latency_cycles", max_iterations=3, seed=1,
    )
    assert report.stopped_early is False
    assert report.iterations == 3
    assert report.wall_clock_s >= 0.0


def test_zero_wall_clock_budget_stops_before_any_real_proposal():
    """The real, direct proof the budget is a genuine, enforced stopping condition: a 0.0s budget
    must stop before the LLM (or the evaluator) is ever called even once."""
    proposer = _ScriptedProposer(['{"spatial_dim": "C", "temporal_order": ["K", "B", "C"]}'])
    evaluator = _KnownLandscapeEvaluator()
    report = run_agentic_search(
        _WORKLOAD, _ARCH, evaluator, proposer,
        for_op="test.op", metric="latency_cycles", max_iterations=10, wall_clock_budget_s=0.0,
    )
    assert proposer.calls == 0
    assert report.iterations == 0
    assert report.stopped_early is True
    assert report.best is None


def test_a_real_wall_clock_budget_stops_partway_through():
    """A real, if coarse, timing-based check: a budget short enough (measured against a
    deliberately slow fake proposer) must stop the search before it reaches max_iterations."""
    import time as _time

    class _SlowProposer:
        def propose(self, prompt: str) -> str:
            _time.sleep(0.02)
            return '{"spatial_dim": "C", "temporal_order": ["K", "B", "C"]}'

    report = run_agentic_search(
        _WORKLOAD, _ARCH, _KnownLandscapeEvaluator(), _SlowProposer(),
        for_op="test.op", metric="latency_cycles", max_iterations=50, wall_clock_budget_s=0.05,
    )
    assert report.stopped_early is True
    assert report.iterations < 50


def test_result_without_the_searched_metric_is_recorded_not_fatal():
    """A Result that lacks the searched metric used to raise KeyError out of `observe()`, killing
    the whole search over one candidate the evaluator couldn't answer for — the D112 hole, closed
    in `search/architecture/dse.py` and never carried across to the strategies (docs/decisions.md
    D168). Reachable with a real adapter, no stub required: `evaluators/rtl` fills its metrics
    dict only when `latency_cycles` was requested, so asking it for any other metric returns a
    valid Result with no metrics at all.
    """
    strategy = _strategy(
        ['{"spatial_dim": "C", "temporal_order": ["K", "B", "C"]}'], max_iterations=1,
    )
    state = SearchState(workload=_WORKLOAD, arch=_ARCH, for_op="test.op")
    strategy.propose(state, k=1)

    metricless = Result(
        metrics={},
        validity=Validity(ok=True, checker_version="test"),
        domain=Domain(in_domain=False),
        bottleneck=Bottleneck(limiter=Limiter.COMPUTE),
        provenance=Provenance(evaluator="rtl@0.0.0", inputs={}),
        escalation=Escalation(recommended=False),
    )
    strategy.observe([metricless])  # must not raise

    assert strategy.evaluated[0].result is None
    assert "returned no" in strategy.evaluated[0].error
    assert strategy.best is None


@pytest.mark.parametrize(
    "raw",
    [
        '```json\n{"spatial_dim": "C", "temporal_order": ["K", "B", "C"]}\n```',
        '```JSON\n{"spatial_dim": "C", "temporal_order": ["K", "B", "C"]}\n```',
        '```yaml\n{"spatial_dim": "C", "temporal_order": ["K", "B", "C"]}\n```',
        'Here is my proposal:\n```json\n{"spatial_dim": "C", "temporal_order": ["K", "B", "C"]}\n```',
        '```json\n{"spatial_dim": "C", "temporal_order": ["K", "B", "C"]}\n```\nHope that helps!',
    ],
    ids=["json", "JSON-uppercase", "yaml-tag", "prose-before", "prose-after"],
)
def test_real_llm_response_habits_are_parsed_without_falling_back(raw):
    """The fence stripper accepted only a lowercase `json` tag and only when the fence was the
    entire response. An uppercase tag, a different tag, or the near-universal "Here is my
    proposal:" preamble all left the fence in place, `json.loads` failed, and the strategy fell
    back to a random unvisited candidate — degrading to random search precisely when the model was
    cooperating (docs/decisions.md D191).

    Falling back is graceful, which is why this was invisible: nothing errors, the run just
    spends its iteration budget on random proposals.
    """
    strategy = _strategy([raw], max_iterations=1)
    state = SearchState(workload=_WORKLOAD, arch=_ARCH, for_op="test.op")

    strategy.propose(state, k=1)
    strategy.observe([_result(10.0)])

    assert strategy.evaluated[0].used_fallback is False, (
        f"fell back instead of using the model's proposal: {strategy.evaluated[0].fallback_reason}"
    )
