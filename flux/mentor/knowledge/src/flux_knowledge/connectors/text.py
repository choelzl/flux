"""Plain-text and Markdown ingestion for the library (D407).

`mentor/knowledge/library/` holds papers and documents dropped there (gitignored, per
machine). This connector makes them retrievable as `Chunk`s: paragraphs split on blank lines,
each tagged with the nearest preceding Markdown heading. `.adoc` files delegate to the AsciiDoc
connector.

PDFs go through `pdftotext` (poppler) when available; without it they are skipped with a note
through `log`. Markdown handling is light-touch: enough for lexical retrieval, not a parser.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from flux_knowledge.document import Chunk, chunks_from, paragraphs

_MD_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")

#: What the library connector reads; anything else in the folder is skipped.
from flux_knowledge.connectors.source import SOURCE_SUFFIXES, ingest_source_file

LIBRARY_SUFFIXES = (".md", ".txt", ".adoc", ".pdf") + SOURCE_SUFFIXES


def parse_text(text: str) -> list[tuple[str | None, str]]:
    """Split Markdown/plain text into `(heading, paragraph)` pairs, document order.

    `heading` is the nearest preceding Markdown heading (any `#` level, its markers
    stripped), or None before the first one. Splitting is `flux_knowledge.document.paragraphs`."""
    def heading(raw: str):
        m = _MD_HEADING.match(raw)
        if not m:
            return False
        return m.group(2).strip().strip("#").strip() or None

    return paragraphs(text, heading=heading)


def _source_path(path: Path, repo_root: Path) -> str:
    try:
        return str(path.resolve().relative_to(Path(repo_root).resolve()))
    except ValueError:  # outside the repo (a test tmpdir): the name still identifies it
        return path.name


def ingest_text_file(path: str | Path, *, standard_id: str, repo_root: Path,
                     id_stem: str | None = None) -> list[Chunk]:
    """One `.md`/`.txt` file into `Chunk`s. `id_stem` (the library-relative path, suffix
    dropped) keeps ids short and collision-free across subfolders; default: the file's stem."""
    path = Path(path)
    source_path = _source_path(path, repo_root)
    stem = id_stem or path.stem
    return chunks_from(parse_text(path.read_text(errors="replace")), standard_id=standard_id,
                       stem=stem, source_path=source_path)


def ingest_pdf_file(path: str | Path, *, standard_id: str, repo_root: Path,
                    id_stem: str | None = None) -> list[Chunk]:
    """One PDF through `pdftotext`, then the plain-text pipeline (no headings). Raises
    FileNotFoundError when the tool is absent so `ingest_library` can skip with a note; any
    other extraction failure yields no chunks."""
    if shutil.which("pdftotext") is None:
        raise FileNotFoundError("pdftotext (poppler) not on PATH")
    path = Path(path)
    try:
        text = subprocess.run(["pdftotext", "-q", str(path), "-"], capture_output=True,
                              text=True, errors="replace", timeout=120).stdout
    except Exception:  # noqa: BLE001 -- a corrupt PDF is skipped, not fatal
        return []
    source_path = _source_path(path, repo_root)
    stem = id_stem or path.stem
    return chunks_from(parse_text(text), standard_id=standard_id, stem=stem, source_path=source_path)


def ingest_library(directory: str | Path, *, standard_id: str = "library",
                   repo_root: Path, log=lambda _m: None) -> list[Chunk]:
    """Every readable document under `directory`, recursively, sorted for deterministic
    chunk ordering. A missing directory is an empty library, not an error."""
    directory = Path(directory)
    if not directory.is_dir():
        return []
    chunks: list[Chunk] = []
    pdf_note_sent = False
    for path in sorted(directory.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in LIBRARY_SUFFIXES:
            continue
        if path.name == "README.md":          # the folder's own manual is not a paper
            continue
        rel_stem = str(path.relative_to(directory).with_suffix(""))
        if path.suffix.lower() == ".adoc":
            from flux_knowledge.connectors.adoc import ingest_adoc_file

            chunks.extend(ingest_adoc_file(path, standard_id=standard_id,
                                           repo_root=repo_root))
        elif path.suffix.lower() == ".pdf":
            try:
                chunks.extend(ingest_pdf_file(path, standard_id=standard_id,
                                              repo_root=repo_root, id_stem=rel_stem))
            except FileNotFoundError:
                if not pdf_note_sent:
                    log("library: PDFs present but pdftotext (poppler) is not on "
                        "PATH; they are not indexed on this machine")
                    pdf_note_sent = True
        elif path.suffix.lower() in SOURCE_SUFFIXES:
            if ".git" in path.parts or "test" in path.parts or "tests" in path.parts:
                continue                       # implementations, not their test benches
            chunks.extend(ingest_source_file(path, standard_id=standard_id,
                                             repo_root=repo_root, id_stem=rel_stem))
        else:
            chunks.extend(ingest_text_file(path, standard_id=standard_id,
                                           repo_root=repo_root, id_stem=rel_stem))
    return chunks
