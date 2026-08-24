"""Plain-text and Markdown ingestion for the library (D407).

`mentor/knowledge/library/` holds papers and documents a person dropped there -- other
people's works, gitignored, one machine's own stash -- and this connector is what makes
them retrievable: paragraphs split on blank lines, each tagged with the nearest
preceding Markdown heading, the same `Chunk` contract the AsciiDoc connector produces.
`.adoc` files under the library delegate to that connector, so a spec excerpt dropped
here reads the same as one in the corpus.

PDFs go through `pdftotext` (poppler) when the machine has it -- papers usually ARE
PDFs, and re-extraction on every index build is cheap next to guessing at bytes; a
machine without the tool skips them with a note through `log` rather than silently or
fatally. Light-touch on Markdown for the same reason the AsciiDoc connector is: good
enough for lexical retrieval, not a parser.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from flux_knowledge.document import Chunk

_MD_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")

#: What the library connector will read. Anything else in the folder is somebody's
#: dataset or image and is skipped -- the README owns that contract.
LIBRARY_SUFFIXES = (".md", ".txt", ".adoc", ".pdf")


def parse_text(text: str) -> list[tuple[str | None, str]]:
    """Split Markdown/plain text into `(heading, paragraph)` pairs, document order.

    `heading` is the nearest preceding Markdown heading (any `#` level, its markers
    stripped), or None before the first one -- plain `.txt` simply never sets one.
    Consecutive non-blank lines join into one paragraph with spaces, matching the
    AsciiDoc connector's treatment of a run of lines."""
    heading: str | None = None
    para_lines: list[str] = []
    out: list[tuple[str | None, str]] = []

    def flush() -> None:
        if para_lines:
            out.append((heading, " ".join(para_lines).strip()))
            para_lines.clear()

    for line in text.splitlines():
        m = _MD_HEADING.match(line)
        if m:
            flush()
            heading = m.group(2).strip().strip("#").strip() or None
            continue
        if not line.strip():
            flush()
            continue
        para_lines.append(line.strip())
    flush()
    return [(h, p) for h, p in out if p]


def _source_path(path: Path, repo_root: Path) -> str:
    try:
        return str(path.resolve().relative_to(Path(repo_root).resolve()))
    except ValueError:  # outside the repo (a test tmpdir): the name still identifies it
        return path.name


def ingest_text_file(path: str | Path, *, standard_id: str, repo_root: Path,
                     id_stem: str | None = None) -> list[Chunk]:
    """One `.md`/`.txt` file into `Chunk`s. `id_stem` (the library-relative path,
    suffix dropped) keeps ids short AND collision-free across subfolders; a bare call
    falls back to the file's own stem."""
    path = Path(path)
    source_path = _source_path(path, repo_root)
    stem = id_stem or path.stem
    return [
        Chunk(id=f"{standard_id}/{stem}#{i}", standard_id=standard_id,
              source_path=source_path, heading=heading, text=paragraph)
        for i, (heading, paragraph) in enumerate(parse_text(path.read_text(errors="replace")))
    ]


def ingest_pdf_file(path: str | Path, *, standard_id: str, repo_root: Path,
                    id_stem: str | None = None) -> list[Chunk]:
    """One PDF through `pdftotext`, then the plain-text pipeline (no headings -- an
    extracted PDF has none worth trusting). Raises FileNotFoundError when the tool is
    absent so `ingest_library` can skip WITH a note; any other extraction failure
    yields no chunks -- one broken download must not empty the library."""
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
    return [
        Chunk(id=f"{standard_id}/{stem}#{i}", standard_id=standard_id,
              source_path=source_path, heading=heading, text=paragraph)
        for i, (heading, paragraph) in enumerate(parse_text(text))
    ]


def ingest_library(directory: str | Path, *, standard_id: str = "library",
                   repo_root: Path, log=lambda _m: None) -> list[Chunk]:
    """Every readable document under `directory`, recursively, sorted for deterministic
    chunk ordering. Missing directory means an empty library, not an error -- the
    folder ships holding only its README and .gitignore."""
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
        else:
            chunks.extend(ingest_text_file(path, standard_id=standard_id,
                                           repo_root=repo_root, id_stem=rel_stem))
    return chunks
