"""Shared document/chunk types for the knowledge layer. A `Chunk` is the retrieval unit: one
paragraph-sized piece of a source document, always carrying the provenance to cite it.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


def paragraphs(text: str, *, heading, skip=None, clean_line=None, clean_paragraph=None
               ) -> list[tuple[str | None, str]]:
    """Split text into paragraphs: consecutive non-blank lines join with spaces; a blank line
    (or one `skip` drops) ends a paragraph. `heading(raw)` returns False for an ordinary line,
    or the heading text (possibly None) that labels the following paragraphs. Shared by the
    Markdown/plain-text and AsciiDoc connectors."""
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
    """`Chunk`s from `(heading, paragraph)` pairs, ids `standard_id/stem#i`."""
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
