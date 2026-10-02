"""Where a loop's time goes (D694), from its journal (`events.jsonl`): each start of the loop is
one process (a `hello` line), each phase a bar from its start to its end.

Every phase that has no child phase (the work itself: a tool, an agent, a model call) is put in
the kind of work of its nearest ancestor, itself first -- an agent, a model call, a check of
the gate, a stage's measurement, a generation, a record re-verified, the knowledge prepared,
or the loop's own bookkeeping. Per kind: how
many calls, their average and longest, the busy time (the union of their bars: what the wall
clock saw, parallel work counted once) and its share of the wall clock, and the summed time
(above the busy time only when calls ran side by side, D772). Passes start where a `propose: decompose` phase starts at the top."""

from __future__ import annotations

import json
import os
import time
from typing import Any

__all__ = ["kind_of", "starts", "timeline"]

_CACHE: dict[str, tuple[tuple[int, float], list[list[dict[str, Any]]]]] = {}


def kind_of(name: str) -> str | None:
    """The kind of work a phase names, or None when it names none of its own."""
    head, _, rest = name.partition(":")
    head, rest = head.strip(), rest.strip()
    if head == "agent":
        return "agent"
    if head == "llm" or name.startswith("model"):
        return "model"
    if head == "simulation":
        return f"stage {rest.split()[0]}" if rest else "stage"
    if head in ("test", "gate"):
        return "gate"
    if head == "generation":
        return "generation"
    if head == "probe":
        return "probe"
    if head == "records":
        return "re-verify"
    if head == "knowledge":
        return "knowledge"
    return None


def starts(path: str) -> list[list[dict[str, Any]]]:
    """The journal's events, one list per start (process), cached while the file is unchanged."""
    try:
        st = os.stat(path)
    except OSError:
        return []
    key = (st.st_size, st.st_mtime)
    got = _CACHE.get(path)
    if got and got[0] == key:
        return got[1]
    out: list[list[dict[str, Any]]] = []
    with open(path, "rb") as fh:
        for raw in fh:
            if b'"ev": "update"' in raw or b'"ev": "publish"' in raw:
                continue                                   # live fields and standings: not the bars
            try:
                e = json.loads(raw)
            except ValueError:
                continue
            if e.get("ev") == "hello" or not out:
                out.append([])
            out[-1].append(e)
    _CACHE[path] = (key, out)
    return out


def _union(spans: list[tuple[float, float]]) -> float:
    total, end = 0.0, None
    for a, b in sorted(spans):
        if end is None or a > end:
            total += b - a
            end = b
        elif b > end:
            total += b - end
            end = b
    return total


def timeline(path: str, start: int | None = None, *, limit: int = 4000, now: float | None = None,
             running: bool | None = None) -> dict[str, Any]:
    """One start's bars (the latest by default) and where its time went."""
    every = starts(path)
    if not every:
        return {"starts": [], "start": None, "bars": [], "kinds": [], "passes": []}
    idx = len(every) - 1 if start is None or not 0 <= start < len(every) else start
    events = every[idx]
    now = now or time.time()
    ph: dict[int, dict[str, Any]] = {}
    for e in events:
        if e.get("ev") == "start":
            ph[e["id"]] = {"id": e["id"], "parent": e.get("parent"), "name": str(e.get("name") or ""),
                           "why": str(e.get("why") or "")[:200], "t0": float(e["t"]), "t1": None, "failed": False, "kids": 0}
        elif e.get("ev") == "end" and e.get("id") in ph:
            ph[e["id"]].update(t1=float(e["t"]), failed=bool(e.get("failed")))
    for p in ph.values():
        if p["parent"] in ph:
            ph[p["parent"]]["kids"] += 1
    last_t = max([float(e.get("t", 0)) for e in events] or [now])
    alive = idx == len(every) - 1 and now - last_t < 3600    # the latest start, heard from lately: still going
    if running is not None:                                 # D755: the server knows: an ended loop is not running
        alive = alive and running
    bars = []
    for p in ph.values():
        if p["kids"]:
            continue
        q, kind = p, None
        while q is not None and kind is None:
            kind = kind_of(q["name"])
            q = ph.get(q["parent"])
        t1 = p["t1"] if p["t1"] is not None else (now if alive else last_t)
        bars.append({"name": p["name"], "why": p["why"], "kind": kind or "loop", "t0": p["t0"], "t1": t1,
                     "running": p["t1"] is None and alive, "failed": p["failed"]})
    bars.sort(key=lambda b: b["t0"])
    t0 = min([float(e["t"]) for e in events if "t" in e] or [now])
    t_end = now if alive else last_t
    kinds: dict[str, dict[str, Any]] = {}
    for b in bars:
        k = kinds.setdefault(b["kind"], {"kind": b["kind"], "count": 0, "summed": 0.0, "longest": 0.0, "spans": []})
        k["count"] += 1
        k["summed"] += b["t1"] - b["t0"]
        k["longest"] = max(k["longest"], b["t1"] - b["t0"])
        k["spans"].append((b["t0"], b["t1"]))
    wall = max(t_end - t0, 1e-9)
    table = []
    for k in kinds.values():
        busy = _union(k.pop("spans"))
        table.append({**k, "busy": busy, "share": busy / wall, "summed": k["summed"], "mean": k["summed"] / max(k["count"], 1)})
    table.sort(key=lambda k: -k["busy"])
    passes = [p["t0"] for p in ph.values() if p["parent"] is None and p["name"].startswith("propose: decompose")]
    said = []
    for i, ev in enumerate(every):
        ts = [float(e["t"]) for e in ev if "t" in e]
        said.append({"index": i, "t0": min(ts) if ts else None, "t1": max(ts) if ts else None})
    if len(bars) > limit:                                  # the longest kept: the short ones do not show anyway
        bars = sorted(bars, key=lambda b: b["t1"] - b["t0"], reverse=True)[:limit]
        bars.sort(key=lambda b: b["t0"])
    return {"starts": said, "start": idx, "t0": t0, "t1": t_end, "running": alive, "bars": bars, "kinds": table,
            "passes": sorted(passes), "wall": t_end - t0}
