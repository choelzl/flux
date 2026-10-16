"""The agents' workbench (D677): one folder beside the document, kept across runs, for the tools
the agents build and the notes they keep -- inner-loop knowledge; the loop provides it, links it
into every agent's work directory, lists it in the brief, and never reads it."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from flux_loop import TaskError, TaskSpec
from flux_loop.agent import AgentSpec, agent_spec, run_turn, workbench_link, workbench_section

DIGITS = Path(__file__).resolve().parents[2] / "core" / "loop" / "examples" / "digits" / "problem.json"


def test_the_document_says_where_and_the_default_is_beside_it(tmp_path):
    doc = {"id": "digits", **json.loads(DIGITS.read_text())}
    assert TaskSpec.from_dict(doc, base=tmp_path).workbench == str((tmp_path / "workbench").resolve())
    assert TaskSpec.from_dict(doc).workbench == "", "an inline document has none"
    on = TaskSpec.from_dict(doc, base=tmp_path)
    assert "workbench" not in on.to_dict() and on.digest == TaskSpec.from_dict(doc, base=tmp_path / "x").digest
    with pytest.raises(TaskError, match="keys a problem document does not have: workbench"):
        TaskSpec.from_dict({**doc, "workbench": "elsewhere"}, base=tmp_path)    # D790: always workbench/


def test_the_brief_lists_what_it_holds_and_the_link_reaches_it(tmp_path):
    bench = tmp_path / "wb"
    assert workbench_section("") == "" and workbench_link("", tmp_path) is None
    work = tmp_path / "w1"
    work.mkdir()
    workbench_link(str(bench), work)
    assert (work / "workbench").resolve() == bench.resolve() and (bench / "tools").is_dir() and (bench / "notes").is_dir()
    assert "It is empty: you are the first." in workbench_section(str(bench))
    (bench / "tools" / "fit.py").write_text('"""Fit a cubic per segment of GELU."""\nimport numpy\n')
    (bench / "notes" / "gelu.md").write_text("# The negative tail needs 2 more bits\n\nbecause ...\n")
    s = workbench_section(str(bench))
    assert "It holds 2 files: `workbench/README.md` lists them" in s and "fit.py" not in s, "D1111: the index, not a listing"
    assert "never reads it" in s and "kept across runs" in s
    index = (bench / "README.md").read_text()
    assert "## tools" in index and "- `tools/fit.py` -- Fit a cubic per segment of GELU." in index
    assert "- `notes/gelu.md` -- The negative tail needs 2 more bits" in index and "README.md`" not in index
    from flux_loop.agent import bench_stale

    assert not bench_stale(str(bench))
    (bench / "notes" / "gelu.md").unlink()
    assert bench_stale(str(bench)) and "gelu.md" in (bench / "README.md").read_text(), \
        "D1114: the brief writes a first index only; the setup keeps it current"
    workbench_link(str(bench), work)                                     # a second turn: the link stays


def test_an_agent_writes_through_the_link_and_claude_gets_the_folder(tmp_path):
    bench = tmp_path / "wb"
    work = tmp_path / "w"
    work.mkdir()
    workbench_link(str(bench), work)
    fake = tmp_path / "a.py"
    fake.write_text("import sys, pathlib\npathlib.Path('workbench/notes/n.md').write_text('seen')\nprint(sys.argv[1:])\n")
    spec = AgentSpec("fake", (sys.executable, str(fake), "{workbench}"), None, "text", timeout_s=30)
    turn = run_turn(spec, spec.argv, {"prompt": "p", "workbench": str(bench)}, workdir=work)
    assert turn.ok and (bench / "notes" / "n.md").read_text() == "seen" and str(bench) in turn.text
    assert agent_spec("claude").add_dir == ("--add-dir",) and agent_spec("opencode").add_dir == ()


def test_the_setup_describes_the_workbench_when_it_changed(tmp_path):
    """D1114: the Setup writes the README again when the workbench changed: what its tools and notes are for, by the
    model (or the digesting agent), then every file; without a model, the file list alone; unchanged, nothing."""
    from flux_llm import ScriptedProposer
    from flux_loop import LoopRequest, LoopState, PromptProblem

    home = tmp_path / "gelu"
    home.mkdir()
    task = TaskSpec.from_dict({"id": "gelu", "statement": "A GELU in fp16.", "flow": {"test": "true"}}, base=home)
    bench = Path(task.workbench)
    (bench / "tools").mkdir(parents=True)
    (bench / "tools" / "fit.py").write_text('"""Fit a cubic per segment of GELU."""\n')
    model = ScriptedProposer(["- `tools/fit.py` fits the segment polynomials: run it before changing the table."])
    state = LoopState(request=LoopRequest(), say=lambda _m: None, proposer=model, feedback=None)
    problem = PromptProblem(task)
    got = problem.bench_readme(state)
    readme = (bench / "README.md").read_text()
    assert got["files"] == 1 and "fits the segment polynomials" in readme and "# Every file" in readme
    assert "- `tools/fit.py` -- Fit a cubic per segment of GELU." in readme and "A GELU in fp16." in model.prompts[0]
    assert problem.bench_readme(state) == {}, "unchanged: not written again"
    (bench / "notes").mkdir()
    (bench / "notes" / "tail.md").write_text("# The tail needs 2 more bits\n")
    alone = LoopState(request=LoopRequest(), say=lambda _m: None, proposer=None, feedback=None)
    assert problem.bench_readme(alone)["described by"].startswith("nobody")
    readme = (bench / "README.md").read_text()
    assert "tail.md` -- The tail needs 2 more bits" in readme and "fits the segment polynomials" not in readme
