"""D735, D736: a loop's own references -- one folder, `library/` beside its document -- join its
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
    for f in ("library/papers", "research", "out"):
        (tmp_path / f).mkdir(parents=True)
    (tmp_path / "library/papers/sqrt.md").write_text("# Fast inverse square root\n\nA Newton step after a magic-constant guess halves the error.\n")
    (tmp_path / "library/notes.txt").write_text("Booth recoding halves the partial products of a multiplier in hardware.\n")
    (tmp_path / "research/a.pdf").write_bytes(b"%PDF-1.4 elsewhere")
    task = load_task(str(tmp_path / "kp.problem.yaml"))
    assert library_folders(task) == (str((tmp_path / "library").resolve()),), "one folder: library/, not research/ or out/"
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
