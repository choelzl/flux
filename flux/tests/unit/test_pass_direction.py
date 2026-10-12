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
