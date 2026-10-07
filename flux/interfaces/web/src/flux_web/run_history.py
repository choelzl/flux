"""Retained starts, campaigns and the byte range of one start in the shared log."""

from __future__ import annotations

import json
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
