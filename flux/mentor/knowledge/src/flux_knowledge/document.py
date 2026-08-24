"""Shared document/chunk types for the knowledge layer (docs/agent-surface.md, docs/decisions.md
D3). A `Chunk` is the retrieval unit: one paragraph-sized piece of a source document, tagged with
enough provenance to cite where it came from — never returned or stored without that provenance,
matching this repo's evaluator `Result.provenance` convention.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


def paragraphs(text: str, *, heading, skip=None, clean_line=None, clean_paragraph=None
               ) -> list[tuple[str | None, str]]:
    """The one paragraph splitter (D443): consecutive non-blank lines join into one paragraph
    with spaces, a blank line (or a line `skip` says to drop) ends one, and `heading(raw)`
    returns False for an ordinary line or the heading text (possibly None) for a heading
    line, which then labels the paragraphs that follow. The Markdown/plain-text and the
    AsciiDoc connectors are this loop with their own `heading`, `skip` and cleaners."""
    current: str | None = None
    lines: list[str] = []
    out: list[tuple[str | None, str]] = []

    def flush() -> None:
        if lines:
            joined = " ".join(lines)
            joined = (clean_paragraph(joined) if clean_paragraph else joined).strip()
            if joined:
                out.append((current, joined))
            lines.clear()

    for raw in text.splitlines():
        if skip is not None and skip(raw):
            flush()
            continue
        head = heading(raw)
        if head is not False:
            flush()
            current = head
            continue
        if not raw.strip():
            flush()
            continue
        lines.append(clean_line(raw) if clean_line else raw.strip())
    flush()
    return out


def chunks_from(pairs, *, standard_id: str, stem: str, source_path: str) -> list["Chunk"]:
    """`Chunk`s from `(heading, paragraph)` pairs, ids `standard_id/stem#i` (D443)."""
    return [Chunk(id=f"{standard_id}/{stem}#{i}", standard_id=standard_id, source_path=source_path,
                  heading=heading, text=paragraph) for i, (heading, paragraph) in enumerate(pairs)]


@dataclass(frozen=True, slots=True)
class Chunk:
    """One retrievable unit of ingested text.

    `id` is stable and content-derived (`{standard_id}/{source_stem}#{index}`), not an
    auto-increment counter, so re-ingesting the same corpus produces the same ids.
    """

    id: str
    standard_id: str
    source_path: str
    heading: str | None
    text: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
