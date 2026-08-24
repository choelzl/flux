"""The loop plan (docs/decisions.md D505): the shape of a pass as a document -- parts, budgets,
stages, roles, tools -- written by hand, by the agent, or both, validated the same way.

Cedric: "planning and designing of the actual loop/sub loops ... both the option to
hardcode/define manually those or to have the agent make its picks".
"""

from __future__ import annotations

import json

import pytest
from flux_loop import LoopRequest, rig, run_loop
from flux_loop.plan import check_plan, plan_surface

from test_roles import Angry, Ladder, Pair, _request
from flux_llm import Reply


def _surface(tmp_path):
    from flux_loop import LoopState

    problem = Pair()
    st = LoopState(request=LoopRequest(), say=lambda _m: None, proposer=None, feedback=None)
    return plan_surface(problem, st)


def test_the_surface_is_the_problems_own_vocabulary(tmp_path):
    s = _surface(tmp_path)
    assert s["parts"]["choices"] == ["front", "back"] and s["stages"]["choices"] == ["stage"]
    assert "agent" in s["roles"]["fields"]["orchestrator"]["choices"] and None in s["roles"]["fields"]["orchestrator"]["choices"]
    assert s["budget"]["fields"]["steps"]["default"] == 24 and s["tools"]["default"] is True


def test_a_plan_is_checked_against_the_vocabulary_field_by_field(tmp_path):
    s = _surface(tmp_path)
    clean, errors = check_plan({"parts": ["back"], "budget": {"steps": 3, "regress_after": 4},
                                "stages": ["stage"], "roles": {"orchestrator": "rules"}, "tools": True,
                                "why": "back first"}, s)
    assert not errors and clean == {"parts": ["back"], "budget": {"steps": 3, "regress_after": 4},
                                    "stages": ["stage"], "roles": {"orchestrator": "rules"}, "tools": True,
                                    "why": "back first"}
    _clean, errors = check_plan({"parts": ["side"], "budget": {"steps": 0, "vibes": 1}, "stages": ["placed"],
                                 "roles": {"orchestrator": "vibes", "critic": "llm"}, "tools": "yes", "gate": "off"}, s)
    assert [e.split(" ")[0] for e in errors] == ["`gate`", "`parts`", "`budget.steps`", "`budget.vibes`", "`stages`",
                                                  "`roles.orchestrator`", "`roles.critic`", "`tools`"]
    assert "which this problem does not have (parts: ['front', 'back'])" in errors[1]
    # "agent" marks a field as the agent's to fill, at the field or inside it
    clean, errors = check_plan({"parts": "agent", "budget": {"steps": "agent", "repair_attempts": 2}}, s)
    assert not errors and clean == {"parts": "agent", "budget": {"steps": "agent", "repair_attempts": 2}}
    assert check_plan("nope", s)[1] == ["a plan is a JSON object"]


def test_a_hand_written_plan_shapes_the_pass(tmp_path):
    """The manual half: a plan file names the parts and their order, the budgets, the roles;
    the pass follows it, and the plan is on the record."""
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"parts": ["back"], "budget": {"steps": 2}, "roles": {"orchestrator": "rules"},
                                "why": "only the back this pass"}))
    said: list[str] = []
    problem = Pair()
    out = run_loop(problem, _request(tmp_path, plan_file=str(plan)), proposer=Angry(), log=said.append)
    assert problem.written == ["back"] and sorted(out.admitted) == ["back"]
    assert problem.roles().orchestrator.name == "rules"
    assert any(m.startswith("  plan (file ") and "parts back" in m and "steps=2" in m for m in said)
    from flux_records import Records

    rec = Records(str(tmp_path / "r.db"), objective={"study": "pair"})
    plans = [e["detail"] for e in rec.store.events(rec.campaign_id) if e["kind"] == "plan"]
    assert plans and plans[-1]["parts"] == ["back"] and plans[-1]["plan"].startswith("file ")


def test_a_bad_field_in_a_plan_is_named_and_the_rest_applies(tmp_path):
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"parts": ["side"], "budget": {"steps": 2}}))
    said: list[str] = []
    problem = Pair()
    run_loop(problem, _request(tmp_path, plan_file=str(plan)), proposer=Angry(), log=said.append)
    assert any("`parts` names ['side']" in m for m in said)
    assert sorted(problem.written) == ["back", "front"]              # the default division
    assert any("steps=2" in m for m in said)


def test_the_agent_fills_the_open_fields_and_a_refused_plan_comes_back_fixed(tmp_path):
    """The agentic half, MIXED with the manual one: the budget is fixed by hand, the parts and
    the roles are "agent"; the agent's first answer names a part that does not exist and is
    sent back with the validator's words; its second is applied, written to the file and
    kept on the record with its reason."""
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"parts": "agent", "budget": {"steps": 3}, "roles": "agent"}))
    asked: list[str] = []

    class Planner:
        def propose(self, prompt, *, schema=None, tools=None, budget=None):
            asked.append(prompt)
            if "PLANNING" in prompt:
                by = {t.name: t for t in (tools or [])}
                assert "GOAL:" in by["standings"].run({})
                if "YOUR LAST PLAN WAS REFUSED" not in prompt:
                    return Reply.of(json.dumps({"parts": ["back", "side"], "roles": {"orchestrator": "rules"}, "why": "side too"}))
                assert "`parts` names ['side']" in prompt
                return Reply.of(json.dumps({"parts": ["back"], "roles": {"orchestrator": "rules"}, "why": "the back alone; front is done"}))
            raise AssertionError("only the planning turn is the agent's here")

    said: list[str] = []
    problem = Pair()
    out = run_loop(problem, _request(tmp_path, plan_file=str(plan), agent=("plan",)), proposer=Planner(), log=said.append)
    assert problem.written == ["back"] and sorted(out.admitted) == ["back"]
    assert len([p for p in asked if "PLANNING" in p]) == 2
    assert "FIXED BY HAND (not yours to change): {\"budget\": {\"steps\": 3}}" in asked[0]
    assert "YOU FILL these fields: parts, roles" in asked[0]
    written = json.loads(plan.read_text())
    assert written["parts"] == ["back"] and written["roles"] == {"orchestrator": "rules"} and written["budget"] == {"steps": 3}
    assert written["why"].startswith("the back alone") and written["plan"] == "file " + str(plan) + " + the agent"
    assert any("written to" in m for m in said)


def test_the_agent_plans_from_nothing_and_the_record_remembers(tmp_path):
    """No file: with `plan` the agent fills every field it is shown (a field left out keeps
    its default); the next pass without the agent reads the plan back from the record."""
    class Planner:
        def propose(self, prompt, *, schema=None, tools=None, budget=None):
            if "PLANNING" in prompt:
                assert "THE PREVIOUS PLAN" not in prompt
                return Reply.of(json.dumps({"parts": ["back", "front"], "budget": {"steps": 5}, "tools": False, "why": "back is cheaper"}))
            return Reply.of('{"pick": "back", "why": "-"}')

    problem = Pair()
    run_loop(problem, _request(tmp_path, agent=("plan",)), proposer=Planner(), log=lambda _m: None)
    assert problem.written == ["back", "front"]
    said: list[str] = []
    again = Pair()
    run_loop(again, _request(tmp_path, agent=("plan",)), proposer=Angry(), log=said.append)   # no proposer = no agent
    assert any("plan (the record's last plan)" in m and "parts back, front" in m for m in said)


def test_the_plan_may_keep_a_subset_of_the_stages_in_order(tmp_path):
    class Two(Pair):
        def stages(self):
            return self.chained(["screen", "placed"])

    from flux_loop import LoopState

    problem = Two()
    st = LoopState(request=LoopRequest(), say=lambda _m: None, proposer=None, feedback=None)
    s = plan_surface(problem, st)
    assert check_plan({"stages": ["placed", "screen"]}, s)[1] == ["`stages` must keep the problem's rising order ['screen', 'placed']"]
    from flux_loop.plan import _apply

    _apply(problem, st, {"stages": ["screen"]})
    assert problem.stages() == ["screen"] and st.plan == {"stages": ["screen"]}


def test_the_ladder_and_the_plan_together(tmp_path):
    """The three halves at once: the plan (by hand) names the parts and hands the orchestrator
    to the agent; the agent picks the step; turns have tools."""
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"parts": ["front", "back"], "roles": {"orchestrator": "agent"}, "tools": True}))

    class Agent:
        def propose(self, prompt, *, schema=None, tools=None, budget=None):
            if "Pick ONE option" in prompt:
                return Reply.of('{"pick": "rewrite", "why": "the goal is 1 byte"}')
            return Reply.of('{"pick": "improve", "why": "-"}')

    problem = Ladder()
    out = run_loop(problem, _request(tmp_path, steps=6, plan_file=str(plan)), proposer=Agent(), log=lambda _m: None)
    assert problem.taken == ["rewrite"] and problem.roles().orchestrator.name == "agent"
    assert out.admitted["front"].name == "front-rewritten"


def test_the_plan_names_the_method_per_part_and_the_generator_reads_it_as_its_brief(tmp_path):
    """D577 (Cedric: "plan first, then run"): `methods` in the plan -- by hand or by the
    agent, who is shown the library's digested index -- becomes each part's brief, which the
    generator's prompt carries."""
    from flux_loop import Candidate

    class Modelled(Pair):
        """Pair on the MODEL path: a design prompt per part, so the brief has a prompt to ride."""

        def generator(self, subgoal, state):
            return None

        def design_prompt(self, subgoal, method, state, human, prior, prior_why):
            return f"write {subgoal}", None

        def parse_design(self, reply, subgoal):
            return Candidate(name=f"{subgoal}-art", artifact=str(reply), subgoal=subgoal), ""

    seen: list[str] = []

    class Writer:
        def propose(self, prompt, *, schema=None, tools=None, budget=None):
            seen.append(prompt)
            if "PLANNING" in prompt:
                raise AssertionError("no agent plans here")
            return Reply.of(json.dumps({"artifact": "x", "why": "w"}))

    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"methods": {"front": "a lookup table first: the record says the polynomial missed", "side": "x"}}))
    said: list[str] = []
    for d in ("one", "two", "three"):
        (tmp_path / d).mkdir()                     # a record each: a resumed record would draft nothing
    run_loop(Modelled(), _request(tmp_path / "one", plan_file=str(plan), patching=False), proposer=Writer(), log=said.append)
    assert any("`methods` names ['side']" in m for m in said), "a part the problem does not have is named"
    plan.write_text(json.dumps({"methods": {"front": "a lookup table first: the record says the polynomial missed"}}))
    seen.clear(); said.clear()
    run_loop(Modelled(), _request(tmp_path / "two", plan_file=str(plan), patching=False), proposer=Writer(), log=said.append)
    assert any("methods front: a lookup table first" in m for m in said)
    fronts = [p for p in seen if "write front" in p]
    assert fronts and "BRIEF (from the orchestrator):\na lookup table first" in fronts[0], "the front's brief is the plan's method"
    backs = [p for p in seen if "write back" in p]
    assert backs and "BRIEF" not in backs[0], "no method was planned for the back"

    asked: list[str] = []

    class Planner(Writer):
        def propose(self, prompt, *, schema=None, tools=None, budget=None):
            asked.append(prompt)
            if "PLANNING" in prompt:
                return Reply.of(json.dumps({"parts": ["front", "back"], "methods": {"front": "from the library: PACE's 16 segments", "back": "the stack from the record"}, "why": "planned"}))
            return Reply.of(json.dumps({"artifact": "x", "why": "w"}))

    class Read(Modelled):
        def library_index(self, state):
            return ["  [PACE.pdf] PACE: piecewise polynomial exp, 16 segments, 1 ULP"]

    run_loop(Read(), _request(tmp_path / "three", agent=("plan",), patching=False), proposer=Planner(), log=lambda _m: None)
    planning = [p for p in asked if "PLANNING" in p]
    assert planning and "THE LIBRARY, digested" in planning[0] and "[PACE.pdf] PACE" in planning[0]
    assert "In `methods`, say for each part the approach to try FIRST" in planning[0]
    assert any("BRIEF (from the orchestrator):\nfrom the library: PACE's 16 segments" in p for p in asked if "write front" in p)
    assert any("BRIEF (from the orchestrator):\nthe stack from the record" in p for p in asked if "write back" in p)


def test_the_document_asks_for_the_plan_with_one_word():
    from flux_loop import TaskSpec
    from flux_loop.task import describe_flow

    doc = {"id": "p", "statement": "p", "parts": ["a"], "gate": {"test": ["true"]}, "flow": {"plan": "llm"}}
    task = TaskSpec.from_dict(doc)
    assert task.budget["agent"] == ["plan"]
    assert any(line.startswith("plan: llm (the pass is planned first") for line in describe_flow(task))
    assert any(line.startswith("plan: none") for line in describe_flow(TaskSpec.from_dict({**doc, "flow": {}})))
    with pytest.raises(Exception, match="flow.plan is one of none, llm"):
        TaskSpec.from_dict({**doc, "flow": {"plan": "given"}})
