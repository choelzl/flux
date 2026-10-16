"""D1122-D1125: the author's work said as lines (what it read, wrote and ran, its check), its question asked of
the person and answered through the inbox beside its journal, its decisions written beside the document (or read
from the document), and its own skills placed where an agent looks -- with a stand-in coding agent."""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

import pytest
import yaml

from flux_loop.author import (AUTHOR_SKILLS, Ask, brief, decisions_of_document, drive, install_author_skills,
                              question_options, read_decisions)

DOC = {"statement": "Echo the input. A small text problem.", "language": "text", "flow": {"test": "true"},
       "objectives": [{"metric": "size", "direction": "minimize"}], "budget": {"steps": 2}}
DECIDED = [{"step": "prompt", "what": "Echo, in text", "why": "the ask says so"},
           {"step": "checks", "what": "No check but true", "why": "nothing to test against"}]

# an OpenCode-shaped agent: its tool calls streamed as JSON lines; asked (no answer yet in its brief), it ends its
# turn on a question and writes nothing; answered, it writes the document and its decisions
AGENT = r'''
import json, sys
from pathlib import Path
brief = Path(sys.argv[1]).read_text()
work = Path(sys.argv[2])
def ev(x): print(json.dumps(x), flush=True)
ev({"type": "tool_use", "part": {"tool": "read", "state": {"input": {"filePath": str(work / "library/spec.md")}, "output": "the spec"}}})
ev({"type": "tool_use", "part": {"tool": "bash", "state": {"input": {"command": "ls applications"}, "output": "mul8"}}})
if "ASK" in brief and "THE ANSWER:" not in brief:
    ev({"type": "text", "part": {"text": "The spec is unclear.\n\nWhich signedness?\n- Signed (spec 2)\n- Unsigned (spec 4)"}})
    sys.exit(0)
answer = brief.split("THE ANSWER:")[-1].strip().splitlines()[0] if "THE ANSWER:" in brief else "none"
doc = DOC | {"contract": "answered: " + answer}
(work / "problem.yaml").write_text(yaml.safe_dump(doc))
ev({"type": "tool_use", "part": {"tool": "write", "state": {"input": {"filePath": str(work / "problem.yaml")}, "output": "ok"}}})
if "NO DECISIONS" not in brief:
    (work / "decisions.json").write_text(json.dumps(DECIDED))
ev({"type": "text", "part": {"text": "written"}})
'''


class _NoRun(Exception):
    pass


def _no_run(*_a):
    raise _NoRun()


def _agent(tmp_path: Path) -> dict:
    fake = tmp_path / "agent.py"
    fake.write_text(f"import yaml\nDOC = {DOC!r}\nDECIDED = {DECIDED!r}\n" + AGENT)
    return {"command": [sys.executable, str(fake), "{prompt_file}", "{workdir}"], "output": "opencode", "timeout_s": 60}


def _rows(journal: Path) -> list[dict]:
    path = journal / "activity.jsonl"
    return [json.loads(x) for x in path.read_text().splitlines()] if path.is_file() else []


def _ask(tmp_path: Path, prompt: str, **kw) -> Ask:
    work = tmp_path / "w"
    (work / "library").mkdir(parents=True)
    (work / "library" / "spec.md").write_text("# spec")
    return Ask(prompt=prompt, workdir=work, inputs=[Path("library/spec.md")], author=_agent(tmp_path), no_run=True,
               journal=work / "runs" / "author", **kw)


def test_the_authors_tool_calls_its_check_and_decisions_are_lines(tmp_path):
    ask = _ask(tmp_path, "echo")
    with pytest.raises(_NoRun):
        drive(ask, run_pass=_no_run, say=lambda _m: None)
    rows = _rows(ask.journal)
    said = [(r["k"], r["text"]) for r in rows]
    assert ("read", "spec.md") in said and ("list", "applications") in said, said
    assert ("write", "problem.yaml") in said
    check = next(r for r in rows if r["k"] == "check")
    assert check["tone"] == "ok" and "passed" in check["text"]
    assert ("note", "wrote decisions.json: 2 decisions") in said
    assert read_decisions(ask.workdir) == (DECIDED, "")
    for name in AUTHOR_SKILLS:                      # D1123: its skills where it looks, never the document's
        assert (ask.workdir / ".claude" / "skills" / name / "SKILL.md").is_file()
    assert "skills" not in yaml.safe_load((ask.workdir / "problem.yaml").read_text())


def test_a_question_is_asked_of_the_person_and_the_answer_reaches_the_agent(tmp_path):
    ask = _ask(tmp_path, "ASK echo", questions="operator", wait_s=30)

    def person():
        for _ in range(300):
            ask_row = next((r for r in _rows(ask.journal) if r["k"] == "ask"), None)
            if ask_row:
                assert ask_row["options"] == ["Signed (spec 2)", "Unsigned (spec 4)"]
                with (ask.journal / "inbox.jsonl").open("a") as fh:
                    fh.write(json.dumps({"id": "a1", "text": "Signed (spec 2)", "by": "bob", "t": time.time()}) + "\n")
                return
            time.sleep(0.05)

    t = threading.Thread(target=person)
    t.start()
    with pytest.raises(_NoRun):
        drive(ask, run_pass=_no_run, say=lambda _m: None)
    t.join(5)
    said = [(r["k"], r["text"], r.get("by")) for r in _rows(ask.journal)]
    assert ("answer", "Signed (spec 2)", "you") in said, said
    assert yaml.safe_load((ask.workdir / "problem.yaml").read_text())["contract"] == "answered: Signed (spec 2)"


def test_an_unanswered_question_lets_the_agent_decide(tmp_path):
    ask = _ask(tmp_path, "ASK echo", questions="operator", wait_s=0.3)
    with pytest.raises(_NoRun):
        drive(ask, run_pass=_no_run, say=lambda _m: None)
    last = [r for r in _rows(ask.journal) if r["k"] == "answer"][-1]
    assert last["by"] == "decide" and "no answer" in last["text"]
    assert (ask.workdir / "problem.yaml").is_file()


def test_without_ask_me_the_brief_says_decide_and_no_question_waits(tmp_path):
    ask = _ask(tmp_path, "echo")
    text = brief(ask, inline=False)
    assert "Nobody answers questions" in text and "decisions.json" in text
    assert "THE BUNDLED LOOPS, read only" in text and "applications" in text and "mul8" in text
    assert "ONE question" in brief(Ask(prompt="x", workdir=ask.workdir, author="opencode", questions="operator"), inline=False)


def test_decisions_are_validated_and_read_from_the_document_without_them(tmp_path):
    assert read_decisions(tmp_path) == ([], "no decisions.json")
    (tmp_path / "decisions.json").write_text(json.dumps([{"step": "nowhere", "what": "x"}]))
    assert "entry 1" in read_decisions(tmp_path)[1]
    (tmp_path / "decisions.json").write_text("{")
    assert "not JSON" in read_decisions(tmp_path)[1]
    doc = yaml.safe_load((Path(__file__).parents[2] / "applications" / "mul8" / "problem.yaml").read_text())
    got = decisions_of_document(doc)
    steps = [d["step"] for d in got]
    assert steps[0] == "prompt" and {"checks", "objective", "graph", "extra"} <= set(steps)
    assert any(d["what"] == "fmax_mhz ≥ 1600" for d in got) and any("screen, confirm" in d["what"] for d in got)
    assert all(d["why"] == "" for d in got)


def test_options_are_the_questions_listed_choices():
    assert question_options("Which?\n- a\n* b\n1. c\ne) e\nplain") == ["a", "b", "c", "e"]
    assert question_options("Signed or not?") == []


def test_the_skills_are_placed_where_every_agent_looks(tmp_path):
    names = install_author_skills(tmp_path)
    assert names == list(AUTHOR_SKILLS)
    for d in (".claude/skills", ".opencode/skills", ".agents/skills"):
        assert (tmp_path / d / "flux-loop-creator" / "SKILL.md").is_file()


def test_the_agents_own_check_is_a_line_with_its_verdict(tmp_path):
    """Live (D1165): OpenCode ran `<python> -m flux_cli task check .` five times in its turn, each REFUSED, and the
    lines read "✓ ran cd /tmp/.../popcount_live && /nix/st..." -- a refused check's command exits fine as a tool call."""
    from flux_loop.author import Activity

    a = Activity(tmp_path)
    cmd = f"cd {tmp_path} && {sys.executable} -m flux_cli task check . --no-sandbox"
    steps = [{"k": "tool", "name": "bash", "call": "bash: " + cmd[:100], "input": {"command": cmd}, "error": False,
              "out": "task x: ...\n  REFUSED: statement names the loop folder by its absolute path\n"},
             {"k": "tool", "name": "bash", "call": "bash: " + cmd[:100], "input": {"command": cmd}, "error": False,
              "out": "not a task: `problem.yaml` is not YAML\n"},
             {"k": "tool", "name": "bash", "call": "bash: " + cmd[:100], "input": {"command": cmd}, "error": False,
              "out": "task x: ...\n  tools: present for every stage\n"},
             {"k": "tool", "name": "bash", "call": "bash: ls -la", "input": {"command": "ls -la"}, "error": False, "out": "x"}]
    token = a.phase_start("agent: opencode", "problem", {})
    a.phase_end(token, "agent: opencode", 1.0, False, {"steps": steps})
    rows = _rows(tmp_path)
    assert [(r["k"], r.get("tone")) for r in rows] == [("check", "bad"), ("check", "bad"), ("check", "ok"), ("list", None)], rows
    assert rows[0]["text"] == "the check refused it: statement names the loop folder by its absolute path"
    assert rows[1]["text"] == "the check refused it: `problem.yaml` is not YAML" and rows[2]["text"] == "ran the check: it passed"
