"""A box of the drawing answered by a coding agent (D640): the brief, `out.json` checked, one retry
with the reason, the rules half when it fails, the turn on the record. A fake agent script stands
in for opencode/claude/codex."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from flux_loop import PromptProblem, TaskError, TaskSpec
from flux_loop.boxes import box_turn

#: D738: these tests measure what one pass does with a whole search; one design a pass is the default
WHOLE = 10_000

FAKE = r'''import json, sys
from pathlib import Path

mode, brief, out = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
count = out.with_name("calls")
n = int(count.read_text()) + 1 if count.exists() else 1
count.write_text(str(n))
good = json.loads(Path(sys.argv[4]).read_text()) if len(sys.argv) > 4 else {"ok": True}
if mode == "fail":
    sys.exit(3)
if mode == "silent":
    print("done"); sys.exit(0)
if mode == "bad" or (mode == "bad_then_good" and n == 1):
    out.write_text('{"nothing": 1}')
else:
    out.write_text(json.dumps(good))
print("wrote out.json")
'''

SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}, "why": {"type": "string"}}, "required": ["ok"]}


def _agent(tmp_path: Path, mode: str, answer: dict | None = None) -> dict:
    fake = tmp_path / "fake_agent.py"
    fake.write_text(FAKE)
    cmd = [sys.executable, str(fake), mode, "{prompt_file}", "{artifact}"]
    if answer is not None:
        (tmp_path / "answer.json").write_text(json.dumps(answer))
        cmd.append(str(tmp_path / "answer.json"))
    return {"command": cmd, "timeout_s": 60}


def _state(tmp_path: Path) -> SimpleNamespace:
    said: list[str] = []
    return SimpleNamespace(workdir=str(tmp_path / "work"), say=said.append, said=said, records=None, proposer=None,
                           lessons=[])


def test_a_good_answer_is_taken_and_the_brief_names_the_schema(tmp_path):
    st = _state(tmp_path)
    got = box_turn("critique", _agent(tmp_path, "good", {"ok": False, "why": "the carry is dropped"}), "Q?", SCHEMA, st)
    assert got == {"ok": False, "why": "the carry is dropped"}
    brief = (tmp_path / "work" / "agents" / "critique" / "001" / "BRIEF.md").read_text()
    assert brief.startswith("Q?") and '"required"' in brief and "Do not run the gate" in brief
    assert any("agent" in m and "answered" in m for m in st.said)


def test_a_refused_answer_is_sent_back_once_with_the_reason(tmp_path):
    st = _state(tmp_path)
    assert box_turn("critique", _agent(tmp_path, "bad_then_good", {"ok": True}), "Q?", SCHEMA, st) == {"ok": True}
    assert (tmp_path / "work" / "agents" / "critique" / "001" / "calls").read_text() == "2"


@pytest.mark.parametrize("mode", ["bad", "fail", "silent"])
def test_a_second_refusal_a_failure_or_no_answer_falls_back(tmp_path, mode):
    st = _state(tmp_path)
    assert box_turn("critique", _agent(tmp_path, mode), "Q?", SCHEMA, st) is None
    assert any("fell back to the rules half" in m for m in st.said)


def test_the_box_s_own_rule_refuses_an_answer(tmp_path):
    st = _state(tmp_path)
    rule = lambda d: None if d.get("why") else "an objection needs a why"   # noqa: E731
    assert box_turn("critique", _agent(tmp_path, "good", {"ok": False}), "Q?", SCHEMA, st, check=rule) is None


def _doc(**flow):
    return {"id": "boxes", "statement": "the word good",
            "objectives": [{"metric": "m", "direction": "minimize"}], "flow": {"test": {"test": ["true"]}, **flow}}


def test_the_loader_takes_an_agent_only_where_one_may_answer():
    task = TaskSpec.from_dict(_doc(critique={"by": "claude"}, validate={"by": {"preset": "opencode"}}))
    assert task.critique and task.flow["validate"] == {"agent": {"preset": "opencode"}}
    with pytest.raises(TaskError, match="never delegated"):
        TaskSpec.from_dict(_doc(test={"by": "claude"}))
    assert TaskSpec.from_dict(_doc(plan={"by": "codex"})).budget["agent"] == ["plan"]
    with pytest.raises(TaskError, match="not a box an agent answers"):
        TaskSpec.from_dict(_doc(feedback={"by": "claude"}))
    with pytest.raises(TaskError, match="flow.critique.by"):
        TaskSpec.from_dict(_doc(critique={"by": "cursor"}))
    from flux_loop.document import describe_flow

    lines = describe_flow(task)
    assert any(ln.startswith("critique: agent claude") for ln in lines)
    assert any(ln.startswith("validate: agent opencode") for ln in lines)


def test_the_agent_critic_objects_and_its_fallback_passes(tmp_path):
    from flux_loop import Candidate

    cand = Candidate("c1", "module m; endmodule")
    agent = _agent(tmp_path, "good", {"ok": False, "issues": ["no carry out"], "why": "incomplete"})
    prob = PromptProblem(TaskSpec.from_dict(_doc(critique={"by": agent})))
    st = _state(tmp_path)
    v = prob.critique("candidate", cand, st)
    assert not v.ok and "no carry out" in v.why
    (tmp_path / "q").mkdir()
    quiet = PromptProblem(TaskSpec.from_dict(_doc(critique={"by": _agent(tmp_path / "q", "fail")})))
    assert quiet.critique("candidate", cand, _state(tmp_path)).ok, "a critic that fails does not veto"


def test_the_agent_reads_the_document_and_objects(tmp_path):
    agent = _agent(tmp_path, "good", {"ok": False, "objections": ["no stage measures m"]})
    prob = PromptProblem(TaskSpec.from_dict(_doc(validate={"by": agent})))
    assert prob.objections(_state(tmp_path)) == ["no stage measures m"]


def test_a_coding_agent_orchestrates_and_its_picks_are_recorded(tmp_path):
    """`flow: {orchestrate: {by: ...}}`: the agent picks the next part from the menu; the loop
    drafts, gates and records it; an off-menu pick would be refused (D640)."""
    from flux_llm import ScriptedProposer
    from flux_loop import LoopRequest, run_loop
    from flux_loop.roles import AgentOrchestrator

    doc = {"id": "orch",
           "statement": "write the word good",
           "parts": {"one": "the word", "two": "the word again"},
           "objectives": [{"metric": "bytes", "direction": "minimize"}],
           "flow": {"orchestrate": {"by": _agent(tmp_path, "good", {"pick": "two", "why": "two first"})},
                    "test": {"test": ["true"]},
                    "measure": {"size": {"command": ["wc", "-c", "{artifact}"], "metrics_re": {"bytes": '(\\d+)'}}}}}
    prob = PromptProblem(TaskSpec.from_dict(doc))
    assert isinstance(prob.roles().orchestrator, AgentOrchestrator) and prob.roles().orchestrator.coding
    said: list[str] = []
    out = run_loop(prob, LoopRequest(batch=WHOLE, db=str(tmp_path / "o.db"), steps=4, repair_attempts=1, critique_rounds=0,
                                     prototype=False, screen_only=True),
                   proposer=ScriptedProposer(['{"artifact": "good", "why": "-"}'] * 4), log=said.append)
    assert sorted(out.admitted) == ["one", "two"]
    picks = [m for m in said if "orchestrate [next part]" in m]
    assert picks and picks[0].strip().startswith("orchestrate [next part]: two"), said
    from flux_records import Records

    rec = Records(str(tmp_path / "o.db"), objective={"study": "orch"}, name="orch")
    turns = rec.recall("agent_turn")
    assert turns and all(t["box"] == "orchestrate" and t["ok"] for t in turns)
    from flux_loop.report import load, render

    rep = load(str(tmp_path / "o.db"))
    assert rep.agent_turns and "<h2>Agent turns</h2>" in render(rep) and "orchestrate" in render(rep)


def test_a_coding_agent_proposes_the_points_of_the_space(tmp_path):
    """`flow: {orchestrate: {by: ...}}`: the agent's points are checked against the space; a point
    outside it is dropped, the rest are measured (D640)."""
    from flux_loop import LoopRequest, run_loop

    stage = ["{python}", "-c", "import sys; print(f'cost={int(sys.argv[1]) * 10 + len(sys.argv[2])}')", "{x}", "{y}"]
    answer = {"points": [{"x": 2, "y": "bb"}, {"x": 9, "y": "a"}], "why": "the corner"}
    doc = {"id": "pts",
           "statement": "a grid",
           "objectives": [{"metric": "cost", "direction": "minimize"}],
           "flow": {"orchestrate": {"by": _agent(tmp_path, "good", answer), "space": {"x": [1, 2, 3], "y": ["a", "bb"]}},
                    "test": {"test": ["true"]},
                    "measure": {"run": {"command": stage, "metrics": ["cost"]}}}}
    said: list[str] = []
    out = run_loop(PromptProblem(TaskSpec.from_dict(doc)),
                   LoopRequest(batch=WHOLE, steps=2, finalists=0, screen_only=True, prototype=False), log=said.append)
    measured = [s.candidate.knobs for s in out.scored if "cost" in s.metrics]
    assert {"x": 2, "y": "bb"} in measured and all(p.get("x") != 9 for p in measured)
    assert any("the agent proposes 1 point(s)" in m and "1 dropped" in m for m in said), said


def test_a_coding_agent_chooses_along_the_front_the_objectives_leave_open(tmp_path):
    """`flow: {select: {by: ...}}`: with no goal, every point of a speed/size front is a choice
    the vector leaves open; the agent's pick and its reason become the decision (D640)."""
    from flux_loop import LoopRequest, run_loop

    stage = ["{python}", "-c", "import sys; x = int(sys.argv[1]); print(f'speed={x}'); print(f'size={x * x}')", "{x}"]
    doc = {"id": "front",
           "statement": "a trade",
           "objectives": [{"metric": "speed", "direction": "maximize"}, {"metric": "size", "direction": "minimize"}],
           "flow": {"orchestrate": {"policy": "sweep", "space": {"x": [1, 2, 3]}},
                    "select": {"by": _agent(tmp_path, "good", {"pick": "x=1", "why": "the smallest"})},
                    "test": {"test": ["true"]},
                    "measure": {"run": {"command": stage, "metrics": ["speed", "size"]}}}}
    out = run_loop(PromptProblem(TaskSpec.from_dict(doc)),
                   LoopRequest(batch=WHOLE, steps=2, finalists=0, screen_only=True, prototype=False), log=lambda _m: None)
    assert out.decision.name == "x=1" and "the agent chose x=1 among 3 ties: the smallest" in out.decided_by


def test_an_agent_draws_lessons_from_the_record_and_each_cites_its_rows(tmp_path):
    """`flow: {knowledge: {lessons: {by: ...}}}`: once a pass the agent reads the measured rows; a lesson
    citing a row that does not exist is refused (D640)."""
    from flux_loop import LoopRequest, run_loop
    from flux_loop.boxes import AgentLessons
    from flux_records import Records

    stage = ["{python}", "-c", "import sys; print(f'cost={int(sys.argv[1]) * 10}')", "{x}"]
    doc = {"id": "rows",
           "statement": "a grid",
           "objectives": [{"metric": "cost", "direction": "minimize"}],
           "flow": {"orchestrate": {"policy": "sweep", "space": {"x": [1, 2, 3]}},
                    "knowledge": {"lessons": {"by": "claude"}},
                    "test": {"test": ["true"]},
                    "measure": {"run": {"command": stage, "metrics": ["cost"]}}}}
    prob = PromptProblem(TaskSpec.from_dict(doc))
    assert [type(s).__name__ for s in prob.roles().knowledge.sources] == ["AgentLessons"]
    db = str(tmp_path / "r.db")
    run_loop(prob, LoopRequest(batch=WHOLE, db=db, steps=2, finalists=0, screen_only=True, prototype=False), log=lambda _m: None)
    rec = Records(db, objective={"study": "rows"}, name="rows")
    seqs = [t.seq for t in rec.store.trials(rec.campaign_id) if t.result is not None]
    good = {"lessons": [{"text": "cost grows with x", "rows": seqs[:2]}]}
    st = _state(tmp_path)
    st.records = rec
    text = AgentLessons(_agent(tmp_path, "good", good)).render(st)
    assert text == f"- cost grows with x (rows {seqs[0]}, {seqs[1]})"
    (tmp_path / "b").mkdir()
    st2 = _state(tmp_path / "b")
    st2.records = rec
    assert AgentLessons(_agent(tmp_path / "b", "good", {"lessons": [{"text": "x", "rows": [999]}]})).render(st2) == ""


def test_a_coding_agent_plans_the_pass_and_its_methods_brief_the_generator(tmp_path):
    """`flow: {plan: {by: ...}}`: the agent's plan is checked by check_plan and applied; each
    part's method reaches the generator's prompt (D640)."""
    from flux_llm import ScriptedProposer
    from flux_loop import LoopRequest, run_loop

    answer = {"methods": {"a": "a lookup table first", "b": "a formula"}, "why": "the record is empty"}
    doc = {"id": "planned",
           "statement": "two words",
           "parts": {"a": "one", "b": "two"},
           "objectives": [{"metric": "m", "direction": "minimize"}],
           "flow": {"orchestrate": "rules",
                    "plan": {"by": _agent(tmp_path, "good", answer)},
                    "test": {"test": ["true"]}}}
    model = ScriptedProposer(['{"artifact": "x", "why": "-"}'] * 4)
    said: list[str] = []
    run_loop(PromptProblem(TaskSpec.from_dict(doc)),
             LoopRequest(batch=WHOLE, db=str(tmp_path / "p.db"), steps=2, repair_attempts=1, critique_rounds=0, prototype=False,
                         screen_only=True, agent=("plan",)), proposer=model, log=said.append)
    assert any("plan: agent" in m and "answered" in m for m in said), said
    assert any("a lookup table first" in p for p in model.prompts), "the method is the part's brief"


def test_the_brief_names_the_problem_s_files_and_nothing_else(tmp_path):
    st = _state(tmp_path)
    box_turn("critique", _agent(tmp_path, "good"), "Q?", SCHEMA, st, home="/the/problem")
    brief = (tmp_path / "work" / "agents" / "critique" / "001" / "BRIEF.md").read_text()
    assert "THE PROBLEM'S FILES are in `/the/problem`" in brief and "nothing else" in brief


def test_with_the_prototype_off_the_coding_agent_writes_the_target(tmp_path):
    """A golden model gives a document a prototype stage; `prototype: false` must still send the
    drafts to the coding agent, not to the loop's model (D643)."""
    from flux_loop import LoopRequest, LoopState
    from flux_loop.problem import _agent_writes_prototypes

    (tmp_path / "golden.py").write_text('PORTS = [{"name": "a", "dir": "in", "bits": 4, "unsigned": True}, '
                                        '{"name": "y", "dir": "out", "bits": 4}]\n\ndef golden(a):\n    return {"y": a}\n')
    doc = {"id": "t",
           "statement": "module `t`",
           "language": "systemverilog",
           "objectives": [{"metric": "area_um2", "direction": "minimize"}],
           "flow": {"generate": {"by": "claude"}, "test": "flux rtl test {artifact} --golden {home}/golden.py"}}
    prob = PromptProblem(TaskSpec.from_dict(doc, base=tmp_path))
    assert prob.prototype() is not None and prob.prototype_agent() is not None

    def st(on):
        return LoopState(request=LoopRequest(batch=WHOLE, prototype=on), say=lambda _m: None, proposer=None, feedback=None)
    assert _agent_writes_prototypes(prob, st(True)) and not _agent_writes_prototypes(prob, st(False))


def test_codex_keeps_its_own_sandbox_on_the_host_and_flux_s_container_is_its_sandbox_inside(monkeypatch):
    """D750: Codex's bubblewrap cannot start in the container (nor on a host whose AppArmor
    refuses it user namespaces); inside Flux's sandbox the container is Codex's sandbox."""
    from flux_loop.agent import agent_spec

    monkeypatch.delenv("FLUX_SANDBOXED", raising=False)
    host = agent_spec({"preset": "codex"}).argv
    assert host[host.index("--sandbox") + 1] == "workspace-write" and "--skip-git-repo-check" in host
    monkeypatch.setenv("FLUX_SANDBOXED", "1")
    boxed = agent_spec({"preset": "codex"}).argv
    assert boxed[boxed.index("--sandbox") + 1] == "danger-full-access" and boxed[-1] == "-"
    assert "--sandbox" not in agent_spec({"preset": "claude"}).argv, "the others untouched"
