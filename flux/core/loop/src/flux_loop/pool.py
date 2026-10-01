"""A pool for what the loop measures (D525): a stage's candidates and a sweep's points fan out
over threads, `LoopRequest.workers` at a time.

The tools are subprocesses (yosys, OpenROAD, Verilator), each in its own temporary directory,
so threads suffice and nothing is pickled. What must stay on one thread stays on the caller's:
the measurement cache (a JSON sidecar written whole), the campaign record (one SQLite
connection), the state's lists. A worker returns numbers or the exception it hit; the caller
writes.
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Iterable, TypeVar

__all__ = ["parallel_cap", "run_parallel", "workers"]

T = TypeVar("T")


def parallel_cap() -> int | None:
    """What may run at once, at most, whatever the document asks (D740): `FLUX_PARALLEL_MAX`,
    which `flux serve` sets to 1 for a run unless an admin allows the loop parallel work (D741:
    then unset, and the document says how much). None (no cap) when unset."""
    raw = os.environ.get("FLUX_PARALLEL_MAX", "").strip()
    try:
        return max(1, int(raw)) if raw else None
    except ValueError:
        return 1


def capped(n: int) -> int:
    cap = parallel_cap()
    return max(1, min(int(n), cap) if cap else int(n))


def workers(request: Any) -> int:
    """How many tool runs may go at once: the request's `workers`, or half the machine's
    cores up to four when it says 0 (a placement is a process of its own; two or three of
    them share a box without starving the model's turn); never past `parallel_cap`."""
    n = int(getattr(request, "workers", 0) or 0)
    if n <= 0:
        n = max(1, min(4, (os.cpu_count() or 2) // 2))
    return capped(n)


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
    from flux_profile import carried

    fn = carried(fn)                     # D739: the workers' phases under the caller's
    with ThreadPoolExecutor(max_workers=min(n, len(todo)), thread_name_prefix="flux-measure") as pool:
        futures = [pool.submit(_call, fn, it) for it in todo]
        return [f.result() for f in futures]
