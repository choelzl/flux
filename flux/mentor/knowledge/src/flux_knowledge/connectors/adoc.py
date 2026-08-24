"""AsciiDoc ingestion connector: turns a RISC-V-ISA-manual-style `.adoc` file into `Chunk`s,
one per paragraph, tagged with the nearest preceding section heading.

Not a full AsciiDoc parser: the corpus is five hand-picked files (knowledge/corpus/riscv-unpriv/,
see its PROVENANCE.md), so this strips only the constructs they use -- inline macros
(`insn:fence.i[]`, `ext:zifencei[]`, `cite:[majc]`), index entries (`(((...)))`), anchors
(`[[id]]`), attribute/admonition lines (`[NOTE]`, `:sectnums!:`), `include::` directives and
block delimiters (`====`, `----`, ...). Good enough for lexical retrieval; some table markup
(`|===`) and cross-references (`<<rv32>>`) survive. A real parser would replace this module,
not the `Chunk` contract.
"""

from __future__ import annotations

import re
from pathlib import Path

from flux_knowledge.document import Chunk, chunks_from, paragraphs

_MACRO_WITH_CONTENT = re.compile(r"\b\w+:([\w.\-]+)\[[^\]]*\]")
_MACRO_BRACKET_ONLY = re.compile(r"\b\w+:\[([^\]]*)\]")
_ANCHOR_SPAN = re.compile(r"\[#[^\]]*\]#([^#]*)#")
_INDEX_ENTRY = re.compile(r"\({2,3}[^()]*\){2,3}")
_HEADING = re.compile(r"^(=+)\s+(.*)$")
_BLOCK_DELIM = re.compile(r"^(={4,}|-{2,4}|\*{4,}|_{4,}|'{3,})$")
_SKIP_LINE = re.compile(r"^(\[[^\]]*\]|\[\[[^\]]*\]\]|include::.*|//.*|:\S+:.*)$")


def _clean_line(line: str) -> str:
    """Inline cleanups that cannot span a line break: macros and index entries."""
    line = _MACRO_WITH_CONTENT.sub(r"\1", line)
    line = _MACRO_BRACKET_ONLY.sub(r"\1", line)
    line = _INDEX_ENTRY.sub("", line)
    return line.strip()


def _clean_paragraph(text: str) -> str:
    """Cleanups that must see a whole paragraph, applied after its lines are joined.

    An anchored span (`[#id]#...#`) often straddles a line break in this corpus, so a per-line
    substitution would leave the markup in the retrievable text (D180).
    """
    return _ANCHOR_SPAN.sub(r"\1", text).strip()


def parse_adoc(text: str) -> list[tuple[str | None, str]]:
    """Split AsciiDoc source into `(heading, paragraph_text)` pairs, in document order.
    `heading` is the nearest preceding section title (any `=`-level), or `None` before the first
    one. Block delimiters and directive lines end a paragraph and are dropped; every line is
    cleaned of inline markup and every paragraph of anchors. The splitting itself is
    `flux_knowledge.document.paragraphs` (D443).
    """
    def heading(raw: str):
        m = _HEADING.match(raw)
        return False if not m else _clean_paragraph(_clean_line(m.group(2)))

    def skip(raw: str) -> bool:
        stripped = raw.strip()
        return bool(_BLOCK_DELIM.match(stripped) or _SKIP_LINE.match(stripped))

    return paragraphs(text, heading=heading, skip=skip, clean_line=_clean_line,
                      clean_paragraph=_clean_paragraph)


def ingest_adoc_file(path: str | Path, *, standard_id: str, repo_root: Path) -> list[Chunk]:
    """Ingest one `.adoc` file into `Chunk`s. Each chunk's `source_path` is repo-relative
    (computed from `repo_root`), like every other path this repo cites.
    """
    path = Path(path)
    text = path.read_text()
    source_path = str(path.resolve().relative_to(repo_root.resolve()))
    return chunks_from(parse_adoc(text), standard_id=standard_id, stem=path.stem,
                       source_path=source_path)


def ingest_adoc_directory(directory: str | Path, *, standard_id: str, repo_root: Path) -> list[Chunk]:
    """Ingest every `*.adoc` file in `directory` (sorted, for deterministic chunk ordering)."""
    chunks: list[Chunk] = []
    for path in sorted(Path(directory).glob("*.adoc")):
        chunks.extend(ingest_adoc_file(path, standard_id=standard_id, repo_root=repo_root))
    return chunks
