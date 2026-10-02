"""One agent session per job (D669): a generate agent keeps its session for a part until the part is
admitted (repairs and send-backs resume it with a short message); a decision box's agent is fresh
every turn (`session: turn`) or one session for the pass (`session: pass`). A fake agent stands in
for OpenCode: it keeps each session's context in a file and logs every turn."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from flux_loop import PromptProblem, TaskError, TaskSpec, request_for, run_loop
from flux_loop.agent import agent_spec

DIGITS = Path(__file__).resolve().parents[2] / "core" / "loop" / "examples" / "digits" / "problem.json"

FAKE = r'''import json, os, sys
from pathlib import Path
here = Path(sys.argv[0]).parent
mode, role = sys.argv[1], sys.argv[2]
sessions = here / "sessions"
sessions.mkdir(exist_ok=True)
digits = [str(i) for i in range(10)]
if mode == "first":
    text, artifact = Path(sys.argv[3]).read_text(), Path(sys.argv[4])
    sid = f"ses_{role}_{len(list(sessions.iterdir())) + 1}"
    context = []
else:
    sid, text, artifact = sys.argv[3], sys.argv[4], Path(sys.argv[5])
    if (here / "overflow").exists():
        print("request (90081 tokens) exceeds the available context size (58112 tokens)"); sys.exit(1)
    if not (sessions / sid).exists():
        print(f"no session {sid}", file=sys.stderr); sys.exit(2)
    context = json.loads((sessions / sid).read_text())["context"]
    cwd0 = json.loads((sessions / sid).read_text())["cwd"]
with open(here / "turns.jsonl", "a") as f:
    f.write(json.dumps({"mode": mode, "role": role, "sid": sid, "cwd": os.getcwd(), "chars": len(text), "text": text,
                        "saw_brief": any("HOW TO ANSWER" in c for c in context),
                        "same_cwd": mode == "first" or cwd0 == os.getcwd()}) + "\n")
(sessions / sid).write_text(json.dumps({"cwd": os.getcwd(), "context": context + [text]}))
if role == "critic":
    n = len([ln for ln in (here / "turns.jsonl").read_text().splitlines() if '"critic"' in ln])
    doc = {"ok": False, "issues": ["the last line must be 9 with no newline after it"]} if n == 1 else {"ok": True}
    artifact.write_text(json.dumps(doc))
elif mode == "first" and role == "gen" and "THE LAST DRAFT" not in text:
    wrong = list(digits); wrong[3] = "x"                          # the gate's failure says what to fix
    artifact.write_text("\n".join(wrong) + "\n")
elif mode == "first":
    artifact.write_text("\n".join(digits) + "\n")
else:
    artifact.write_text("\n".join(digits))                        # the repair: right, and a new text
print(json.dumps({"type": "text", "sessionID": sid, "part": {"text": "written"}}))
'''


def _fake(tmp_path: Path) -> Path:
    fake = tmp_path / "fake.py"
    fake.write_text(FAKE)
    return fake


def _agent(fake: Path, role: str, resume: bool = True, **extra) -> dict:
    spec = {"command": [sys.executable, str(fake), "first", role, "{prompt_file}", "{artifact}"],
            "output": "opencode", "timeout_s": 60, **extra}
    if resume:
        spec["resume"] = [sys.executable, str(fake), "resume", role, "{session}", "{answer}", "{artifact}"]
    return spec


def _turns(tmp_path: Path) -> list[dict]:
    p = tmp_path / "turns.jsonl"
    return [json.loads(ln) for ln in p.read_text().splitlines()] if p.exists() else []


def _digits(fake: Path, role: str = "gen", resume: bool = True, **doc) -> TaskSpec:
    d = {"id": "digits", **json.loads(DIGITS.read_text())}
    d["flow"] = {**d.get("flow", {}), "generate": {"by": _agent(fake, role, resume)}}
    d["budget"] = {"steps": 3, "repair_attempts": 2, "prototype": False}
    d.update(doc)
    return TaskSpec.from_dict(d)


def test_a_gate_repair_resumes_the_parts_session_with_a_short_message(tmp_path):
    task = _digits(_fake(tmp_path))
    said: list[str] = []
    out = run_loop(PromptProblem(task), request_for(task, db=str(tmp_path / "d.db")), proposer=None, log=said.append)
    assert out.decision is not None, out.refused
    first, repair = _turns(tmp_path)
    assert first["mode"] == "first" and repair["mode"] == "resume" and repair["sid"] == first["sid"]
    assert repair["same_cwd"] and repair["saw_brief"], "the resumed turn has the brief in its context"
    assert "FAIL line 4" in repair["text"] and "HOW TO ANSWER" not in repair["text"], "the failure, not the brief"
    assert repair["chars"] < first["chars"] / 2, (repair["chars"], first["chars"])
    assert Path(first["cwd"]).name == "digits" and Path(first["cwd"]).parent.name == "generate"
    assert any("resumed session ses_gen_1" in m for m in said), said
    from flux_loop.report import load

    turns = load(str(tmp_path / "d.db")).agent_turns
    assert [(t["box"], t["session"], t["session_id"]) for t in turns] == [
        ("generate", "fresh", "ses_gen_1"), ("generate", "resumed", "ses_gen_1")]


def test_a_critique_send_back_resumes_the_session_and_admission_ends_it(tmp_path):
    fake = _fake(tmp_path)
    d = {"id": "digits", **json.loads(DIGITS.read_text())}
    d["flow"] = {**d.get("flow", {}), "generate": {"by": _agent(fake, "genok")}, "critique": {"by": _agent(fake, "critic")}}
    d["budget"] = {"steps": 3, "repair_attempts": 1, "prototype": False, "critique_rounds": 1}
    task = TaskSpec.from_dict(d)
    out = run_loop(PromptProblem(task), request_for(task, db=""), proposer=None, log=lambda _m: None)
    assert out.decision is not None, out.refused
    gen = [t for t in _turns(tmp_path) if t["role"] == "genok"]
    assert [t["mode"] for t in gen] == ["first", "resume"] and gen[1]["sid"] == gen[0]["sid"]
    assert "CRITIQUE" in gen[1]["text"] and "the last line must be 9" in gen[1]["text"]


def test_admission_drops_the_session_so_an_improve_starts_fresh(tmp_path):
    from flux_loop.loop import _admit, _improve_step
    from flux_loop.sources import Attempt
    from flux_loop.types import Improve, LoopState

    task = _digits(_fake(tmp_path), role="genok")
    prob = PromptProblem(task)
    state = LoopState(request=request_for(task, db=""), say=lambda _m: None, proposer=None, feedback=None,
                      workdir=str(tmp_path / "trace"))
    agent = agent_spec(task.generator["agent"])
    cand, why = prob._agent_draft(Attempt(None, state), agent)
    assert cand is not None and state.part(None).sessions["generate"].id == "ses_genok_1"
    _admit(prob, state, [None], [], None, cand, prob.build(cand, None, state), "")
    assert "*" in state.admitted and not state.part(None).sessions, "admitted: the session is dropped"
    _improve_step(prob, state, Improve(cand, "make it shorter", subgoal=None, explore=True))
    first, improve = _turns(tmp_path)
    assert improve["mode"] == "first" and improve["sid"] != first["sid"], "an improve is a new agent"
    assert Path(improve["cwd"]).name == "digits-2" and not improve["saw_brief"]
    assert not state.part(None).sessions, "the improve's session ends with it"


def test_a_second_part_starts_a_fresh_session(tmp_path):
    task = _digits(_fake(tmp_path), parts={"lo": "the digits, as the task says", "hi": "the digits again"})
    out = run_loop(PromptProblem(task), request_for(task, db=""), proposer=None, log=lambda _m: None)
    assert set(out.admitted) == {"lo", "hi"}, out.refused
    turns = _turns(tmp_path)
    assert [t["mode"] for t in turns] == ["first", "resume", "first", "resume"]
    assert turns[0]["sid"] == turns[1]["sid"] != turns[2]["sid"] == turns[3]["sid"]
    assert {Path(t["cwd"]).name for t in turns} == {"lo", "hi"}


def test_an_agent_that_cannot_resume_gets_the_full_brief_again(tmp_path):
    task = _digits(_fake(tmp_path), resume=False)
    said: list[str] = []
    out = run_loop(PromptProblem(task), request_for(task, db=""), proposer=None, log=said.append)
    assert out.decision is not None, out.refused
    first, repair = _turns(tmp_path)[:2]
    assert first["mode"] == repair["mode"] == "first" and repair["sid"] != first["sid"]
    assert "THE LAST DRAFT" in repair["text"] and "FAIL line 4" in repair["text"] and "HOW TO ANSWER" in repair["text"]
    assert repair["cwd"] == first["cwd"], "the part's directory is stable across attempts"
    assert not any("resumed session" in m for m in said)


def test_generate_refuses_a_session_option():
    d = {"id": "digits", **json.loads(DIGITS.read_text())}
    d["flow"] = {**d.get("flow", {}), "generate": {"by": {"preset": "opencode", "session": "pass"}}}
    with pytest.raises(TaskError, match="one session per part until the part is admitted"):
        TaskSpec.from_dict(d)
    with pytest.raises(ValueError, match="session is one of turn, pass"):
        agent_spec({"preset": "opencode", "session": "run"})
    assert agent_spec("opencode").session == "turn" and agent_spec({"preset": "claude", "session": "pass"}).session == "pass"
    d["flow"] = {**{"id": "digits", **json.loads(DIGITS.read_text())}["flow"], "critique": {"by": {"preset": "opencode", "session": "pass"}}}
    task = TaskSpec.from_dict(d)
    from flux_loop.document import describe_flow

    assert any("one session a pass" in line for line in describe_flow(task, PromptProblem(task)))


# ---- decision boxes ---------------------------------------------------------------------------

SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}


def _state(tmp_path: Path, name: str = "work") -> SimpleNamespace:
    said: list[str] = []
    return SimpleNamespace(workdir=str(tmp_path / name), say=said.append, said=said, records=None, proposer=None,
                           lessons=[])


def test_a_pass_session_box_resumes_across_turns_and_the_next_pass_is_fresh(tmp_path):
    from flux_loop.boxes import box_turn

    spec = _agent(_fake(tmp_path), "critic", session="pass")
    st = _state(tmp_path)
    assert box_turn("critique", spec, "Is candidate A right?", SCHEMA, st) == {"ok": False, "issues": [
        "the last line must be 9 with no newline after it"]}
    assert box_turn("critique", spec, "Is candidate B right?", SCHEMA, st) == {"ok": True}
    one, two = _turns(tmp_path)
    assert one["mode"] == "first" and two["mode"] == "resume" and two["sid"] == one["sid"] and two["same_cwd"]
    assert two["saw_brief"] and "Is candidate B right?" in two["text"] and "out-002.json" in two["text"]
    assert "HOW TO ANSWER" not in two["text"] and "the same schema as before" in two["text"]
    work = tmp_path / "work" / "agents" / "critique" / "pass"
    assert (work / "out-001.json").is_file() and (work / "out-002.json").is_file()
    assert any("resumed session ses_critic_1" in m for m in st.said), st.said
    st2 = _state(tmp_path, "work2")                                   # the next pass: a new state
    box_turn("critique", spec, "Is candidate C right?", SCHEMA, st2)
    three = _turns(tmp_path)[2]
    assert three["mode"] == "first" and three["sid"] != one["sid"]


def test_a_turn_session_box_is_fresh_every_turn(tmp_path):
    from flux_loop.boxes import box_turn

    spec = _agent(_fake(tmp_path), "critic")                          # session: turn, the default
    st = _state(tmp_path)
    box_turn("critique", spec, "A?", SCHEMA, st)
    box_turn("critique", spec, "B?", SCHEMA, st)
    one, two = _turns(tmp_path)
    assert one["mode"] == two["mode"] == "first" and one["sid"] != two["sid"] and one["cwd"] != two["cwd"]


def test_a_session_out_of_context_continues_fresh(tmp_path):
    from flux_loop.boxes import box_turn

    fake = _fake(tmp_path)
    spec = _agent(fake, "critic", session="pass")
    st = _state(tmp_path)
    box_turn("critique", spec, "A?", SCHEMA, st)
    (tmp_path / "overflow").write_text("")
    assert box_turn("critique", spec, "B?", SCHEMA, st) == {"ok": True}
    one, two = _turns(tmp_path)                    # the overflowing resume logs nothing
    assert two["mode"] == "first" and two["sid"] != one["sid"] and "HOW TO ANSWER" in two["text"]
    assert "B?" in two["text"] and "out-002.json" in two["text"]
    assert any("could not be resumed (out of context)" in m for m in st.said), st.said
    assert st.agent_sessions["critique"].id == two["sid"], "the fresh session is the pass's session now"


def test_a_preset_s_executable_can_be_renamed_and_nothing_else(monkeypatch):
    """`bin` (per document) or FLUX_<PRESET>_BIN (per machine) replaces only the executable; the
    preset's arguments and resume command stay (D670)."""
    import pytest

    from flux_loop.agent import PRESETS, agent_spec

    monkeypatch.delenv("FLUX_OPENCODE_BIN", raising=False)
    assert agent_spec("opencode").argv == PRESETS["opencode"]["argv"]
    monkeypatch.setenv("FLUX_OPENCODE_BIN", "oc")
    spec = agent_spec("opencode")
    assert spec.argv[0] == "oc" and spec.argv[1:] == PRESETS["opencode"]["argv"][1:]
    assert spec.resume[0] == "oc" and spec.resume[1:] == PRESETS["opencode"]["resume"][1:]
    assert agent_spec({"preset": "opencode", "bin": "~/bin/opencode-dev"}).argv[0].endswith("/bin/opencode-dev")
    assert agent_spec({"preset": "claude", "bin": "claude-work"}).resume[0] == "claude-work"
    with pytest.raises(ValueError, match="bin"):
        agent_spec({"command": ["x"], "bin": "y"})


def test_a_preset_takes_extra_arguments(monkeypatch):
    """`args` (per document) or FLUX_<PRESET>_ARGS (per machine), e.g. OpenCode's `--agent flux`,
    go at the end, the prompt being on stdin (D670, D672); codex's `-` stays last."""
    import pytest

    from flux_loop.agent import agent_spec

    monkeypatch.delenv("FLUX_OPENCODE_BIN", raising=False)
    monkeypatch.setenv("FLUX_OPENCODE_ARGS", "--agent flux")
    spec = agent_spec("opencode")
    assert spec.argv[-2:] == ("--agent", "flux") and spec.resume[-2:] == ("--agent", "flux")
    doc = agent_spec({"preset": "opencode", "args": ["--agent", "review"]})     # the document's wins
    assert doc.argv[-2:] == ("--agent", "review")
    assert agent_spec({"preset": "codex", "args": ["-m", "o4"]}).argv[-3:] == ("-m", "o4", "-")
    monkeypatch.delenv("FLUX_OPENCODE_ARGS")
    assert "--agent" not in agent_spec("opencode").argv
    with pytest.raises(ValueError, match="list of arguments"):
        agent_spec({"preset": "opencode", "args": "--agent flux"})
