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

import functools
import json
import os
import threading
import time
from typing import Any

__all__ = ["kind_of", "starts", "timeline"]

_CACHE: dict[str, tuple[int, int, list[list[dict[str, Any]]]]] = {}   # path -> (inode, bytes read, starts)
_READING = threading.Lock()
_KEPT = ("ev", "t", "id", "parent", "name", "why", "failed")


@functools.lru_cache(maxsize=8192)
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
    """The journal's events, one list per start (process). D779: read as it grows -- the file is
    only appended to, so a running loop's next look parses only what was added since the last,
    and each event keeps only what the bars need (not an end's output). A new file (a new
    inode) or a cut one is read afresh."""
    try:
        st = os.stat(path)
    except OSError:
        return []
    with _READING:
        got = _CACHE.get(path)
        if got is not None and got[0] == st.st_ino and got[1] <= st.st_size:
            _ino, offset, out = got
        else:
            offset, out = 0, []
        if offset < st.st_size:
            with open(path, "rb") as fh:
                fh.seek(offset)
                data = fh.read()
            end = data.rfind(b"\n") + 1                     # a line being written waits for the next look
            for raw in data[:end].splitlines():
                if b'"ev": "update"' in raw or b'"ev": "publish"' in raw:
                    continue                               # live fields and standings: not the bars
                try:
                    e = json.loads(raw)
                except ValueError:
                    continue
                if e.get("ev") == "hello" or not out:
                    out.append([])
                out[-1].append({k: (str(v)[:200] if k == "why" else v) for k, v in e.items() if k in _KEPT})
            offset += end
        _CACHE[path] = (st.st_ino, offset, out)
        # the earlier starts are done; the last may still grow under another look
        return [*out[:-1], list(out[-1])] if out else []


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


_TABLES: dict[tuple, dict[str, Any]] = {}      # (path, inode, start) -> its phases, read as they come
_RESULTS: dict[tuple, dict[str, Any]] = {}


def _table(path: str, idx: int, events: list[dict[str, Any]]) -> dict[str, Any]:
    """D780: a start's phases, built from the events added since the last look -- each phase's
    times, its children counted, the earliest and latest moment heard."""
    with _READING:
        key = (path, _CACHE.get(path, (None,))[0], idx)     # a new file (a new start's) is another table
        tb = _TABLES.get(key)
        if tb is None or tb["n"] > len(events):
            tb = _TABLES[key] = {"n": 0, "ph": {}, "t0": None, "last_t": None, "kind": {}}
        ph = tb["ph"]
        for e in events[tb["n"]:]:
            t = e.get("t")
            if t is not None:
                t = float(t)
                tb["t0"] = t if tb["t0"] is None else min(tb["t0"], t)
                tb["last_t"] = t if tb["last_t"] is None else max(tb["last_t"], t)
            if e.get("ev") == "start":
                ph[e["id"]] = {"id": e["id"], "parent": e.get("parent"), "name": str(e.get("name") or ""),
                               "why": str(e.get("why") or "")[:200], "t0": float(e["t"]), "t1": None, "failed": False, "kids": 0}
                if e.get("parent") in ph:
                    ph[e["parent"]]["kids"] += 1
            elif e.get("ev") == "end" and e.get("id") in ph:
                ph[e["id"]].update(t1=float(e["t"]), failed=bool(e.get("failed")))
        tb["n"] = len(events)
        return tb


def _kind_in(tb: dict[str, Any], p: dict[str, Any]) -> str:
    """A phase's kind: its own name's, else its nearest ancestor's (kept: it does not change)."""
    got = tb["kind"].get(p["id"])
    if got is None:
        q, got = p, None
        while q is not None and got is None:
            got = kind_of(q["name"])
            q = tb["ph"].get(q["parent"])
        got = tb["kind"][p["id"]] = got or "loop"
    return got


def timeline(path: str, start: int | None = None, *, limit: int = 4000, now: float | None = None,
             running: bool | None = None) -> dict[str, Any]:
    """One start's bars (the latest by default) and where its time went. D780: its phases are
    built as the journal grows, and a start that is not running is worked out once."""
    every = starts(path)
    if not every:
        return {"starts": [], "start": None, "bars": [], "kinds": [], "passes": []}
    idx = len(every) - 1 if start is None or not 0 <= start < len(every) else start
    events = every[idx]
    now = now or time.time()
    tb = _table(path, idx, events)
    ph = tb["ph"]
    last_t = tb["last_t"] if tb["last_t"] is not None else now
    alive = idx == len(every) - 1 and now - last_t < 3600    # the latest start, heard from lately: still going
    if running is not None:                                 # D755: the server knows: an ended loop is not running
        alive = alive and running
    said = []
    for i, ev in enumerate(every):
        other = tb if i == idx else _table(path, i, ev)
        said.append({"index": i, "t0": other["t0"], "t1": other["last_t"]})
    done = (path, _CACHE.get(path, (None,))[0], idx, len(events), limit, len(every))
    if not alive and done in _RESULTS:
        return {**_RESULTS[done], "starts": said}
    bars = []
    for p in ph.values():
        if p["kids"]:
            continue
        t1 = p["t1"] if p["t1"] is not None else (now if alive else last_t)
        bars.append({"name": p["name"], "why": p["why"], "kind": _kind_in(tb, p), "t0": p["t0"], "t1": t1,
                     "running": p["t1"] is None and alive, "failed": p["failed"]})
    bars.sort(key=lambda b: b["t0"])
    t0 = tb["t0"] if tb["t0"] is not None else now
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
    if len(bars) > limit:                                  # the longest kept: the short ones do not show anyway
        bars = sorted(bars, key=lambda b: b["t1"] - b["t0"], reverse=True)[:limit]
        bars.sort(key=lambda b: b["t0"])
    out = {"starts": said, "start": idx, "t0": t0, "t1": t_end, "running": alive, "bars": bars, "kinds": table,
           "passes": sorted(passes), "wall": t_end - t0}
    if not alive:
        if len(_RESULTS) > 64:                             # a few loops' worth, not every one ever looked at
            _RESULTS.clear()
        _RESULTS[done] = out
    return out
