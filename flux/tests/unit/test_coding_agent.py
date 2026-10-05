"""A coding agent as the generator (D575): a terminal tool that takes the brief and writes
the artifact, the loop's build, test and judge around it."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

from flux_loop import PromptProblem, TaskError, TaskSpec, request_for, run_loop
from flux_loop.agent import DECIDE, agent_spec, missing_agent, question_in
from flux_loop.document import describe_flow

DIGITS = Path(__file__).resolve().parents[2] / "core" / "loop" / "examples" / "digits" / "problem.json"

FAKE_AGENT = '''
import sys, re
from pathlib import Path
mode, prompt_file, artifact = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
brief = prompt_file.read_text()
assert sys.stdin.read() == "", "the loop must hand the agent a closed stdin"
import os
assert os.environ.get("PWD") == str(Path.cwd()), "PWD is the work directory (OpenCode reads it, D586)"
assert "REPLY SHAPE" not in brief, "the model half's reply shape is not an agent's brief"
with open(Path(sys.argv[0]).with_name("seen.txt"), "a") as f:   # beside the script: every brief this agent read
    f.write(brief + "\\n=====\\n")
lines = [str(i) for i in range(10)]
if "THE LAST DRAFT" not in brief:
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
    doc = {"id": "digits", **json.loads(DIGITS.read_text())}
    doc["flow"] = {**doc.get("flow", {}), "generate": {"by": {"command": ["{python}", str(fake), mode, "{prompt_file}", "{artifact}"], "timeout_s": 60}}}
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
    first = seen.split("=====")[0]
    assert "raw tools: they are denied" in first and "comes back to you" in first and "THE GATE" not in first, \
        "D673: the agent writes, the loop runs the gate and comes back with its output"
    assert "flux probe gate FILE" in first, "D678: and it may check its file through the loop's own gate"


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
        a = agent_spec(name)
        assert a.tool == name and a.argv[0] == name and "{prompt}" not in a.argv and a.timeout_s == 1800.0
        assert a.questions == "decide" and a.max_questions == 2
    assert agent_spec("opencode").resume[-2:] == ("--session", "{session}") and agent_spec("opencode").output == "opencode"
    assert "--dir" in agent_spec("opencode").argv and "{workdir}" in agent_spec("opencode").argv
    assert "AskUserQuestion" in agent_spec("claude").argv and "--resume" in agent_spec("claude").resume
    for argv in (agent_spec("claude").argv, agent_spec("claude").resume):    # D673: a shell without the design tools
        assert argv[argv.index("--allowedTools") + 1] == "Bash" and "Bash(yosys:*)" in argv and "Bash(bash:*)" in argv
    a = agent_spec({"preset": "codex", "timeout_s": 60, "questions": "model"})
    assert a.tool == "codex" and a.timeout_s == 60.0 and a.questions == "model" and a.resume is None
    with pytest.raises(ValueError, match="not an agent here"):
        agent_spec("cursor")
    with pytest.raises(ValueError, match="questions is one of decide, model, operator"):
        agent_spec({"preset": "opencode", "questions": "ask-me"})
    with pytest.raises(ValueError, match="quetions is not one of"):
        agent_spec({"preset": "opencode", "quetions": "model"})
    with pytest.raises(TaskError, match="flow.generate.by"):
        TaskSpec.from_dict({**{"id": "digits", **json.loads(DIGITS.read_text())}, "flow": {**{"id": "digits", **json.loads(DIGITS.read_text())}.get("flow", {}), "generate": {"by": "cursor"}}})
    doc = {**{"id": "digits", **json.loads(DIGITS.read_text())}, "flow": {**{"id": "digits", **json.loads(DIGITS.read_text())}.get("flow", {}), "generate": {"by": "claude"}}}
    task = TaskSpec.from_dict(doc)
    assert task.generator == {"agent": "claude"}
    missing = PromptProblem(task).tools_missing()
    assert ("claude" in missing) == (shutil.which("claude") is None)
    assert missing_agent({"command": [sys.executable, "x.py"]}) == []
    assert missing_agent("opencode") == ([] if shutil.which("opencode") else ["opencode"])



# ---- D585: the agent's questions, answered by the document's policy ----------------------

ASKING_AGENT = '''
import json, sys
from pathlib import Path
mode, prompt_file, artifact = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
here = Path(sys.argv[0]).parent
brief = prompt_file.read_text()
with open(here / "briefs.txt", "a") as f:
    f.write(brief + "\\n=====\\n")
if mode == "always" or "THE ANSWER:" not in brief:
    print("I can write the digits in two ways.\\n\\nWhich base should the digits be in, 10 or 16?")
    sys.exit(0)
artifact.write_text("\\n".join(str(i) for i in range(10)) + "\\n"); print("written")
'''

RESUMING_AGENT = '''
import json, sys
from pathlib import Path
args = sys.argv[1:]
here = Path(sys.argv[0]).parent
if args[0] == "resume":                          # resume <session> <answer> <artifact>
    session, answer, artifact = args[1], args[2], Path(args[3])
    (here / "resumed.txt").write_text(session + "|" + answer)
    artifact.write_text("\\n".join(str(i) for i in range(10)) + "\\n")
    print(json.dumps({"type": "text", "sessionID": session, "part": {"text": "Done."}}))
else:
    print(json.dumps({"type": "step_start", "sessionID": "ses_42"}))
    print(json.dumps({"type": "text", "sessionID": "ses_42", "part": {"text": "Which base, 10 or 16?"}}))
'''


def _asking(tmp_path: Path, mode: str, questions: str, **extra) -> dict:
    fake = tmp_path / "ask.py"
    fake.write_text(ASKING_AGENT)
    doc = {"id": "digits", **json.loads(DIGITS.read_text())}
    doc["flow"] = {**doc.get("flow", {}), "generate": {"by": {"command": ["{python}", str(fake), mode, "{prompt_file}", "{artifact}"],
                                                "timeout_s": 60, "questions": questions, **extra}}}
    doc["budget"] = {"steps": 1, "repair_attempts": 1, "prototype": False}
    return doc


def test_a_question_the_loop_cannot_resume_is_answered_in_a_fresh_brief(tmp_path):
    task = TaskSpec.from_dict(_asking(tmp_path, "once", "decide"))
    said: list[str] = []
    out = run_loop(PromptProblem(task), request_for(task, db=""), proposer=None, log=said.append)
    assert out.decision is not None and out.decision.candidate.artifact.split() == [str(i) for i in range(10)]
    asked = out.decision.candidate.meta["questions"]
    assert asked == [{"question": "I can write the digits in two ways.\n\nWhich base should the digits be in, 10 or 16?",
                      "answer": DECIDE, "by": "decide"}]
    briefs = (tmp_path / "briefs.txt").read_text().split("=====")
    assert "Nobody answers questions during this run" in briefs[0]
    assert "YOU ASKED: " in briefs[1] and "THE ANSWER: Nobody is available" in briefs[1]
    assert any("asks: I can write the digits" in m for m in said) and any("answered by the decide" in m for m in said)


def test_the_model_answers_as_the_designer_and_the_brief_invites_one_question(tmp_path):
    from flux_llm import ScriptedProposer

    task = TaskSpec.from_dict(_asking(tmp_path, "once", "model"))
    proposer = ScriptedProposer(["Base 10: the gate checks the decimal digits 0 to 9."])
    out = run_loop(PromptProblem(task), request_for(task, db=""), proposer=proposer, log=lambda _m: None)
    assert out.decision is not None and out.decision.candidate.meta["questions"][0]["by"] == "model"
    briefs = (tmp_path / "briefs.txt").read_text().split("=====")
    assert "you may end your reply with ONE question" in briefs[0]
    assert "THE ANSWER: Base 10" in briefs[1]
    assert "THE AGENT ASKS:\nI can write the digits" in proposer.prompts[0]


class _Operator:
    """A prompt line that answers once the question has been put to it (the log shows it)."""

    def __init__(self, text: str):
        self.text, self.armed = text, False

    def say(self, message: str) -> None:
        self.armed = self.armed or message.startswith("QUESTION from the coding agent")

    def drain(self):
        import time

        from flux_feedback import Note

        if not self.armed or self.text is None:
            return []
        note, self.text = Note(self.text, time.time()), None
        return [note]


def test_the_operator_answers_at_the_prompt_line(tmp_path):
    task = TaskSpec.from_dict(_asking(tmp_path, "once", "operator", wait_s=5))
    operator = _Operator("hexadecimal, please")
    out = run_loop(PromptProblem(task), request_for(task, db=""), proposer=None, log=operator.say, feedback=operator)
    got = out.decision.candidate.meta["questions"][0]
    assert got["by"] == "operator" and got["answer"] == "hexadecimal, please"


def test_an_agent_that_keeps_asking_is_refused_after_max_questions(tmp_path):
    task = TaskSpec.from_dict(_asking(tmp_path, "always", "decide", max_questions=2))
    out = run_loop(PromptProblem(task), request_for(task, db=""), proposer=None, log=lambda _m: None)
    assert out.decision is None
    assert any("still asking after 2 answer(s)" in why for _n, why in out.refused), out.refused


def test_the_answer_resumes_the_agents_own_session(tmp_path):
    fake = tmp_path / "resume.py"
    fake.write_text(RESUMING_AGENT)
    doc = {"id": "digits", **json.loads(DIGITS.read_text())}
    doc["flow"] = {**doc.get("flow", {}), "generate": {"by": {"command": ["{python}", str(fake), "first", "{artifact}"],
                                                "resume": ["{python}", str(fake), "resume", "{session}", "{answer}", "{artifact}"],
                                                "output": "opencode", "timeout_s": 60}}}
    doc["budget"] = {"steps": 1, "repair_attempts": 1, "prototype": False}
    task = TaskSpec.from_dict(doc)
    out = run_loop(PromptProblem(task), request_for(task, db=""), proposer=None, log=lambda _m: None)
    assert out.decision is not None
    session, answer = (tmp_path / "resumed.txt").read_text().split("|", 1)
    assert session == "ses_42" and answer == DECIDE


def test_what_counts_as_a_question():
    assert question_in("Which base, 10 or 16?") == "Which base, 10 or 16?"
    assert question_in("Pick one:\n\nWhich colour?\n- A) red\n- B) blue") == "Pick one:\n\nWhich colour?\n- A) red\n- B) blue"
    assert question_in("Done: the file is written.") is None
    assert question_in("```\nx = a ? b : c  // why?\n```\nWritten.") is None, "a ? in code is not a question"
    assert question_in("") is None


def test_an_agent_that_ends_without_writing_is_nudged(tmp_path):
    """An agent that ends its turn without writing is told to write the file now; a file it was
    given to edit and left unchanged counts as not written (D618)."""
    from flux_loop.agent import NUDGES, converse

    fake = tmp_path / "lazy.py"
    fake.write_text("import sys\nbrief, out = sys.argv[1], sys.argv[2]\n"
                    "if 'You have not' in brief:\n    open(out, 'w').write('done\\n')\nprint('thinking...')\n")
    spec = agent_spec({"command": ["{python}", str(fake), "{prompt}", "{artifact}"]})
    art, said = tmp_path / "out.txt", []
    subs = {"prompt": "write out.txt", "artifact": str(art), "python": sys.executable, "workdir": str(tmp_path)}
    turn, _ = converse(spec, subs, workdir=tmp_path, artifact=art, answer=lambda q: ("", "nobody"), say=said.append)
    assert art.read_text() == "done\n" and any("nudged (1 of" in m for m in said)
    art.write_text("given\n")                                     # a file to edit, left as it was
    said.clear()
    lazier = tmp_path / "lazier.py"
    lazier.write_text("print('still thinking')\n")
    spec2 = agent_spec({"command": ["{python}", str(lazier)]})
    converse(spec2, subs, workdir=tmp_path, artifact=art, answer=lambda q: ("", "nobody"), say=said.append)
    assert sum("nudged" in m for m in said) == NUDGES


def test_an_agent_that_ran_out_of_context_continues_in_a_fresh_session(tmp_path):
    """A session that outgrew the server's context window is not resumed; a fresh session starts
    from the brief and the file (D618)."""
    from flux_loop.agent import converse, overflowed, Turn

    assert overflowed(Turn(False, 1, "", stdout='{"message":"request (73556 tokens) exceeds the available context size (58112 tokens)"}'))
    assert not overflowed(Turn(True, 0, "the file is written"))
    fake = tmp_path / "full.py"
    fake.write_text("import sys\nbrief, out = sys.argv[1], sys.argv[2]\n"
                    "if 'ran out of context' in brief:\n    open(out, 'w').write('done\\n'); print('written')\n"
                    "else:\n    print('request (90081 tokens) exceeds the available context size (58112 tokens)'); sys.exit(1)\n")
    spec = agent_spec({"command": ["{python}", str(fake), "{prompt}", "{artifact}"]})
    art, said = tmp_path / "out.txt", []
    subs = {"prompt": "write out.txt", "artifact": str(art), "python": sys.executable, "workdir": str(tmp_path)}
    converse(spec, subs, workdir=tmp_path, artifact=art, answer=lambda q: ("", "nobody"), say=said.append)
    assert art.read_text() == "done\n" and any("ran out of context; a fresh session" in m for m in said)


def test_opencode_is_denied_the_design_tools_in_its_inline_config():
    """D673: the shell allowed, the deny list merged into OPENCODE_CONFIG_CONTENT, the machine's
    own keys kept."""
    from flux_loop.agent import DENIED, _config_env

    spec = agent_spec("opencode")
    env = _config_env(spec, {"OPENCODE_CONFIG_CONTENT": '{"permission": {"edit": "allow"}, "model": "m"}'})
    cfg = json.loads(env["OPENCODE_CONFIG_CONTENT"])
    assert cfg["model"] == "m" and cfg["permission"]["edit"] == "allow"
    assert cfg["permission"]["external_directory"] == "allow", "D710: `opencode run` cannot ask for a path outside its folder"
    bash = cfg["permission"]["bash"]
    assert list(bash)[0] == "*" and bash["*"] == "allow"            # first: a later, narrower rule wins
    assert bash["yosys"] == bash["yosys *"] == bash["flux rtl *"] == "deny" and len(bash) == 1 + 2 * len(DENIED)
    assert _config_env(agent_spec("claude"), {}) == {}
