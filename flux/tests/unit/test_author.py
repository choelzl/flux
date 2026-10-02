"""The loop from a prompt (D586): an author writes the problem document from the prompt and
its files, the loop checks and runs it, the author reads the report and steers."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

from flux_llm import ScriptedProposer
from flux_loop import request_for, run_loop
from flux_loop.author import Ask, check_document, drive, reference, workspace

CHECK = """import sys
want = [str(i) for i in range(10)]
got = [l.strip() for l in open(sys.argv[1]).read().splitlines() if l.strip()]
bad = sum(1 for i, w in enumerate(want) if i >= len(got) or got[i] != w)
print(f"{bad} failing")
"""

DOC = {"statement": "The digits 0 to 9, one per line, as the spec in library/spec.md says.",
       "language": "text",
       "budget": {"steps": 1, "repair_attempts": 2, "prototype": False},
       "flow": {"test": "{python} {home}/check.py {artifact}"}}


def _reply(files: dict, done: bool = False, why: str = "") -> str:
    """A model author's reply in its shape: FILE blocks, or DONE."""
    if done and not files:
        return f"DONE\nWHY: {why}"
    return "".join(f"FILE {name}\n```\n{text.rstrip()}\n```\n" for name, text in files.items()) + f"WHY: {why}\n"


def test_the_reply_is_file_blocks_or_json():
    from flux_loop.author import parse_files

    files, done, why = parse_files("Here:\nFILE problem.yaml\n```yaml\nid: x\n```\nFILE `sub/check.py`\n```python\nprint(1)\n```\nWHY: two files")
    assert files == {"problem.yaml": "id: x", "sub/check.py": "print(1)"} and not done and why == "two files"
    assert parse_files("DONE\nWHY: it meets the goal") == ({}, True, "it meets the goal")
    assert parse_files(json.dumps({"files": {"a.py": "x"}, "why": "json"})) == ({"a.py": "x"}, False, "json")


def _run(problem_task, loop_replies):
    def run_pass(task, problem):
        return run_loop(problem, request_for(task, db=""), proposer=ScriptedProposer(list(loop_replies)), log=lambda _m: None)
    return run_pass


def test_the_reference_carries_the_guide_and_the_live_examples():
    ref = reference()
    assert "YOU WRITE" not in ref and "## Keys" in ref and "flux rtl test" in ref
    assert "`mul8/problem.yaml`" in ref and "`adder16/gen.py`" in ref


def test_a_model_author_writes_the_problem_the_loop_runs_it_and_the_author_settles_it(tmp_path):
    spec = tmp_path / "spec.md"
    spec.write_text("# Digits\nEvery line one decimal digit, ascending from 0 to 9.\n")
    work = tmp_path / "work"
    inputs = workspace([spec], work)
    assert inputs == [Path("library/spec.md")] and (work / "library/spec.md").is_file(), "D791: the loop's library"
    author = ScriptedProposer([_reply({"problem.yaml": yaml.safe_dump(DOC), "check.py": CHECK}, why="a checker script"),
                               _reply({}, done=True, why="the digits pass the gate")])
    said: list[str] = []
    loop_digits = json.dumps({"artifact": "\n".join(str(i) for i in range(10)) + "\n"})
    got = drive(Ask(prompt="write the digits", workdir=work, inputs=inputs, passes=3), proposer=author,
                run_pass=_run(None, [loop_digits]), say=said.append)
    assert not got["error"] and got["result"].decision is not None
    doc = yaml.safe_load((work / "problem.yaml").read_text())
    assert doc["flow"]["knowledge"]["files"] == ["library/spec.md"], "the input the author forgot is added"
    # D593: DONE settles the document, it does not end the run -- the loop runs it again, and
    # the run ends here only because the scripted author has nothing more to say
    assert [h["turn"] for h in got["history"]] == ["write", "run", "revise", "run"]
    assert any("the document stands; the loop goes on" in s for s in said)
    assert "THE ASK:\nwrite the digits" in author.prompts[0] and "Every line one decimal digit" in author.prompts[0]
    assert "THE LOOP RAN YOUR DOCUMENT" in author.prompts[1] and "DECISION" in author.prompts[1]
    assert json.loads((work / "authoring.json").read_text())[-1]["turn"] == "run"


def test_a_refused_document_goes_back_with_the_reason(tmp_path):
    work = tmp_path / "w"
    bad = {**DOC, "goals": ["fast"]}
    author = ScriptedProposer([_reply({"problem.yaml": yaml.safe_dump(bad)}),                  # a key no document has
                               _reply({"problem.yaml": yaml.safe_dump(DOC)}),                  # check.py still missing
                               _reply({"check.py": CHECK})])
    got = drive(Ask(prompt="digits", workdir=work, passes=1, checks=3), proposer=author,
                run_pass=_run(None, [json.dumps({"artifact": "0\n"})]), say=lambda _m: None)
    assert not got["error"]
    refusals = [h["refused"] for h in got["history"] if h["turn"] == "repair"]
    assert "keys a document does not have: goals" in refusals[0]
    assert "check.py beside the document, and it is not there" in refusals[1]
    assert "THE DOCUMENT WAS REFUSED" in author.prompts[1] and "goals" in author.prompts[1]


def test_a_document_that_never_loads_ends_with_the_reason(tmp_path):
    author = ScriptedProposer([_reply({"problem.yaml": "statement: y\n"})])
    got = drive(Ask(prompt="x", workdir=tmp_path / "w", passes=1, checks=1), proposer=author,
                run_pass=lambda t, p: pytest.fail("nothing runs"), say=lambda _m: None)
    assert got["error"] and "does not load" in got["error"]


def test_a_coding_agent_authors_the_problem(tmp_path):
    fake = tmp_path / "author.py"
    fake.write_text(
        "import sys, json\nfrom pathlib import Path\n"
        "brief, workdir = Path(sys.argv[1]).read_text(), Path(sys.argv[2])\n"
        f"(workdir / 'problem.yaml').write_text({yaml.safe_dump(DOC)!r})\n"
        f"(workdir / 'check.py').write_text({CHECK!r})\n"
        "assert 'YOU WRITE THE PROBLEM, NOT THE DESIGN' in brief\n"
        "print('written')\n")
    agent = {"command": ["{python}", str(fake), "{prompt_file}", "{workdir}"], "timeout_s": 60}
    got = drive(Ask(prompt="digits", workdir=tmp_path / "w", author=agent, passes=1),
                run_pass=_run(None, [json.dumps({"artifact": "\n".join(str(i) for i in range(10)) + "\n"})]),
                say=lambda _m: None)
    assert not got["error"] and got["result"].decision is not None


def test_a_document_file_list_reads_its_inputs_into_knowledge(tmp_path):
    from flux_loop import TaskSpec

    (tmp_path / "ref.txt").write_text("REFERENCE: carry-save beats ripple here\n")
    task = TaskSpec.from_dict({**DOC, "id": "digits", "flow": {**DOC.get("flow", {}), "knowledge": {"files": ["ref.txt"]}}}, base=tmp_path)
    assert "FILE ref.txt:\nREFERENCE: carry-save" in task.knowledge
    from flux_loop import TaskError

    with pytest.raises(TaskError, match="knowledge.files: 'nope.txt' is not a file"):
        TaskSpec.from_dict({**DOC, "id": "digits", "flow": {**DOC.get("flow", {}), "knowledge": {"files": ["nope.txt"]}}}, base=tmp_path)


def test_the_cli_writes_and_checks_without_running(tmp_path):
    replies = tmp_path / "author.json"
    replies.write_text(json.dumps([_reply({"problem.yaml": yaml.safe_dump(DOC), "check.py": CHECK})]))
    from flux_cli.main import main

    rc = main(["ask", "the digits", "--dir", str(tmp_path / "w"), "--no-run", "--author-replies", str(replies),
               "--replies", str(replies)])
    assert rc == 0 and (tmp_path / "w" / "problem.yaml").is_file()
    task, problem, why = check_document(tmp_path / "w")
    assert task is not None and not why


def test_a_review_note_goes_back_to_the_author_before_the_loop_runs(tmp_path):
    """D587: the person reads the checked problem; a note sends it back; None runs it."""
    work = tmp_path / "w"
    wider = {**DOC, "statement": "The digits 0 to 9, and say why in the statement."}
    author = ScriptedProposer([_reply({"problem.yaml": yaml.safe_dump(DOC), "check.py": CHECK}),
                               _reply({"problem.yaml": yaml.safe_dump(wider)})])
    seen: list[str] = []

    def review(path, task, problem):
        seen.append(task.statement)
        return "say why in the statement" if len(seen) == 1 else None

    ran: list[str] = []

    def run_pass(task, problem):
        ran.append(task.statement)
        return run_loop(problem, request_for(task, db=""), proposer=ScriptedProposer([json.dumps({"artifact": "0\n"})]),
                        log=lambda _m: None)

    got = drive(Ask(prompt="digits", workdir=work, passes=1), proposer=author, run_pass=run_pass,
                say=lambda _m: None, review=review)
    assert not got["error"] and len(seen) == 2 and ran == [wider["statement"]]
    assert "THE PERSON WHO ASKED READ YOUR DOCUMENT and says:\nsay why in the statement" in author.prompts[1]
    assert [h["turn"] for h in got["history"]] == ["write", "review", "run"]


def test_a_golden_model_that_cannot_run_is_refused_before_the_loop(tmp_path):
    """D589: the check runs the golden once -- a bare number where a dict belongs is caught."""
    import shutil

    if shutil.which("verilator") is None:
        pytest.skip("the rtl tools are needed for `flux rtl test` to be on the tool list")
    work = tmp_path / "w"
    work.mkdir()
    doc = {"statement": "negate",
           "language": "verilog",
           "budget": {"steps": 1, "prototype": False},
           "flow": {"test": "flux rtl test {artifact} --golden {home}/golden.py"}}
    (work / "problem.yaml").write_text(yaml.safe_dump(doc))
    (work / "golden.py").write_text("PORTS = [{'name': 'a', 'dir': 'in', 'bits': 8}, {'name': 'y', 'dir': 'out', 'bits': 8}]\n"
                                    "def golden(a):\n    return -a\n")
    task, problem, why = check_document(work)
    assert task is None and "cannot run" in why and "AttributeError" in why
    (work / "golden.py").write_text("PORTS = [{'name': 'a', 'dir': 'in', 'bits': 8}, {'name': 'y', 'dir': 'out', 'bits': 8}]\n"
                                    "def golden(a):\n    return {'out': -a}\n")
    task, problem, why = check_document(work)
    assert task is None and "not the output ports ['y']" in why
    (work / "golden.py").write_text("PORTS = [{'name': 'a', 'dir': 'in', 'bits': 8}, {'name': 'y', 'dir': 'out', 'bits': 8}]\n"
                                    "def golden(a):\n    return {'y': -a}\n")
    task, problem, why = check_document(work)
    assert task is not None and not why


def test_a_golden_that_never_fills_its_declared_width_is_refused(tmp_path):
    """An unsigned output whose top bit no vector sets disagrees with its width (D622)."""
    from types import SimpleNamespace

    from flux_loop.author import _golden_fault
    from flux_loop.document import _gate

    def check(ret: str) -> str:
        (tmp_path / "golden.py").write_text(
            "PORTS = [{'name': 'a', 'dir': 'in', 'bits': 8, 'unsigned': True},\n"
            "         {'name': 'b', 'dir': 'in', 'bits': 8, 'unsigned': True},\n"
            "         {'name': 's', 'dir': 'out', 'bits': 9, 'unsigned': True}]\n"
            f"COUNT = 16\n\ndef golden(a, b):\n    return {{'s': {ret}}}\n")
        task = SimpleNamespace(gate=_gate("flux rtl test {artifact} --golden {home}/golden.py"))
        return _golden_fault(task, tmp_path)

    assert "never sets the top bit of output `s` (9 bits)" in check("(a + b) & 0xFF")
    assert check("a + b") == ""
