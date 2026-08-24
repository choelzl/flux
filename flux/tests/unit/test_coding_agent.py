"""A coding agent as the generator (D575): a terminal tool that takes the brief and writes
the artifact, the loop's build, test and judge around it."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

from flux_loop import PromptProblem, TaskError, TaskSpec, request_for, run_loop
from flux_loop.agent import agent_argv, missing_agent
from flux_loop.task import describe_flow

DIGITS = Path(__file__).resolve().parents[2] / "core" / "loop" / "examples" / "digits.task.json"

FAKE_AGENT = '''
import sys, re
from pathlib import Path
mode, prompt_file, artifact = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
brief = prompt_file.read_text()
assert sys.stdin.read() == "", "the loop must hand the agent a closed stdin"
assert "REPLY SHAPE" not in brief, "the model half's reply shape is not an agent's brief"
with open(Path(sys.argv[0]).with_name("seen.txt"), "a") as f:   # beside the script: every brief this agent read
    f.write(brief + "\\n=====\\n")
lines = [str(i) for i in range(10)]
if "FAIL line" not in brief:
    lines[3] = "x"                       # the first draft gets line 4 wrong; the repair prompt names it
if mode == "file":
    artifact.write_text("\\n".join(lines) + "\\n"); print("written")
elif mode == "stdout":
    print("here it is:\\n```\\n" + "\\n".join(lines) + "\\n```")
elif mode == "fail":
    print("could not do it", file=sys.stderr); sys.exit(3)
'''


def _doc(tmp_path: Path, mode: str) -> dict:
    fake = tmp_path / "agent.py"
    fake.write_text(FAKE_AGENT)
    doc = json.loads(DIGITS.read_text())
    doc["generator"] = {"agent": {"command": ["{python}", str(fake), mode, "{prompt_file}", "{artifact}"], "timeout_s": 60}}
    doc["budget"] = {"steps": 2, "repair_attempts": 2, "prototype": False}
    return doc


def test_the_agent_writes_the_artifact_and_is_repaired_from_the_failure(tmp_path):
    task = TaskSpec.from_dict(_doc(tmp_path, "file"))
    prob = PromptProblem(task)
    assert prob.generator(None, None).name == "agent:agent.py"
    assert any("coding agent `agent.py`" in line for line in describe_flow(task, prob))
    said = []
    out = run_loop(prob, request_for(task, db=str(tmp_path / "d.db")), proposer=None, log=said.append)
    assert out.decision is not None and out.decision.candidate.artifact.split() == [str(i) for i in range(10)]
    assert out.decision.candidate.knobs["generator"] == "agent:agent.py"
    assert any("ADMITTED" in m for m in said)
    seen = (tmp_path / "seen.txt").read_text()
    assert seen, "the agent read a brief"
    last = seen.split("=====")[-2]
    assert "HOW TO ANSWER" in last and "Write the complete text artifact" in last
    assert "THE LAST DRAFT" in last and "FAIL line 4" in last, "the repair brief carries the prior and the failure"


def test_the_agent_may_print_the_artifact_instead(tmp_path):
    task = TaskSpec.from_dict(_doc(tmp_path, "stdout"))
    out = run_loop(PromptProblem(task), request_for(task, db=""), proposer=None, log=lambda _m: None)
    assert out.decision is not None and out.decision.candidate.artifact.split() == [str(i) for i in range(10)]


def test_an_agent_that_fails_is_a_refusal_with_its_words(tmp_path):
    task = TaskSpec.from_dict(_doc(tmp_path, "fail"))
    out = run_loop(PromptProblem(task), request_for(task, db=""), proposer=None, log=lambda _m: None)
    assert out.decision is None
    assert any("exited 3" in why and "could not do it" in why for _n, why in out.refused), out.refused


def test_the_presets_and_the_missing_binary():
    for name in ("claude", "codex", "opencode"):
        tool, argv, timeout = agent_argv(name)
        assert tool == name and argv[0] == name and "{prompt}" in argv and timeout == 1800.0
    tool, argv, timeout = agent_argv({"preset": "codex", "timeout_s": 60})
    assert tool == "codex" and timeout == 60.0
    with pytest.raises(ValueError, match="not a preset"):
        agent_argv("cursor")
    with pytest.raises(TaskError, match="generator.agent"):
        TaskSpec.from_dict({**json.loads(DIGITS.read_text()), "generator": {"agent": "cursor"}})
    doc = {**json.loads(DIGITS.read_text()), "flow": {"generate": {"agent": "claude"}}}
    task = TaskSpec.from_dict(doc)
    assert task.generator == {"agent": "claude"}
    missing = PromptProblem(task).tools_missing()
    assert ("claude" in missing) == (shutil.which("claude") is None)
    assert missing_agent({"command": [sys.executable, "x.py"]}) == []
    assert missing_agent("opencode") == ([] if shutil.which("opencode") else ["opencode"])
