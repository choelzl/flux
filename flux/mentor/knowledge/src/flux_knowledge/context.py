"""A prompt block from the knowledge index (D443): a few targeted lookups, deduplicated,
source-cited, every excerpt whole (D548: no character budget here -- the mentor bounds
knowledge by the model's window). The NLU study and the interconnect flow had each written
this loop; this is the one copy, and a problem's `mentor_sections` can call it for its
library, the design-guidance corpus or a standard alike."""

from __future__ import annotations

from typing import Any, Iterable

__all__ = ["library_context"]

LIBRARY_HEADER = ("FROM THE OPERATOR'S LIBRARY (papers on this machine, retrieved "
                  "lexically -- excerpts, cite-worthy for direction only):")


def library_context(queries: Iterable[str], *, standard_id: str | None = "library",
                    k_per_query: int = 2, header: str | None = LIBRARY_HEADER,
                    prefix: str = "  * ", cite: bool = True, index: Any = None) -> str:
    """Lines of `prefix[source] text` for the top `k_per_query` hits of each query, each chunk
    once and whole; "" when nothing is found or the index is unavailable (a run is the same
    without papers). `index` overrides the default index (tests)."""
    try:
        from .retrieval import knowledge_lookup
    except Exception:  # noqa: BLE001
        return ""
    seen: set[str] = set()
    lines: list[str] = []
    try:
        for q in queries:
            # ask for a few more than needed: a degenerate chunk (a table row of "FP16 FP16
            # FP16", a figure caption) scores high on repetition and says nothing (D477)
            taken = 0
            for hit in knowledge_lookup(q, standard_id=standard_id, k=k_per_query + 3,
                                        index=index):
                if taken >= k_per_query:
                    break
                c = hit.chunk
                if c.id in seen or _degenerate(c.text):
                    continue
                seen.add(c.id)
                taken += 1
                src = f"[{c.source_path.rsplit('/', 1)[-1]}] " if cite else ""
                lines.append(f"{prefix}{src}{c.text}")
    except Exception:  # noqa: BLE001
        return _render(header, lines)
    return _render(header, lines)


def _degenerate(text: str) -> bool:
    """Fewer than four distinct words, or one word making up most of it: a table row or a
    caption, not a paragraph worth a line of the model's prompt (a five-word sentence is)."""
    words = [w for w in text.replace("\n", " ").split() if w.strip("|,.:;()")]
    if len(set(w.lower() for w in words)) < 4:
        return True
    top = max((words.count(w) for w in set(words)), default=0)
    return len(words) >= 4 and top / len(words) > 0.5


def _render(header: str | None, lines: list[str]) -> str:
    if not lines:
        return ""
    return (header + "\n" if header else "") + "\n".join(lines)
