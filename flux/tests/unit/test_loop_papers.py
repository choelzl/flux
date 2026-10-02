"""D735-D737: a loop's own references -- `library/` and `inputs/` beside its document -- join its
library without a word in the document, and the sandbox mounts the libraries a run reads."""

from __future__ import annotations

import types
from pathlib import Path

from flux_cli import sandbox
from flux_loop import load_task
from flux_loop.document import library_folders

DOC = "id: kp\nstatement: the fastest\nlanguage: python\ngate: 'python3 check.py {artifact}'\nstages:\n  - {name: b, command: 'python3 b.py {artifact}', metrics: [t]}\nobjectives:\n  - {metric: t, direction: minimize}\n"


def test_the_loops_library_folder_is_its_library(tmp_path, monkeypatch):
    (tmp_path / "kp.problem.yaml").write_text(DOC)
    for f in ("library/papers", "inputs", "research", "out"):
        (tmp_path / f).mkdir(parents=True)
    (tmp_path / "library/papers/sqrt.md").write_text("# Fast inverse square root\n\nA Newton step after a magic-constant guess halves the error.\n")
    (tmp_path / "inputs/notes.txt").write_text("Booth recoding halves the partial products of a multiplier in hardware.\n")
    (tmp_path / "research/a.pdf").write_bytes(b"%PDF-1.4 elsewhere")
    task = load_task(str(tmp_path / "kp.problem.yaml"))
    assert library_folders(task) == (str((tmp_path / "library").resolve()), str((tmp_path / "inputs").resolve())), \
        "library/ and inputs/, not research/ or out/"
    monkeypatch.setenv("FLUX_LIBRARY", str(tmp_path / "no-shared-library"))
    from flux_loop.task import PromptProblem

    mentor = PromptProblem(task).knowledge()
    assert mentor is not None, "an empty shared library: the loop's own library alone"
    papers = mentor.source("papers").render(None)
    assert "sqrt.md" in papers and "notes.txt" in papers
    assert any("sqrt.md" in line for line in mentor.source("library").lookup("magic constant newton square root", k=2))


def test_the_sandbox_mounts_the_libraries_a_run_reads(tmp_path, monkeypatch):
    shared, outside, home = tmp_path / "shared-lib", tmp_path / "team-papers", tmp_path / "loop"
    for d in (shared, outside, home):
        d.mkdir()
    (home / "kp.problem.yaml").write_text(DOC + f"knowledge: {{library: {outside}}}\n")
    monkeypatch.setenv("FLUX_LIBRARY", str(shared))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    args = types.SimpleNamespace(file=str(home / "kp.problem.yaml"), db=None, out=None, json=None)
    ro, _ = sandbox.mounts_for(args, "task run")
    assert str(shared.resolve()) in ro and str(outside.resolve()) in ro
    assert Path(home).resolve().as_posix() in ro


def test_a_loops_own_papers_are_digested_once_by_its_model_on_their_own(tmp_path, monkeypatch):
    """D753: no `flux knowledge digest` to remember -- Background reading digests the loop's own
    papers (library/, inputs/) with the run's model, once each, and keeps to them."""
    from types import SimpleNamespace

    from flux_loop import PromptProblem, TaskSpec

    monkeypatch.setenv("FLUX_LIBRARY", str(tmp_path / "shared"))
    (tmp_path / "shared").mkdir()
    (tmp_path / "shared/other.md").write_text("Another paper on caches, from the shared library, long enough to be a paper.\n")
    (tmp_path / "loop/library").mkdir(parents=True)
    (tmp_path / "loop/library/adders.md").write_text("Prefix adders: Kogge-Stone has log2(n) levels and fan-out 2. " * 20)
    task = TaskSpec.from_dict({"id": "x", "statement": "an 8-bit adder", "language": "verilog", "gate": {"test": ["true"]},
                               "objectives": []}, base=tmp_path / "loop")

    class Model:
        def __init__(self):
            self.prompts = []

        def propose(self, prompt):
            self.prompts.append(prompt)
            return SimpleNamespace(text="Kogge-Stone prefix adder notes\nlog2(n) levels, fan-out 2")

    model = Model()
    digest = next(s for s in PromptProblem(task).knowledge().sources if type(s).__name__ == "Digest")
    state = SimpleNamespace(request=SimpleNamespace(db=str(tmp_path / "r.db")), proposer=model, say=lambda _m: None)
    text = digest.render(state)
    assert "log2(n) levels, fan-out 2" in text and "[adders.md]" in text
    assert len(model.prompts) == 1 and "adders.md" in model.prompts[0], "its own paper only, not the shared library"
    assert "other.md" not in text
    digest.render(state)
    assert len(model.prompts) == 1, "once: the record keeps it"


def _adders(tmp_path, monkeypatch, knowledge=None):
    from flux_loop import TaskSpec

    monkeypatch.setenv("FLUX_LIBRARY", str(tmp_path / "shared"))
    (tmp_path / "shared").mkdir(exist_ok=True)
    (tmp_path / "loop/library").mkdir(parents=True, exist_ok=True)
    (tmp_path / "loop/library/adders.md").write_text("Prefix adders: Kogge-Stone has log2(n) levels and fan-out 2. " * 20)
    doc = {"id": "x", "statement": "an 8-bit adder", "language": "verilog", "gate": {"test": ["true"]}, "objectives": []}
    if knowledge is not None:
        doc["knowledge"] = knowledge
    return TaskSpec.from_dict(doc, base=tmp_path / "loop")


def test_the_papers_are_digested_in_the_setup(tmp_path, monkeypatch):
    """D771: the Setup's `knowledge: digest` digests what is new, said in the task pane; a prompt
    after it only reads what is stored."""
    from types import SimpleNamespace

    from flux_loop import PromptProblem

    asked = []
    model = SimpleNamespace(model="m1", propose=lambda p: asked.append(p) or SimpleNamespace(text="adders\nlog2(n) levels"))
    problem = PromptProblem(_adders(tmp_path, monkeypatch))
    assert problem.digesting()
    state = SimpleNamespace(request=SimpleNamespace(db=str(tmp_path / "r.db")), proposer=model, say=lambda _m: None)
    got = problem.digest(state)
    assert got["digested"] == 1 and got["in all"] == 1 and got["new"] == "adders.md" and got["by"] == "m1"
    assert problem.digest(state)["digested"] == 0 and len(asked) == 1, "once"
    digest = next(s for s in problem.knowledge().sources if type(s).__name__ == "Digest")
    assert "log2(n) levels" in digest.render(state) and len(asked) == 1, "the prompt reads what the Setup made"


def test_an_agent_the_document_names_digests_the_papers(tmp_path, monkeypatch):
    """D771: `knowledge: {digest: {agent: …}}` -- one agent turn per paper, told where the file is."""
    import sys
    from types import SimpleNamespace

    import pytest

    from flux_loop import PromptProblem, TaskError
    from flux_loop.agent_check import agents_used

    fake = tmp_path / "agent.py"
    fake.write_text("import sys\nbrief = sys.stdin.read()\nassert 'adders.md' in brief and 'is the file' in brief\n"
                    "print('Prefix adders, read by the agent\\nKogge-Stone: log2(n) levels')\n")
    spec = {"command": [sys.executable, str(fake)], "output": "text", "timeout_s": 60}
    task = _adders(tmp_path, monkeypatch, {"digest": {"agent": spec}})
    problem = PromptProblem(task)
    model = SimpleNamespace(model="m1", propose=lambda p: (_ for _ in ()).throw(AssertionError("not the model")))
    state = SimpleNamespace(request=SimpleNamespace(db=str(tmp_path / "r.db")), proposer=model, say=lambda _m: None)
    got = problem.digest(state)
    assert got["digested"] == 1, got
    digest = next(s for s in problem.knowledge().sources if type(s).__name__ == "Digest")
    assert "read by the agent" in digest.render(state)
    assert agents_used(_adders(tmp_path, monkeypatch, {"digest": {"agent": "opencode"}})) == ["opencode"], "its Test gates a start"
    with pytest.raises(TaskError, match="knowledge.digest"):
        _adders(tmp_path, monkeypatch, {"digest": "someone"})
