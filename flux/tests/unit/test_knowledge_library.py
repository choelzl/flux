"""D407: the library -- a person's papers and documents, indexed beside the corpus,
never committed. Pins the text/PDF connector, the default-index wiring, and the
gitignore that keeps other people's works off the remote."""

from __future__ import annotations

import subprocess
from pathlib import Path

FLUX_ROOT = Path(__file__).resolve().parents[2]


def _library(tmp_path, files: dict[str, str]) -> Path:
    root = tmp_path / "knowledge"
    (root / "corpus").mkdir(parents=True)          # empty corpus: the library stands alone
    lib = root / "library"
    lib.mkdir(parents=True, exist_ok=True)
    for rel, text in files.items():
        p = lib / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return root


def test_markdown_paragraphs_carry_their_nearest_heading(tmp_path):
    from flux_knowledge.connectors.text import ingest_library

    root = _library(tmp_path, {"paper.md": (
        "# Prefetching Revisited\n\nintro paragraph about spatial patterns\n\n"
        "## Results\n\nbingo beats stride on 5G traces\nby a wide margin\n")})
    chunks = ingest_library(root / "library", repo_root=tmp_path)
    assert [c.heading for c in chunks] == ["Prefetching Revisited", "Results"]
    assert chunks[1].text == "bingo beats stride on 5G traces by a wide margin"
    assert chunks[0].standard_id == "library"


def test_subfolders_cannot_collide_and_the_readme_is_not_a_paper(tmp_path):
    from flux_knowledge.connectors.text import ingest_library

    root = _library(tmp_path, {"a/notes.txt": "alpha content here",
                               "b/notes.txt": "beta content here",
                               "README.md": "# the manual, not a paper"})
    chunks = ingest_library(root / "library", repo_root=tmp_path)
    assert len(chunks) == 2 and len({c.id for c in chunks}) == 2
    assert not any("manual" in c.text for c in chunks)


def test_default_index_serves_the_library_through_knowledge_lookup(tmp_path):
    from flux_knowledge.retrieval import build_default_index, knowledge_lookup

    root = _library(tmp_path, {"imapping.md": "# Skewed layouts\n\n"
                               "xor swizzle removes bank conflicts for strided tiles\n"})
    index = build_default_index(root, repo_root=tmp_path)
    hits = knowledge_lookup("xor swizzle bank conflicts", standard_id="library",
                            index=index)
    assert hits and hits[0].chunk.source_path.endswith("imapping.md")


def test_pdf_goes_through_pdftotext_and_absence_is_a_note_not_a_crash(tmp_path, monkeypatch):
    from flux_knowledge.connectors import text as t

    root = _library(tmp_path, {})
    (root / "library" / "paper.pdf").write_bytes(b"%PDF-1.4 stub")
    # tool present: our pipeline chunks whatever pdftotext hands back
    monkeypatch.setattr(t.shutil, "which", lambda _n: "/usr/bin/pdftotext")
    monkeypatch.setattr(t.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(
        a, 0, stdout="extracted paragraph one\n\nextracted paragraph two\n"))
    chunks = t.ingest_library(root / "library", repo_root=tmp_path)
    assert [c.text for c in chunks] == ["extracted paragraph one", "extracted paragraph two"]
    assert chunks[0].id == "library/paper#0"
    # tool absent: skipped, one note, no exception
    monkeypatch.setattr(t.shutil, "which", lambda _n: None)
    notes: list[str] = []
    assert t.ingest_library(root / "library", repo_root=tmp_path, log=notes.append) == []
    assert len(notes) == 1 and "pdftotext" in notes[0]


def test_the_library_gitignore_keeps_papers_local():
    """git itself is the authority on what the .gitignore means (D407): a paper under
    library/ is ignored, while the folder's README and the .gitignore are tracked."""
    lib = "flux/mentor/knowledge/library"
    repo = FLUX_ROOT.parent

    def ignored(rel: str) -> bool:
        return subprocess.run(["git", "check-ignore", "-q", f"{lib}/{rel}"],
                              cwd=repo).returncode == 0

    assert ignored("some-paper.pdf") and ignored("sub/dir/notes.md")
    assert not ignored("README.md") and not ignored(".gitignore")
