"""D1155-D1158: an author's refused draft is repaired, not abandoned. The agent runs the loop's own check itself
(`flux task check`, the author's check), a repair continues its session with the refusal only, a refusal names each
missing file with the key that names it and what the folder holds, the same refusal twice says the last change did
not fix it, and when the repairs run out the draft stays with its refusal for another round."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

from flux_llm import ScriptedProposer
from flux_loop.author import Ask, brief, check_command, check_document, check_problem, drive, named_files

CHECK = "import sys\nprint('0 failing')\n"
DOC = {"statement": "The digits 0 to 9, one per line.", "language": "text",
       "budget": {"steps": 1, "repair_attempts": 2, "prototype": False},
       "flow": {"test": "{python} {home}/check.py {artifact}"}}


def _reply(files: dict) -> str:
    return "".join(f"FILE {name}\n```\n{text.rstrip()}\n```\n" for name, text in files.items()) + "WHY: tried\n"


def _no_run(task, problem):
    raise AssertionError("nothing runs: the document is refused")


class Checked(Exception):
    """The document passed: the pass that would run now is not this test's."""


def _passed(task, problem):
    raise Checked(task.id)


def _drive(ask: Ask, **kw) -> list[str]:
    """`drive` until its document passes the check; what it said."""
    said: list[str] = []
    with pytest.raises(Checked):
        drive(ask, run_pass=_passed, say=said.append, **kw)
    return said


def test_a_file_named_outside_flow_is_refused_by_its_key_with_what_the_folder_holds(tmp_path):
    """Cause (c): the check looked for `{home}/x` in flow and the generator only -- a knowledge file, a baseline,
    a skills folder came back as the loader's raw error, with the folder's absolute path."""
    work = tmp_path / "w"
    (work / "library").mkdir(parents=True)
    (work / "library" / "notes.md").write_text("notes\n")
    (work / "check.py").write_text(CHECK)
    doc = {**DOC, "flow": {**DOC["flow"], "knowledge": {"files": ["library/spec.md"]}},
           "baseline": {"command": "{python} {home}/base.py", "file": "base.txt"}, "skills": ["myskills"]}
    (work / "problem.yaml").write_text(yaml.safe_dump(doc))
    keys = {(k, p) for k, p, _w in named_files(doc)}
    assert {("flow.knowledge.files[0]", "library/spec.md"), ("baseline.command", "base.py"), ("baseline.file", "base.txt"),
            ("skills[0]", "myskills"), ("flow.test", "check.py")} <= keys
    task, _p, why = check_document(work)
    assert task is None
    for line in ("  - library/spec.md  (named by flow.knowledge.files[0])", "  - base.py  (named by baseline.command)",
                 "  - base.txt  (named by baseline.file)", "  - myskills  (named by skills[0]; a folder)"):
        assert line in why, why
    assert "library/ holds: notes.md" in why and "The loop folder holds: library/, check.py, problem.yaml" in why
    assert str(work) not in why, "paths relative to the loop folder, sandboxed or not"


def test_a_loader_error_names_paths_relative_to_the_loop_folder(tmp_path):
    work = tmp_path / "w"
    work.mkdir()
    (work / "check.py").write_text(CHECK)
    (work / "problem.yaml").write_text(yaml.safe_dump({**DOC, "flow": {**DOC["flow"], "knowledge": {"sheet": "{home}"}}}))
    _t, _p, why = check_document(work)
    assert why and str(work) not in why and str(work.resolve()) not in why


def test_a_draft_names_no_absolute_path_and_writes_its_golden_a_loop_need_not(tmp_path):
    """D604 stays for a loop: the model writes a missing golden; an author's draft writes its own, and does not name
    the folder it drafts in by its absolute path (the folder moves when the loop is made)."""
    work = tmp_path / "w"
    work.mkdir()
    gate = {**DOC, "language": "verilog", "flow": {"test": "{python} -m flux_loop.golden_model {artifact} --golden {home}/golden.py"}}
    (work / "problem.yaml").write_text(yaml.safe_dump(gate))
    assert check_problem(work / "problem.yaml", draft=False, tools=False)[2] == ""
    why = check_problem(work / "problem.yaml", draft=True, tools=False)[2]
    assert "  - golden.py  (named by flow.test; the golden model)" in why
    (work / "check.py").write_text(CHECK)
    (work / "problem.yaml").write_text(yaml.safe_dump({**DOC, "flow": {"test": f"{{python}} {work}/check.py {{artifact}}"}}))
    assert "flow.test names the loop folder by its absolute path" in check_problem(work / "problem.yaml", tools=False)[2]
    assert check_problem(work / "problem.yaml", draft=False, tools=False)[2] == ""


def test_flux_task_check_is_the_authors_check(tmp_path, capsys):
    """Cause (a): the agent was pointed at `flux task check`, which passed what the author's check refused."""
    from flux_cli.main import main

    work = tmp_path / "w"
    work.mkdir()
    (work / "AUTHOR-BRIEF.md").write_text("the brief\n")
    (work / "problem.yaml").write_text(yaml.safe_dump(DOC))                       # check.py is not there
    _t, _p, why = check_document(work)
    capsys.readouterr()
    assert main(["task", "check", str(work), "--no-sandbox"]) == 1
    out = capsys.readouterr().out
    assert "REFUSED: " + why.splitlines()[0] in out and "  - check.py  (named by flow.test)" in out
    (work / "problem.yaml").write_text(yaml.safe_dump({**DOC, "flow": {**DOC["flow"], "knowledge": {"files": ["spec.md"]}}}))
    (work / "check.py").write_text(CHECK)
    _t, _p, why = check_document(work)
    assert main(["task", "check", str(work), "--no-sandbox"]) == 2, "it does not load: not a task"
    assert f"not a task: {why}" in capsys.readouterr().out
    (work / "spec.md").write_text("the spec\n")
    assert check_document(work)[2] == ""
    assert main(["task", "check", str(work), "--no-sandbox"]) == 0
    assert "REFUSED" not in capsys.readouterr().out


def test_the_agents_brief_tells_it_to_run_the_check_until_it_passes(tmp_path):
    ask = Ask(prompt="digits", workdir=tmp_path, author="opencode")
    text = brief(ask, inline=False)
    assert "CHECK BEFORE YOU END YOUR TURN: run `" + check_command() + "`" in text and "until it passes" in text
    assert check_command().endswith("-m flux_cli task check . --no-sandbox") and sys.executable in check_command()
    assert "CHECK BEFORE YOU END YOUR TURN" not in brief(Ask(prompt="digits", workdir=tmp_path), inline=True), "a model has no shell"


def test_the_same_refusal_twice_says_the_last_change_did_not_fix_it(tmp_path):
    """Cause (b): each repair started blind -- the same refusal came back three times and nobody said so."""
    work = tmp_path / "w"
    renamed = {**DOC, "statement": "Ten digits, ascending."}
    author = ScriptedProposer([_reply({"problem.yaml": yaml.safe_dump(DOC)}),          # names check.py, never writes it
                               _reply({"problem.yaml": yaml.safe_dump(renamed)}),      # changes the statement only
                               _reply({"notes.md": "thinking\n"}),                     # changes another file only
                               _reply({"check.py": CHECK})])
    _drive(Ask(prompt="digits", workdir=work, passes=1, checks=3), proposer=author)
    assert "YOUR LAST CHANGE" not in author.prompts[1], "the first repair has nothing to compare"
    assert "YOUR LAST CHANGE DID NOT FIX THIS" in author.prompts[2] and "-statement: The digits 0 to 9" in author.prompts[2]
    assert "+statement: Ten digits, ascending." in author.prompts[2]
    assert "`problem.yaml` did not change" in author.prompts[3] and "Other files you changed: notes.md" in author.prompts[3]


def _fake_agent(tmp_path: Path) -> dict:
    """An OpenCode-shaped agent: its first turn names check.py and never writes it; a resumed turn writes it."""
    fake = tmp_path / "agent.py"
    fake.write_text(
        "import json, sys\nfrom pathlib import Path\nargs = sys.argv[1:]\n"
        "if args[0] == 'resume':\n"
        "    session, answer, work = args[1], args[2], Path(args[3])\n"
        "    (work.parent / 'resumed.txt').write_text(session + '|' + answer)\n"
        f"    (work / 'check.py').write_text({CHECK!r})\n"
        "else:\n"
        "    brief, work = Path(args[0]).read_text(), Path(args[1])\n"
        "    (work.parent / 'first.txt').write_text(brief)\n"
        f"    (work / 'problem.yaml').write_text({yaml.safe_dump(DOC)!r})\n"
        "print(json.dumps({'type': 'text', 'sessionID': 'ses_7', 'part': {'text': 'written'}}))\n")
    return {"command": ["{python}", str(fake), "{prompt_file}", "{workdir}"],
            "resume": ["{python}", str(fake), "resume", "{session}", "{answer}", "{workdir}"], "output": "opencode", "timeout_s": 60}


def test_a_repair_continues_the_agents_session_with_the_refusal_only(tmp_path):
    work = tmp_path / "w"
    _drive(Ask(prompt="digits", workdir=work, author=_fake_agent(tmp_path), passes=1, journal=work / "runs" / "author"))
    session, answer = (tmp_path / "resumed.txt").read_text().split("|", 1)
    assert session == "ses_7" and "THE CHECK REFUSED `problem.yaml` (repair 1 of 3)" in answer and "check.py" in answer
    assert "YOU WRITE THE PROBLEM" not in answer and "## Worked examples" not in answer, "the session holds the brief"
    assert check_command() in answer and check_command() in (tmp_path / "first.txt").read_text()
    assert json.loads((work / "runs" / "author" / "session.json").read_text()) == {"agent": "agent.py", "session": "ses_7"}
    assert json.loads((work / "runs" / "author" / "outcome.json").read_text())["error"] == ""


def test_spent_repairs_leave_the_draft_its_refusal_and_its_session(tmp_path):
    """Cause (d): after the repairs the run ended with nothing offered; now the draft and why stay for another round."""
    work = tmp_path / "w"
    author = ScriptedProposer([_reply({"problem.yaml": yaml.safe_dump(DOC)})] * 3)
    got = drive(Ask(prompt="digits", workdir=work, passes=1, checks=2, journal=work / "runs" / "author"), proposer=author,
                run_pass=_no_run, say=lambda _m: None)
    assert "  - check.py  (named by flow.test)" in got["error"] and (work / "problem.yaml").is_file()
    assert json.loads((work / "runs" / "author" / "outcome.json").read_text())["error"] == got["error"]
    lines = [json.loads(x) for x in (work / "runs" / "author" / "activity.jsonl").read_text().splitlines()]
    assert lines[-1]["text"].startswith("stopped after 2 repairs: the draft stays") and lines[-1]["tone"] == "bad"
    # another round: the draft is checked first, the person's note told with the refusal, nothing written anew
    again = ScriptedProposer([_reply({"check.py": CHECK})])
    said = _drive(Ask(prompt="digits", workdir=work, passes=1, repair=True, note="write the checker", journal=work / "runs" / "author"),
                  proposer=again)
    assert len(again.prompts) == 1 and any(s.startswith("author (your note): wrote check.py") for s in said)
    assert "THE PERSON WHO ASKED READ YOUR DOCUMENT and says:\nwrite the checker" in again.prompts[0]
    assert "THE DOCUMENT WAS REFUSED" in again.prompts[0] and "check.py" in again.prompts[0]
    plain = ScriptedProposer([])
    _drive(Ask(prompt="digits", workdir=work, passes=1, repair=True), proposer=plain)
    assert plain.prompts == [], "a draft that passes now needs no turn"
