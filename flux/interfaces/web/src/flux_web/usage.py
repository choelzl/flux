"""What a loop's model and agent turns cost (D694), from its transcript (`turns.jsonl`): turns,
seconds, tokens in, out and read from a cache, and USD where the agent prices it -- in all and
per agent or model. Tokens are recorded since D694; older turns count in turns and seconds only."""

from __future__ import annotations

import json
import os
from typing import Any

__all__ = ["usage"]

_KEYS = ("tokens_in", "tokens_out", "tokens_cached", "cost_usd")
_CACHE: dict[str, tuple[int, int, dict[str, Any]]] = {}   # path -> (inode, bytes read, the sums so far): D921


def _num(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _empty() -> dict[str, Any]:
    return {"turns": 0, "seconds": 0.0, "errors": 0, "counted": 0, **{k: 0.0 for k in _KEYS}}


def _add(into: dict[str, Any], t: dict[str, Any]) -> None:
    into["turns"] += 1
    into["seconds"] += _num(t.get("seconds"))
    into["errors"] += 1 if t.get("error") or str(t.get("ok")) == "False" else 0
    notes = t.get("notes") if isinstance(t.get("notes"), dict) else {}
    tin = t.get("tokens_in", notes.get("input_tokens"))       # a model turn before D694: its last exchange
    tout = t.get("tokens_out", notes.get("output_tokens"))
    if tin is not None or tout is not None:
        into["counted"] += 1
    into["tokens_in"] += _num(tin)
    into["tokens_out"] += _num(tout)
    into["tokens_cached"] += _num(t.get("tokens_cached"))
    into["cost_usd"] += _num(t.get("cost_usd"))


def grown(path: str, read: int) -> tuple[int, list[bytes], int] | None:
    """D921: a transcript's whole lines after byte `read` -- (its inode, the lines, the offset after
    them); a line being written waits. None when it is gone."""
    try:
        with open(path, "rb") as fh:
            st = os.fstat(fh.fileno())
            if st.st_size <= read:
                return st.st_ino, [], read
            fh.seek(read)
            data = fh.read(st.st_size - read)
    except OSError:
        return None
    end = data.rfind(b"\n") + 1
    return st.st_ino, data[:end].splitlines(), read + end


def usage(path: str | None) -> dict[str, Any]:
    """{total, by: [{who, kind, ...}], first, last} for a transcript; zeros without one. D921: summed
    as the file grows -- a turn appended adds that turn, the transcript is not read again (another
    file, or one cut, is read from its start)."""
    out: dict[str, Any] = {"total": _empty(), "by": [], "first": None, "last": None}
    if not path:
        return out
    try:
        st = os.stat(path)
    except OSError:
        return out
    got = _CACHE.get(path)
    if got is None or got[0] != st.st_ino or got[1] > st.st_size:
        got = (st.st_ino, 0, {"total": _empty(), "by": {}, "first": None, "last": None})
    ino, read, s = got
    more = grown(path, read)
    if more is None:
        return out
    if more[0] != ino:                                         # replaced between the look and the read: from its start
        ino, read, s = more[0], 0, {"total": _empty(), "by": {}, "first": None, "last": None}
        more = grown(path, 0) or (ino, [], 0)
    for raw in more[1]:
        try:
            t = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(t, dict):
            continue
        kind = str(t.get("kind") or "turn")
        who = str(t.get("agent") or t.get("model") or kind)
        _add(s["total"], t)
        _add(s["by"].setdefault((kind, who), {"kind": kind, "who": who, **_empty()}), t)
        ts = _num(t.get("ts")) or None
        if ts:
            s["first"] = min(s["first"] or ts, ts)
            s["last"] = max(s["last"] or ts, ts)
    _CACHE[path] = (ino, more[2], s)
    return {"total": dict(s["total"]), "by": sorted((dict(b) for b in s["by"].values()), key=lambda b: -b["seconds"]),
            "first": s["first"], "last": s["last"]}
