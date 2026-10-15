"""D999: a pass decides its direction once. A try that gives no design -- refused, or the design
agent out of time -- goes on with the same decision; an agent stopped at its time limit resumes its
own session to finish the design instead of starting a new one."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from flux_loop import PromptProblem, TaskSpec, request_for, run_loop
from test_agent_sessions import DIGITS, _digits, _fake

SLOW = r'''import json, sys, time
from pathlib import Path
here = Path(sys.argv[0]).parent
mode = sys.argv[1]
text = Path(sys.argv[2]).read_text() if mode == "first" else sys.argv[3]
artifact = Path(sys.argv[-1])
with open(here / "turns.jsonl", "a") as f:
    f.write(json.dumps({"mode": mode, "text": text}) + "\n")
print(json.dumps({"type": "text", "sessionID": "ses_slow", "part": {"text": "working"}}), flush=True)
if mode == "first":
    artifact.unlink(missing_ok=True)
    time.sleep(30)                                   # past its time limit: stopped, nothing written
else:
    artifact.write_text("\n".join(str(i) for i in range(10)) + "\n")
'''


def _turns(tmp_path: Path) -> list[dict]:
    p = tmp_path / "turns.jsonl"
    return [json.loads(ln) for ln in p.read_text().splitlines()] if p.exists() else []


def test_a_design_agent_out_of_time_resumes_its_session_to_finish(tmp_path):
    slow = tmp_path / "slow.py"
    slow.write_text(SLOW)
    d = {"id": "digits", **json.loads(DIGITS.read_text())}
    d["flow"] = {**d.get("flow", {}), "generate": {"by": {
        "command": [sys.executable, str(slow), "first", "{prompt_file}", "{artifact}"],
        "resume": [sys.executable, str(slow), "resume", "{session}", "{answer}", "{artifact}"],
        "output": "opencode", "timeout_s": 2}}}
    d["budget"] = {"steps": 3, "repair_attempts": 2, "prototype": False}
    task = TaskSpec.from_dict(d)
    out = run_loop(PromptProblem(task), request_for(task, db=str(tmp_path / "d.db")), proposer=None, log=lambda _m: None)
    turns = _turns(tmp_path)
    assert [t["mode"] for t in turns[:2]] == ["first", "resume"], turns
    assert "RAN PAST ITS TIME LIMIT" in turns[1]["text"] and "Continue where you stopped" in turns[1]["text"]
    assert out.decision is not None, out.refused


def test_a_pass_decides_its_direction_once_and_goes_on_with_it(tmp_path):
    db = str(tmp_path / "d.db")
    task = _digits(_fake(tmp_path), role="genok")
    run_loop(PromptProblem(task), request_for(task, db=db), proposer=None, log=lambda _m: None)
    said: list[str] = []
    run_loop(PromptProblem(task), request_for(task, db=db), proposer=None, log=said.append)
    # the fake agent writes the same digits each time: refused as a repeat, tried again -- one decision
    assert sum("direction: improve" in m for m in said) == 1, [m for m in said if "direction" in m]
    assert any("the pass goes on with" in m for m in said), said


NEVER = SLOW.replace('''if mode == "first":
    artifact.unlink(missing_ok=True)
    time.sleep(30)                                   # past its time limit: stopped, nothing written
else:
    artifact.write_text("\\n".join(str(i) for i in range(10)) + "\\n")''', '''artifact.write_text("0\\n1\\n")                       # a partial design, then past its time limit every turn
print(json.dumps({"type": "text", "sessionID": "ses_slow", "part": {"text": "still tiling the adder"}}), flush=True)
time.sleep(30)''')


def test_a_design_agent_out_of_time_on_every_continue_ends_the_pass_with_its_notes(tmp_path):
    """D1026, D1100: `budget.agent_continues` full turns past its limit, then the pass gives up -- no
    design built from what it left, no other turn; why, its last words and its partial file on the record."""
    assert NEVER != SLOW
    slow = tmp_path / "slow.py"
    slow.write_text(NEVER)
    d = {"id": "digits", **json.loads(DIGITS.read_text())}
    d["flow"] = {**d.get("flow", {}), "generate": {"by": {
        "command": [sys.executable, str(slow), "first", "{prompt_file}", "{artifact}"],
        "resume": [sys.executable, str(slow), "resume", "{session}", "{answer}", "{artifact}"],
        "output": "opencode", "timeout_s": 3}}}           # D1070: a loaded machine starts Python in over a second
    d["budget"] = {"steps": 9, "repair_attempts": 9, "prototype": False, "agent_continues": 3, "agent_grace_s": 0}   # D1088: no final turn
    task = TaskSpec.from_dict(d)
    said: list[str] = []
    out = run_loop(PromptProblem(task), request_for(task, db=str(tmp_path / "d.db")), proposer=None, log=said.append)
    assert [t["mode"] for t in _turns(tmp_path)] == ["first", "resume", "resume"], "three full turns: its first and two continues"
    assert "THIS IS YOUR LAST FULL TURN" in _turns(tmp_path)[2]["text"], "the last continue says so (D1088)"
    assert out.decision is None and out.stopped.startswith("out of time"), out.stopped
    why = " ".join(r for _n, r in out.refused)
    assert "OUT OF TIME" in why and "still tiling the adder" in why and "partial design of 2 lines" in why, why
    assert any(x.startswith("[out of time]") for x in out.lessons)


def test_a_design_agent_out_of_time_gets_a_final_turn_to_hand_in_its_design(tmp_path):
    """D1088: its continues used up, a short last turn in the same session -- no more experiments, the design as it
    stands written -- and what it writes is judged as any draft: the pass has something to check."""
    slow = tmp_path / "slow.py"
    slow.write_text(NEVER)
    d = {"id": "digits", **json.loads(DIGITS.read_text())}
    d["flow"] = {**d.get("flow", {}), "generate": {"by": {
        "command": [sys.executable, str(slow), "first", "{prompt_file}", "{artifact}"],
        "resume": [sys.executable, str(slow), "resume", "{session}", "{answer}", "{artifact}"],
        "output": "opencode", "timeout_s": 3}}}
    d["budget"] = {"steps": 3, "repair_attempts": 2, "prototype": False, "agent_continues": 2, "agent_grace_s": 3}
    task = TaskSpec.from_dict(d)
    said: list[str] = []
    out = run_loop(PromptProblem(task), request_for(task, db=str(tmp_path / "d.db")), proposer=None, log=said.append)
    turns = _turns(tmp_path)
    assert [t["mode"] for t in turns][:3] == ["first", "resume", "resume"], turns
    assert "TIME IS UP" in turns[2]["text"] and "Do not experiment" in turns[2]["text"], turns[2]["text"][:300]
    assert any("a final 3 s turn to hand in its design" in m for m in said) and any("handed in" in m for m in said), said
    why = " ".join(r for _n, r in out.refused)
    assert "OUT OF TIME" not in why and not out.stopped.startswith("out of time"), (out.stopped, why)


def test_a_design_agent_that_cannot_start_stops_the_pass_instead_of_trying_again(tmp_path):
    """D1092: its program missing -- exit 2 at once, nothing written -- is not a design that failed: the pass stops,
    says why, and spends no more attempts on it."""
    d = {"id": "digits", **json.loads(DIGITS.read_text())}
    d["flow"] = {**d.get("flow", {}), "generate": {"by": {
        "command": [sys.executable, str(tmp_path / "missing.py"), "{prompt_file}", "{artifact}"], "output": "opencode", "timeout_s": 30}}}
    d["budget"] = {"steps": 6, "repair_attempts": 6, "prototype": False}
    task = TaskSpec.from_dict(d)
    said: list[str] = []
    out = run_loop(PromptProblem(task), request_for(task, db=str(tmp_path / "d.db")), proposer=None, log=said.append)
    assert out.stopped.startswith("the design agent cannot start") and "missing.py" in out.stopped, out.stopped
    assert sum("cannot start" in m and "pass stops" in m for m in said) == 1, [m for m in said if "cannot start" in m]
