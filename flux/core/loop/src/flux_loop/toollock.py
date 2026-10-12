"""At most N checks and measurements at once (D1004): `budget.tools_at_once` -- for a tool that
breaks when two of its runs share a machine (two compilations at once). One slot is a file lock in
the loop's `out/.flux-tools/`, so passes at once, parts at once and an agent's `flux probe` (a
process of its own) all wait for the same slots; a run that waited says so as a `wait:` task."""

from __future__ import annotations

import fcntl
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

#: commands that only write a design, never check or measure one: not counted
FREE = ("generate", "search", "baseline")


def _open(path: Path) -> int | None:
    try:
        return os.open(path, os.O_RDWR | os.O_CREAT, 0o666)
    except OSError:
        try:
            return os.open(path, os.O_RDONLY)      # a probe's sandbox may see the loop's folder read-only
        except OSError:
            return None


def _take(folder: Path, n: int) -> int | None:
    for i in range(n):
        fd = _open(folder / f"slot-{i}.lock")
        if fd is None:
            continue
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except OSError:
            os.close(fd)
    return None


@contextmanager
def tool_slot(home: str | Path, n: int, what: str) -> Iterator[None]:
    """Hold one of `n` slots while the block runs (`n` <= 0, or a command in `FREE`: no limit)."""
    if n <= 0 or str(what).split(" ", 1)[0].rstrip(":") in FREE:
        yield
        return
    folder = Path(home or ".") / "out" / ".flux-tools"
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    fd = _take(folder, n)
    if fd is None:
        from flux_profile import phase

        with phase("wait: a check or measurement slot", why=f"{n} at once (budget.tools_at_once): {what}"[:200]):
            while fd is None:
                time.sleep(0.2)
                fd = _take(folder, n)
    try:
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
