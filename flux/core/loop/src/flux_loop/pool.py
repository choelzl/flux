"""A POOL for what the loop measures (docs/decisions.md D525, review step 8a): a stage's
candidates and a sweep's points fan out over threads, `LoopRequest.workers` at a time.

The tools are subprocesses (yosys, OpenROAD, Verilator), each in its own temporary
directory, so threads are enough and nothing is pickled. What must stay on ONE thread stays
here on the caller's: the measurement cache (a JSON sidecar read, changed and written whole),
the campaign record (one SQLite connection), the state's lists. A worker gets the candidate
and returns numbers or the exception it hit; the caller writes.

Before D525 nothing ran in parallel: a six-point register sweep synthesised its points one
after another (26 s each placed), a stage measured its candidates in a row, and the seven
parts waited for one another (review §1.3.6: "the cheapest large speed-up left, and it needs
no model").
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Iterable, TypeVar

__all__ = ["run_parallel", "workers"]

T = TypeVar("T")


def workers(request: Any) -> int:
    """How many tool runs may go at once: the request's `workers`, or half the machine's
    cores up to four when it says 0 (a placement is a process of its own; two or three of
    them share a box without starving the model's turn)."""
    n = int(getattr(request, "workers", 0) or 0)
    if n <= 0:
        n = max(1, min(4, (os.cpu_count() or 2) // 2))
    return n


def _call(fn: Callable[[T], Any], item: T) -> tuple[Any, BaseException | None]:
    try:
        return fn(item), None
    except BaseException as exc:  # noqa: BLE001 -- the caller decides; a worker never raises
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return None, exc


def run_parallel(items: Iterable[T], fn: Callable[[T], Any], n: int) -> list[tuple[Any, BaseException | None]]:
    """`fn(item)` for every item, `n` at a time, results in the items' order, each beside the
    exception it raised (or None). One item, or one worker, runs inline."""
    todo = list(items)
    if n <= 1 or len(todo) <= 1:
        return [_call(fn, it) for it in todo]
    with ThreadPoolExecutor(max_workers=min(n, len(todo)), thread_name_prefix="flux-measure") as pool:
        futures = [pool.submit(_call, fn, it) for it in todo]
        return [f.result() for f in futures]
