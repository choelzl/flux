"""AsciiDoc ingestion connector. Turns a RISC-V-ISA-manual-style `.adoc` source file into a list
of `Chunk`s, one per paragraph, tagged with the nearest preceding section heading.

Deliberately a *light touch*, not a full AsciiDoc parser: this corpus is five hand-picked files
(knowledge/corpus/riscv-unpriv/, see its PROVENANCE.md), not an arbitrary AsciiDoc tree, so this
strips the specific handful of constructs that source actually uses — inline macros
(`insn:fence.i[]`, `ext:zifencei[]`, `cite:[majc]`), index entries (`(((...)))`), anchors
(`[[id]]`), attribute/admonition lines (`[NOTE]`, `:sectnums!:`), `include::` directives, and
block delimiters (`====`, `----`, ...) — rather than pulling in an external AsciiDoc library for
this. Output quality is "good enough for lexical retrieval," not publication-typeset text: some
table markup (`|===`) and cross-reference syntax (`<<rv32>>`) survive uncleaned. If the corpus
grows to need a real parser, replace this module, not the `Chunk` contract it produces.
"""

from __future__ import annotations

import re
from pathlib import Path

from flux_knowledge.document import Chunk

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

    An AsciiDoc anchored span opens with `[#id]#` and closes with `#`, and in this repo's own
    corpus those routinely straddle a line break — `m-st-ext.adoc` opens one on line 23 and closes
    it on line 25. Running the substitution per line could never match those, so the raw markup
    survived into the retrievable text of 10 of the corpus's 195 chunks: an agent reading a hit got
    `[#norm:mul_op]#mul performs ...` instead of the sentence, and BM25 indexed the marker's
    fragments as if they were content (docs/decisions.md D180).
    """
    return _ANCHOR_SPAN.sub(r"\1", text).strip()


def parse_adoc(text: str) -> list[tuple[str | None, str]]:
    """Split AsciiDoc source into `(heading, paragraph_text)` pairs, in document order.
    `heading` is the nearest preceding section title (any `=`-level), or `None` before the first
    one. Consecutive non-blank, non-directive lines join into one paragraph with spaces —
    matching how AsciiDoc itself treats a run of lines with no blank line between them.
    """
    heading: str | None = None
    para_lines: list[str] = []
    paragraphs: list[tuple[str | None, str]] = []

    def flush() -> None:
        nonlocal para_lines
        if para_lines:
            joined = _clean_paragraph(" ".join(para_lines))
            if joined:
                paragraphs.append((heading, joined))
            para_lines = []

    for raw in text.splitlines():
        stripped = raw.strip()
        if _BLOCK_DELIM.match(stripped) or _SKIP_LINE.match(stripped):
            flush()
            continue
        heading_match = _HEADING.match(raw)
        if heading_match:
            flush()
            heading = _clean_paragraph(_clean_line(heading_match.group(2)))
            continue
        if not stripped:
            flush()
            continue
        para_lines.append(_clean_line(raw))
    flush()
    return paragraphs


def ingest_adoc_file(path: str | Path, *, standard_id: str, repo_root: Path) -> list[Chunk]:
    """Ingest one `.adoc` file into `Chunk`s. `source_path` on each chunk is repo-relative
    (matching how every other component in this repo cites file paths — e.g.
    `CorpusEntry.workload_path`), computed from `repo_root`, not stored as an absolute path.
    """
    path = Path(path)
    text = path.read_text()
    source_path = str(path.resolve().relative_to(repo_root.resolve()))
    chunks = []
    for index, (heading, paragraph) in enumerate(parse_adoc(text)):
        chunks.append(
            Chunk(
                id=f"{standard_id}/{path.stem}#{index}",
                standard_id=standard_id,
                source_path=source_path,
                heading=heading,
                text=paragraph,
            )
        )
    return chunks


def ingest_adoc_directory(directory: str | Path, *, standard_id: str, repo_root: Path) -> list[Chunk]:
    """Ingest every `*.adoc` file in `directory` (sorted, for deterministic chunk ordering)."""
    chunks: list[Chunk] = []
    for path in sorted(Path(directory).glob("*.adoc")):
        chunks.extend(ingest_adoc_file(path, standard_id=standard_id, repo_root=repo_root))
    return chunks
