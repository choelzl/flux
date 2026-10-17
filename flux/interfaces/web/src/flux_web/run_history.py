"""Retained starts, campaigns and the byte range of one start in the shared log."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from typing import Any, BinaryIO

from .confine import open_read, within

_START = re.compile(rb"^\xe2\x94\x80\xe2\x94\x80 started (\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) ")


def log_range(fh: BinaryIO, run: dict[str, Any], following: dict[str, Any] | None) -> tuple[int, int]:
    """Use saved byte boundaries when available; older starts are found by their dated marker."""
    options = json.loads(run.get("options") or "{}")
    next_options = json.loads((following or {}).get("options") or "{}")
    offset, end = options.get("log_offset"), next_options.get("log_offset")
    fh.seek(0, 2)
    size = fh.tell()
    if isinstance(offset, int) and 0 <= offset <= size and isinstance(end, int) and offset <= end <= size:
        return offset, end
    fh.seek(offset if isinstance(offset, int) and 0 <= offset <= size else 0)
    lo, marked = (offset if fh.tell() == offset else None), False
    while True:
        at = fh.tell()
        line = fh.readline(1 << 20)
        if not line:
            break
        match = _START.match(line)
        if not match:
            continue
        if lo is not None:
            if marked:
                return lo, at
            marked = True
        else:
            stamp = datetime.strptime(match[1].decode(), "%Y-%m-%d %H:%M:%S").timestamp()
            if abs(stamp - float(run["started"])) < 2:
                lo, marked = at, True
    if lo is None:
        raise FileNotFoundError("this start's log is no longer available")
    return lo, size


def trace_path(run: dict[str, Any], campaign: str, kind: str, runs: Any) -> str:
    """A retained campaign's transcript or journal, confined to this loop and its cache."""
    if kind not in ("events", "turns"):
        raise ValueError("choose events or turns")
    with open_read(f"{run['db']}.runs.json", *runs.roots(run), text=True) as fh:
        pointer = json.load(fh)
    directory = pointer.get(campaign) if isinstance(pointer, dict) else None
    if not isinstance(directory, str):
        raise FileNotFoundError("this campaign's trace is no longer available")
    return str(within(f"{directory}/{kind}.jsonl", *runs.roots(run)))


# D987: a start that did nothing is not kept -- one stopped before any work, seconds after it began
_SETUP_HEADS = ("gate", "propose: decompose", "knowledge", "validate", "records", "feedback", "setup")


def _segment(run: dict[str, Any], runs: Any) -> tuple[str, int, int] | None:
    """This start's journal: (events.jsonl, the offset of its hello, the next hello's or the end)."""
    try:
        with open_read(f"{run['db']}.runs.json", *runs.roots(run), text=True) as fh:
            pointer = json.load(fh)
    except (OSError, ValueError):
        return None
    started, ended = float(run.get("started") or 0), float(run.get("ended") or 0) or float("inf")
    for directory in (pointer.values() if isinstance(pointer, dict) else ()):
        if not isinstance(directory, str):
            continue
        try:
            events = str(within(f"{directory}/events.jsonl", *runs.roots(run)))
            marks = str(within(f"{directory}/marks.jsonl", *runs.roots(run)))
            with open(marks, encoding="utf-8") as fh:
                hellos = [m["at"] for m in map(json.loads, filter(str.strip, fh)) if m.get("ev") == "hello"]
            with open(events, "rb") as fh:
                for i, at in enumerate(hellos):
                    fh.seek(at)
                    t = float(json.loads(fh.readline() or b"{}").get("t") or 0)
                    if started - 10 <= t <= ended + 5:
                        fh.seek(0, 2)
                        return events, at, hellos[i + 1] if i + 1 < len(hellos) else fh.tell()
        except (OSError, ValueError, KeyError, FileNotFoundError):
            continue
    return None


def empty_start(run: dict[str, Any], runs: Any) -> bool:
    """True for an ended start whose journal shows nothing but setup (the gates, the division, the
    inputs): stopped before a search, a design, a check or a measurement began. Without a journal
    there is no telling: such a start is kept."""
    if not run.get("ended"):
        return False
    seg = _segment(run, runs)
    if seg is None:
        return False
    path, lo, hi = seg
    with open(path, "rb") as fh:
        fh.seek(lo)
        left = hi - lo
        for raw in fh:                             # D1071: a line at a time -- the first sign of work ends it
            if left <= 0:
                break
            left -= len(raw)
            try:
                e = json.loads(raw)
            except ValueError:
                continue
            if e.get("ev") == "mark" and e.get("name") == "pass":
                return False                       # a pass began after the first: work was done
            # D1233: a task says its box; a journal before it, its name
            box = (e.get("tags") or {}).get("box") if e.get("ev") == "start" else None
            if e.get("ev") == "start" and e.get("parent") is None and (box != "setup" if box else not str(e.get("name", "")).lower().startswith(_SETUP_HEADS)):
                return False
    return True


def forget_start(run: dict[str, Any], runs: Any, store: Any, last: bool) -> None:
    """Drop an empty start (D987): its row; and when it is the loop's latest start, its part of the
    loop's log and of the journal too (an earlier one's would take later starts' with it)."""
    options = json.loads(run.get("options") or "{}")
    offset = options.get("log_offset")
    if last and isinstance(offset, int) and run.get("log"):
        try:
            with open(run["log"], "r+b") as fh:
                fh.seek(0, 2)
                if offset <= fh.tell():
                    fh.truncate(offset)
        except OSError:
            pass
    seg = _segment(run, runs) if last else None
    if seg is not None and seg[2] == os.path.getsize(seg[0]):          # only the journal's last start is cut back
        path, lo, _hi = seg
        try:
            with open(path, "r+b") as fh:
                fh.truncate(lo)
            marks = os.path.join(os.path.dirname(path), "marks.jsonl")
            with open(marks, encoding="utf-8") as fh:
                kept = [ln for ln in fh if ln.strip() and json.loads(ln).get("at", 0) < lo]
            with open(marks, "w", encoding="utf-8") as fh:
                fh.writelines(kept)
        except (OSError, ValueError):
            pass
    store.delete_run(run["id"])
