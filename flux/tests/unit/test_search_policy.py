"""DSE preferences guide code, prototypes and points without adding a direction setting."""

from types import SimpleNamespace

import pytest

from flux_loop import LoopRequest, LoopState, PromptProblem, TaskError, TaskSpec
from flux_loop.direction import PROMPTS, choose, guidance, record_choice
from flux_loop.prototype import prefers_edits
from flux_loop.types import Candidate


def task(policy="adaptive", by="model"):
    return TaskSpec.from_dict({"id": "search", "statement": "Try useful alternatives.",
                              "flow": {"orchestrate": {"by": by, "dse": policy},
                                       "test": {"check": "true"}}})


@pytest.mark.parametrize("policy", PROMPTS)
@pytest.mark.parametrize("by", ["model", "rules", "tools", "claude"])
def test_existing_dse_field_reaches_prompts_and_round_trips(policy, by):
    spec = task(policy, by)
    problem = PromptProblem(spec)
    state = LoopState(request=LoopRequest(), say=lambda _: None, proposer=None, feedback=None)
    assert problem.roles().orchestrator.dse == policy
    assert PROMPTS[policy] in guidance(problem, state)
    assert TaskSpec.from_dict(spec.to_dict()).roles == spec.roles


@pytest.mark.parametrize("bad", [-0.1, 1.1, "half", True])
def test_invalid_exploration_quotas_are_rejected(bad):
    with pytest.raises(TaskError, match="exploration_quota"):
        TaskSpec.from_dict({"id": "search", "statement": "Search", "budget": {"exploration_quota": bad},
                            "flow": {"test": {"check": "true"}}})


def test_quota_reserves_experiments_even_when_orchestrator_prefers_tuning():
    problem = PromptProblem(task("tune", "rules"))
    state = LoopState(request=LoopRequest(exploration_quota=0.25), say=lambda _: None, proposer=None, feedback=None)
    picks = []
    for _ in range(8):
        pick, why = choose(problem, state, Candidate("baseline", "x"), "it is verified")
        record_choice(state, pick, why)
        picks.append(pick)
    assert picks == ["explore", "tune", "tune", "tune"] * 2


def test_default_prototype_search_allows_rewrites_near_a_passing_design():
    state = LoopState(request=LoopRequest(), say=lambda _: None, proposer=None, feedback=None)
    capability = SimpleNamespace(domain_size=1000)
    assert prefers_edits(capability, None, state, 1) is None
    state.part(None).dse = "finetune"
    assert prefers_edits(capability, None, state, 1)
    state.part(None).dse = "explore"
    assert prefers_edits(capability, None, state, 1) is None


@pytest.mark.parametrize("policy, develop_distant", [("adaptive", True), ("explore", True), ("finetune", False)])
def test_part_ladder_offers_early_redesign_and_development_of_distant_alternatives(monkeypatch, policy, develop_distant):
    from flux_loop import Improve, Objective, Objectives
    from flux_loop import ladder

    declared = ladder.Ladder(steps=("depth", "redesign", "contender"))
    problem = SimpleNamespace(subgoals=lambda: ["part"], objectives=lambda: Objectives([Objective("quality", goal=100)]),
                              stages=lambda: ["screen"], ladder=lambda: declared)
    state = LoopState(request=LoopRequest(), say=lambda _: None, proposer=None, feedback=None)
    state.prototypes["part"] = "baseline prototype"
    baseline = Candidate("baseline", "target", subgoal="part")
    state.admitted["part"] = baseline
    state.part("part").alone = {"quality": 100}
    state.part("part").dse = policy
    monkeypatch.setattr(ladder, "part_depth", lambda *args: {"depth": 4})
    monkeypatch.setattr(ladder, "contender", lambda *args: {"value": 10, "depth": 20})
    menu = {option.name: option for option in ladder.options(problem, declared, Improve(baseline, "try again", subgoal="part"), state)}
    assert menu["depth"].due and menu["redesign"].due, "redesign is available before spending a depth pass"
    assert menu["contender"].due is develop_distant


def test_exploration_reservations_survive_a_new_pass_state():
    from flux_loop.direction import reserve_choice

    events = []
    store = SimpleNamespace(append_event=lambda campaign, kind, detail: events.append({"kind": kind, "detail": detail}),
                            events=lambda campaign: events)
    records = SimpleNamespace(store=store, campaign_id="campaign")
    picks = []
    for _ in range(8):
        state = LoopState(request=LoopRequest(exploration_quota=0.25), say=lambda _: None,
                          proposer=None, feedback=None, records=records)
        pick, _ = reserve_choice(state, "tune", "try a nearby setting")
        picks.append(pick)
    assert picks == ["explore", "tune", "tune", "tune"] * 2


def test_parallel_passes_reserve_against_the_same_exploration_quota():
    from concurrent.futures import ThreadPoolExecutor
    from flux_loop.direction import reserve_choice

    events = []
    records = SimpleNamespace(campaign_id="campaign", store=SimpleNamespace(
        append_event=lambda campaign, kind, detail: events.append({"kind": kind, "detail": detail}),
        events=lambda campaign: events))

    def reserve(_):
        state = LoopState(request=LoopRequest(exploration_quota=0.25), say=lambda _: None,
                          proposer=None, feedback=None, records=records)
        return reserve_choice(state, "tune", "local change")[0]

    with ThreadPoolExecutor(max_workers=8) as pool:
        picks = list(pool.map(reserve, range(16)))
    assert picks.count("explore") == 4 and len(events) == 16


def test_permissive_repair_accepts_a_complete_replacement_in_the_same_turn(tmp_path):
    import json

    from flux_llm import ScriptedProposer
    from flux_loop.generation import _generate_with_model

    problem = PromptProblem(task())
    state = LoopState(request=LoopRequest(prototype=False, repair_attempts=0), say=lambda _: None,
                      proposer=ScriptedProposer([json.dumps({"artifact": "a different approach", "why": "test a new structure"})]),
                      feedback=None, workdir=str(tmp_path))
    state.best["*"] = (1, Candidate("baseline", "broken approach"), "does not pass")
    cand, _, reason = _generate_with_model(problem, None, "", state, None)
    assert cand is not None and cand.artifact == "a different approach", reason
    assert len(state.proposer.prompts) == 1


def test_reaching_a_goal_does_not_prevent_the_next_experiment(tmp_path):
    import json

    from flux_llm import ScriptedProposer
    from flux_loop import run_loop

    problem = PromptProblem(task())
    problem.good_enough = lambda state: "goal already met"
    result = run_loop(problem, LoopRequest(prototype=False, steps=1, db=""),
                      proposer=ScriptedProposer([json.dumps({"artifact": "an experiment"})]), log=lambda _: None)
    assert result.admitted and not result.stopped.startswith("good enough")
