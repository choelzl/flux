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
