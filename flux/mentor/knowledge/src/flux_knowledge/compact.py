"""Compacting knowledge that does not fit its share of the window (D549, D550), in three
halves. `densify` (rules) drops what says nothing twice: repeated lines, runs of blank lines.
`select_relevant` (rules, D550) keeps the paragraphs nearest the run's FOCUS -- the part in
hand -- ranked lexically, whole, in their original order, while they fit. `condense` (the
model) asks the proposer for the text within a room, every number, formula, identifier, unit
and citation kept -- one call per source per run, memoised by the mentor.
"""

from __future__ import annotations

from typing import Any

__all__ = ["condense", "densify", "select_relevant"]


def densify(text: str) -> str:
    """Exact repeats of a line once (the first stays, case-insensitively), runs of blank
    lines one; nothing else is judged, a sheet's table row is as good as its prose."""
    seen: set[str] = set()
    out: list[str] = []
    blank = False
    for ln in text.splitlines():
        s = ln.strip()
        if not s:
            if not blank and out:
                out.append("")
            blank = True
            continue
        blank = False
        if s.lower() in seen:
            continue
        seen.add(s.lower())
        out.append(ln.rstrip())
    return "\n".join(out).strip()


def paragraphs(text: str, *, lines_per_block: int = 6) -> list[str]:
    """The text's paragraphs (blank-line separated); a paragraph with no blank lines in it at
    all (a sheet written as one block) is cut into runs of `lines_per_block` lines."""
    blocks = [b.strip("\n") for b in text.split("\n\n") if b.strip()]
    if len(blocks) > 1:
        return blocks
    lines = text.splitlines()
    return ["\n".join(lines[i:i + lines_per_block]) for i in range(0, len(lines), lines_per_block)] or [text]


def select_relevant(text: str, room: int, focus: str) -> tuple[str, int, int] | None:
    """(the paragraphs nearest `focus` that fit `room`, in original order; how many kept; how
    many there were), or None when nothing ranks (no focus, one paragraph, no term in common)."""
    if not focus or not focus.strip() or room <= 0:
        return None
    blocks = paragraphs(text)
    if len(blocks) < 2:
        return None
    try:
        from .document import Chunk
        from .retrieval import BM25Index

        index = BM25Index([Chunk(id=f"p#{i}", standard_id="focus", source_path="", heading=None, text=b)
                           for i, b in enumerate(blocks)])
        ranked = [int(h.chunk.id[2:]) for h in index.search(focus, k=len(blocks))]
    except Exception:  # noqa: BLE001 -- ranking is help; the caller falls back
        return None
    if not ranked:
        return None
    kept: set[int] = set()
    size = 0
    for i in ranked:
        if size + len(blocks[i]) + 2 > room:
            continue
        kept.add(i)
        size += len(blocks[i]) + 2
    if not kept:
        return None
    return "\n\n".join(blocks[i] for i in sorted(kept)), len(kept), len(blocks)


def condense(text: str, room: int, proposer: Any, *, title: str = "") -> str | None:
    """The model's condensation of `text` within `room` characters, or None when the model
    fails or overruns -- the caller then cuts and says so."""
    if room <= 0:
        return None
    prompt = (f"Condense the reference below to at most {room} characters. Keep every number, formula, "
              "identifier, unit and citation exactly as written; drop repetition and prose that adds "
              "nothing to a designer. Reply with the condensed text only.\n\n"
              + (f"{title}\n" if title else "") + text)
    try:
        got = (proposer.propose(prompt).text or "").strip()
    except Exception:  # noqa: BLE001 -- knowledge is help, never a gate
        return None
    return got if got and len(got) <= room else None
