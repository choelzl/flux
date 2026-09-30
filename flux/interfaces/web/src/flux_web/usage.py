"""What a loop's model and agent turns cost (D694), from its transcript (`turns.jsonl`): turns,
seconds, tokens in, out and read from a cache, and USD where the agent prices it -- in all and
per agent or model. Tokens are recorded since D694; older turns count in turns and seconds only."""

from __future__ import annotations

import json
import os
from typing import Any

__all__ = ["usage"]

_KEYS = ("tokens_in", "tokens_out", "tokens_cached", "cost_usd")
_CACHE: dict[str, tuple[tuple[int, float], dict[str, Any]]] = {}


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


def usage(path: str | None) -> dict[str, Any]:
    """{total, by: [{who, kind, ...}], first, last} for a transcript; zeros without one."""
    out: dict[str, Any] = {"total": _empty(), "by": [], "first": None, "last": None}
    if not path:
        return out
    try:
        st = os.stat(path)
    except OSError:
        return out
    key = (st.st_size, st.st_mtime)
    got = _CACHE.get(path)
    if got and got[0] == key:
        return got[1]
    by: dict[tuple[str, str], dict[str, Any]] = {}
    with open(path, "rb") as fh:
        for raw in fh:
            try:
                t = json.loads(raw)
            except ValueError:
                continue
            kind = str(t.get("kind") or "turn")
            who = str(t.get("agent") or t.get("model") or kind)
            _add(out["total"], t)
            _add(by.setdefault((kind, who), {"kind": kind, "who": who, **_empty()}), t)
            ts = _num(t.get("ts")) or None
            if ts:
                out["first"] = min(out["first"] or ts, ts)
                out["last"] = max(out["last"] or ts, ts)
    out["by"] = sorted(by.values(), key=lambda b: -b["seconds"])
    _CACHE[path] = (key, out)
    return out
