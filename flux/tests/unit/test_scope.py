"""D1022: one whole design, a pass on one scope of it. `flow.orchestrate.scope` lists the scopes, briefs
on one and checks it; each pass the orchestrator picks its direction and a scope in one decision (the
rules take every scope in turn), the design agent is told the scope and its brief, and the pass goes
on -- the same agent told the failures -- until the scope's check is clean or its steps are spent.
The whole design is gated and measured as before."""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from types import SimpleNamespace

import pytest


from flux_loop import PromptProblem, TaskError, TaskSpec, request_for, run_loop
from flux_loop.direction import decide, next_scope
from flux_loop.ledger import Kind
from flux_loop.types import Candidate

AGENT = r'''import json, sys
from pathlib import Path
here = Path(sys.argv[0]).parent
first = sys.argv[1] == "first"
sid = f"ses_{len(list(here.glob('turn-*')))}" if first else sys.argv[2]
text = Path(sys.argv[2]).read_text() if first else sys.argv[3]
n = len(list(here.glob("turn-*"))) + 1
(here / f"turn-{n}.json").write_text(json.dumps({"sid": sid, "text": text}))
Path(sys.argv[-1]).write_text(f"design {n}\\n")
print(json.dumps({"type": "text", "sessionID": sid, "part": {"text": "written"}}))
'''


def _task(tmp_path, scope) -> TaskSpec:
    """Any text passes the gate; the agent writes a new design each turn (`design N`)."""
    agent = tmp_path / "agent.py"
    agent.write_text(AGENT)
    py = sys.executable
    doc = {"id": "d", "statement": "Write a design.", "language": "text",
           "flow": {"test": "true", "generate": {"by": {
               "command": [py, str(agent), "first", "{prompt_file}", "{artifact}"],
               "resume": [py, str(agent), "resume", "{session}", "{answer}", "{artifact}"],
               "output": "opencode", "timeout_s": 60}}},
           "budget": {"steps": 6, "repair_attempts": 1, "prototype": False}}
    return replace(TaskSpec.from_dict(doc), home=str(tmp_path), scope=scope)


def _turns(tmp_path) -> list[dict]:
    return [json.loads(p.read_text()) for p in sorted(tmp_path.glob("turn-*"), key=lambda p: int(p.stem[5:]))]


LIST = "fluxpreset ndf list --spec {home}/spec"
BRIEF = "fluxpreset ndf clauses {scope} --spec {home}/spec"
CHECK = "{python} {home}/check.py {scope} {artifact}"


def _doc(**orch) -> dict:
    return {"id": "t", "statement": "s", "flow": {"test": "true", "orchestrate": orch}}


def test_the_scope_is_said_under_orchestrate_and_written_back_there():
    task = TaskSpec.from_dict(_doc(by="model", scope={"list": LIST, "brief": BRIEF, "check": CHECK}))
    assert task.scope["brief"][-4:] == ("clauses", "{scope}", "--spec", "{home}/spec") and task.scope["timeout_s"] == 120
    assert task.to_dict()["flow"]["orchestrate"] == {"by": "model", "scope": {"list": LIST.split(), "brief": BRIEF.split(),
                                                                              "check": CHECK.split()}}
    assert TaskSpec.from_dict(task.to_dict()) == task
    alone = TaskSpec.from_dict(_doc(scope={"list": LIST, "timeout_s": 30}))
    assert alone.to_dict()["flow"]["orchestrate"] == {"scope": {"list": LIST.split(), "timeout_s": 30}}
    assert "scope list" in dict(alone.commands())


@pytest.mark.parametrize("scope, said", [
    ({"brief": BRIEF}, "scope is {list:"),
    ({"list": LIST, "brief": "fluxpreset ndf clauses TADD"}, "scope.brief names the scope"),
    ({"list": LIST, "check": "true"}, "scope.check names the scope"),
    ({"list": LIST, "every": 2}, "every is not a setting"),
    ({"list": LIST, "timeout_s": 0}, "timeout_s is a number"),
])
def test_a_scope_that_cannot_work_is_refused(scope, said):
    with pytest.raises(TaskError, match=said.replace("{", r"\{")):
        TaskSpec.from_dict(_doc(scope=scope))


def test_a_loop_of_parts_has_no_scope():
    with pytest.raises(TaskError, match="one whole design"):
        TaskSpec.from_dict({**_doc(scope={"list": LIST}), "parts": ["a", "b"]})


def _state(scoped=()):
    rows = []
    for s, said in scoped:
        rows.append(SimpleNamespace(kind=Kind.SEARCH_CHOICE, detail={"pick": "improve", "scope": s}))
        if said:
            rows.append(SimpleNamespace(kind=Kind.SCOPE_CHECK, detail={"scope": s, "said": said}))
    return SimpleNamespace(records=None, request=SimpleNamespace(explore_after=2, exploration_quota=0),
                           ledger=SimpleNamespace(entries=lambda kind=None: [r for r in rows if kind in (None, r.kind)]))


def test_the_rules_take_every_scope_in_turn():
    names = ["TADD", "TSUB", "TMUL"]
    assert next_scope(_state(), names) == "TADD"
    assert next_scope(_state([("TADD", "")]), names) == "TSUB"
    assert next_scope(_state([("TADD", ""), ("TSUB", ""), ("TMUL", "")]), names) == "TADD", "then the one worked on longest ago"
    assert next_scope(_state([("TSUB", ""), ("TADD", ""), ("TMUL", ""), ("TSUB", "")]), names) == "TADD"


def test_the_orchestrator_picks_the_scope_with_the_direction_seeing_how_checks_ended():
    menu = [("TADD", "add two tiles"), ("TSUB", "subtract")]
    seen = {}

    def direction(_p, _s, lines, scope=None):
        seen.update(lines="\n".join(lines), scope=scope)
        return ("explore", "TADD still fails", "TADD")

    prob = SimpleNamespace(roles=lambda: SimpleNamespace(orchestrator=SimpleNamespace(direction=direction, dse="")))
    pick, why, scope = decide(prob, _state([("TADD", "2 failing")]), Candidate("p#1", "x"), "verified", menu=menu)
    assert (pick, scope) == ("explore", "TADD") and "TADD still fails" in why
    assert seen["scope"] == ["TADD", "TSUB"] and "  - TSUB: subtract" in seen["lines"]
    assert "the latest first, with how the scope's check ended: TADD (2 failing)." in seen["lines"]
    off = SimpleNamespace(roles=lambda: SimpleNamespace(orchestrator=SimpleNamespace(
        direction=lambda _p, _s, _l, scope=None: ("improve", "", "TNOPE"), dse="")))
    assert decide(off, _state([("TADD", "")]), Candidate("p#1", "x"), "", menu=menu)[2] == "TSUB", "off the list: the rules'"
    assert decide(prob, _state(), Candidate("p#1", "x"), "")[2] == "", "no list, no scope"


def test_a_pass_on_a_scope_goes_on_until_its_check_is_clean(tmp_path):
    db = str(tmp_path / "d.db")
    py = sys.executable
    (tmp_path / "check.py").write_text(       # fails twice for TADD, then clean: the pass must go on
        "import pathlib, sys\n"
        "n = pathlib.Path(sys.argv[3]); k = int(n.read_text()) if n.exists() else 0; n.write_text(str(k + 1))\n"
        "print(f'{max(0, 2 - k)} failing of 3'); print('TADD-CONTRACT-001 failed' if k < 2 else 'all pass')\n")
    task = _task(tmp_path, {"list": (py, "-c", "print('TADD add two tiles'); print('TSUB subtract')"),
                            "brief": (py, "-c", "import sys; print('MUST add', sys.argv[1])", "{scope}"),
                            "check": (py, str(tmp_path / "check.py"), "{scope}", "{artifact}", str(tmp_path / "count")),
                            "timeout_s": 60})
    run_loop(PromptProblem(task), request_for(task, db=db), proposer=None, log=lambda _m: None)
    before = len(_turns(tmp_path))
    said: list[str] = []
    run_loop(PromptProblem(task), request_for(task, db=db), proposer=None, log=said.append)
    assert any("direction: improve d#1 on TADD" in m for m in said), said
    ends = [m.strip() for m in said if m.strip().startswith("scope TADD:")]
    assert [e.split(":")[1].split(" on ")[0].strip() for e in ends] == ["2 failing", "1 failing", "clean"], ends
    turns = _turns(tmp_path)[before:]
    told = "\n".join(t["text"] for t in turns)
    assert "THIS PASS'S SCOPE: TADD" in told and "MUST add TADD" in told and "TADD-CONTRACT-001 failed" in told
    assert len({t["sid"] for t in turns}) == 1, "the same agent session to the pass's end"
    said.clear()
    run_loop(PromptProblem(task), request_for(task, db=db), proposer=None, log=said.append)
    assert any(" on TSUB" in m for m in said if "direction:" in m), "then the other scope"


def test_a_note_leads_the_pass_of_one_design_it_reaches(tmp_path):
    """D1023: a loop of one design drained no note before its pass -- a note reached the record at the
    pass's end and the prompts only a pass later, as background. Now the pass's decision and the
    design agent's brief lead with it."""
    from flux_feedback import scripted_channel

    db = str(tmp_path / "d.db")
    task = _task(tmp_path, {})
    run_loop(PromptProblem(task), request_for(task, db=db), proposer=None, log=lambda _m: None)
    before = len(_turns(tmp_path))
    run_loop(PromptProblem(task), request_for(task, db=db), proposer=None, log=lambda _m: None,
             feedback=scripted_channel("use a lookup table, not a loop"))
    first = _turns(tmp_path)[before]["text"]
    assert first.lstrip().startswith("HUMAN GUIDANCE") and "use a lookup table, not a loop" in first[:600], first[:600]
    assert "FOLLOW THEM" in first and "advisory" not in first


def test_a_note_mid_pass_leads_the_resumed_sessions_next_message(tmp_path):
    """D1023: a note typed while the pass works -- its scope's retry resumes the same agent -- is the head
    of that agent's next message, not only of a fresh brief it will not read again."""
    from flux_feedback import Note

    class Later:                                  # the note comes once the agent has taken a turn
        sent = False

        def drain(self):
            if self.sent or len(list(tmp_path.glob("turn-*"))) <= before:
                return []
            self.sent = True
            return [Note("keep the adder ripple-carry", 0.0)]

    db = str(tmp_path / "d.db")
    py = sys.executable
    (tmp_path / "check.py").write_text("import pathlib, sys\n"
                                       "n = pathlib.Path(sys.argv[1]); k = int(n.read_text()) if n.exists() else 0\n"
                                       "n.write_text(str(k + 1)); print(f'{1 if k == 0 else 0} failing of 1')\n")
    task = _task(tmp_path, {"list": (py, "-c", "print('TADD add')"),
                            "check": (py, str(tmp_path / "check.py"), str(tmp_path / "count"), "{scope}"), "timeout_s": 60})
    run_loop(PromptProblem(task), request_for(task, db=db), proposer=None, log=lambda _m: None)
    before = len(_turns(tmp_path))
    run_loop(PromptProblem(task), request_for(task, db=db), proposer=None, log=lambda _m: None, feedback=Later())
    turns = _turns(tmp_path)[before:]
    assert len(turns) >= 2 and len({t["sid"] for t in turns}) == 1, turns
    assert "keep the adder ripple-carry" not in turns[0]["text"]
    assert turns[1]["text"].startswith("HUMAN GUIDANCE") and "keep the adder ripple-carry" in turns[1]["text"][:600]
