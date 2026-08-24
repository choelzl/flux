"""Find/replace patching (D414), problem-agnostic: it edits TEXT. Exact match wins, whitespace-tolerant fallback, uniqueness always enforced, a refusal names lines."""

from __future__ import annotations

import re
from typing import Iterable

from .model import _json

__all__ = ["apply_patch", "focus_window", "parse_patch", "patch_prompt", "patch_schema"]

def patch_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "edits": {"type": "array", "items": {
                "type": "object",
                "properties": {"find": {"type": "string"}, "replace": {"type": "string"},
                               "nth": {"type": "integer", "minimum": 1}},
                "required": ["find", "replace"]}},
            "why": {"type": "string"},
        },
        "required": ["edits"],
    }


def focus_window(artifact: str, lines: list[int], context: int = 40) -> str:
    """The numbered lines around each located line (merged windows) plus the
    artifact's OUTLINE (its unindented lines), so a model reads what matters and can
    still anchor an edit anywhere (D422). No located lines: the whole artifact."""
    rows = artifact.splitlines()
    if not lines or not rows:
        return "\n".join(f"{i + 1:4d} | {ln}" for i, ln in enumerate(rows))
    keep: set[int] = set()
    for ln in lines:
        keep.update(range(max(1, ln - context), min(len(rows), ln + context) + 1))
    shown = sorted(keep)
    if len(shown) >= len(rows):
        return "\n".join(f"{i + 1:4d} | {ln}" for i, ln in enumerate(rows))
    out = [f"(window around line(s) {', '.join(str(x) for x in lines)}; "
           f"{len(rows)} lines in all -- an edit may anchor on ANY line of the artifact)"]
    prev = 0
    for i in shown:
        if i != prev + 1:
            out.append("      …")
        out.append(f"{i:4d} | {rows[i - 1]}")
        prev = i
    if prev < len(rows):
        out.append("      …")
    outline = [f"{i + 1:4d} | {ln}" for i, ln in enumerate(rows)
               if ln and not ln[0].isspace() and (i + 1) not in keep]
    if outline:
        out += ["", "outline (unindented lines elsewhere):"] + outline[:60]
    return "\n".join(out)


def patch_prompt(name: str, artifact: str, failure: str, view: str | None = None) -> str:
    numbered = view if view is not None else "\n".join(
        f"{i + 1:4d} | {ln}" for i, ln in enumerate(artifact.splitlines()))
    return (
        f"`{name}` was refused:\n\n{failure}\n\n"
        "Fix it with the SMALLEST possible EDITS -- do not rewrite. Each edit is an exact "
        "find/replace on the text: `find` must appear EXACTLY ONCE (include enough "
        "surrounding text to be unique, or set \"nth\") and is replaced verbatim by "
        "`replace`. Change only what the failure points at.\n\n"
        'Reply with ONLY JSON: {"edits": [{"find": "...", "replace": "..."}], '
        '"why": "<one sentence>"}\n\n'
        f"Current text (line numbers are for reading only):\n\n{numbered}\n")


def parse_patch(reply: str) -> tuple[list[dict] | None, str]:
    doc = _json(reply)
    if not isinstance(doc, dict):
        return None, "patch reply was not a JSON object"
    edits = doc.get("edits")
    if not isinstance(edits, list) or not edits:
        return None, "patch reply carried no edits"
    out = []
    for e in edits:
        if not isinstance(e, dict) or "find" not in e or "replace" not in e:
            return None, "an edit lacked find/replace"
        edit = {"find": str(e["find"]), "replace": str(e["replace"])}
        if isinstance(e.get("nth"), int):
            edit["nth"] = e["nth"]
        out.append(edit)
    return out, str(doc.get("why", ""))[:120]


def _find_span(source: str, find: str, nth: int | None = None
               ) -> tuple[tuple[int, int] | None, str | None]:
    def _lines(spans: Iterable[tuple[int, int]]) -> str:
        return ", ".join(str(source.count("\n", 0, a) + 1) for a, _b in spans)

    exact = []
    start = source.find(find)
    while start >= 0:
        exact.append((start, start + len(find)))
        start = source.find(find, start + 1)
    if len(exact) == 1:
        return exact[0], None
    if len(exact) > 1:
        if nth is not None and 1 <= nth <= len(exact):
            return exact[nth - 1], None
        return None, (f"appears {len(exact)} times (lines {_lines(exact)}), must be "
                      "unique -- add surrounding lines to the anchor, or set \"nth\"")
    tokens = find.split()
    if not tokens:
        return None, "empty `find`"
    pattern = re.compile(r"\s+".join(re.escape(t) for t in tokens))
    hits = [(m.start(), m.end()) for m in pattern.finditer(source)]
    if len(hits) == 1:
        return hits[0], None
    if len(hits) > 1:
        if nth is not None and 1 <= nth <= len(hits):
            return hits[nth - 1], None
        return None, (f"appears {len(hits)} times ignoring whitespace (lines "
                      f"{_lines(hits)}), must be unique -- add surrounding lines, or "
                      "set \"nth\"")
    return None, "text is not in the source" + _closest(source, find)


def _closest(source: str, find: str) -> str:
    """The source lines nearest to what the model quoted (D468): on the live NLU run a
    rejected patch cost a model call of an hour and a half, and the model had quoted a
    comment from an EARLIER attempt. Naming the nearest real lines turns the next edit into
    an anchored one instead of another guess."""
    import difflib

    wanted = next((ln.strip() for ln in find.splitlines() if ln.strip()), "")
    if not wanted:
        return ""
    lines = [ln for ln in source.splitlines() if ln.strip()]
    near = difflib.get_close_matches(wanted, [ln.strip() for ln in lines], n=3, cutoff=0.5)
    if not near:
        return ""
    numbered = []
    for hit in near:
        for i, ln in enumerate(source.splitlines(), 1):
            if ln.strip() == hit:
                numbered.append(f"{i}: {ln.strip()[:70]}")
                break
    return " -- the closest lines in the current source are " + "; ".join(numbered)


_NUMBERED = re.compile(r"^\s*\d+ \| ", re.M)


def _unnumbered(text: str) -> str:
    """The `NN | ` prefixes of the "line numbers for reading only" listing, stripped when a
    model pasted them into its find/replace (D484: a live edit failed for exactly that)."""
    lines = text.splitlines(keepends=True)
    if lines and all(_NUMBERED.match(ln) for ln in lines if ln.strip()):
        return "".join(_NUMBERED.sub("", ln, count=1) for ln in lines)
    return text


def apply_patch(source: str, edits: list[dict]) -> tuple[str | None, str | None]:
    """Exact-once find/replace, whitespace-tolerant fallback, uniqueness always
    enforced; a refusal names the reason (and the lines) so the model can act."""
    out = source
    for i, e in enumerate(edits, 1):
        find, repl = _unnumbered(e["find"]), _unnumbered(e["replace"])
        if not find:
            return None, f"edit {i}: empty `find`"
        nth = e.get("nth")
        span, err = _find_span(out, find, int(nth) if isinstance(nth, int) else None)
        if span is None:
            return None, f"edit {i}: `find` {err}: {find[:70]!r}"
        out = out[:span[0]] + repl + out[span[1]:]
    if out == source:
        return None, "the edits changed nothing"
    return out, None
