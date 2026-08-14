"""Where the wall-clock actually went (docs/decisions.md D295).

A run of this repo's demo spends its time in four very different places — external tools
(Yosys, OpenROAD, Verilator), local model inference, the search's own bookkeeping, and waiting —
and until this existed none of them were separable. That mattered practically: a generation round
that produced nothing looked identical to one that was merely slow, and an hour was spent
diagnosing "the demo is stuck" when the answer was one 48-minute model call.

TWO CLOCKS, and reporting only one of them would lie. `phase()` sums the time spent INSIDE each
category across every thread, so with concurrent escalation the sum legitimately exceeds the
elapsed wall-clock — eight placements running together contribute eight seconds of tool time per
second of real time. Both numbers are reported, and their ratio is the concurrency actually
achieved rather than the concurrency requested.

Deliberately tiny and dependency-free: a dict, a lock, a context manager. It is a measuring
instrument, not a tracing framework, and it must never be able to fail a run — every entry point
swallows nothing but also allocates nothing that could raise.
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager

_LOCK = threading.Lock()
_PHASES: dict[str, list] = {}          # name -> [calls, seconds]
_STARTED = time.perf_counter()


def reset() -> None:
    """Start a fresh measurement window."""
    global _STARTED
    with _LOCK:
        _PHASES.clear()
        _STARTED = time.perf_counter()


def record(name: str, seconds: float) -> None:
    with _LOCK:
        row = _PHASES.get(name)
        if row is None:
            _PHASES[name] = [1, seconds]
        else:
            row[0] += 1
            row[1] += seconds


@contextmanager
def phase(name: str):
    """Time a block into `name`. Records even when the block raises — a tool that failed still
    spent the time, and hiding that would make a slow failure look free."""
    t0 = time.perf_counter()
    try:
        yield
    finally:
        record(name, time.perf_counter() - t0)


def snapshot() -> dict[str, tuple[int, float]]:
    with _LOCK:
        return {k: (v[0], v[1]) for k, v in _PHASES.items()}


def elapsed_s() -> float:
    return time.perf_counter() - _STARTED


def report_lines(*, derived: dict[str, float] | None = None) -> list[str]:
    """A table, ordered by cost. `derived` carries figures the caller computed by subtraction
    (for example "proposing, excluding model time"), kept separate from measured phases because
    a difference of two measurements is not itself a measurement."""
    snap = snapshot()
    total = elapsed_s()
    if not snap and not derived:
        return ["(no timing recorded)"]
    lines = [f"{'phase':<34}{'calls':>7}{'seconds':>10}{'% of run':>10}"]
    for name, (calls, secs) in sorted(snap.items(), key=lambda kv: -kv[1][1]):
        lines.append(f"{name:<34}{calls:>7}{secs:>10.1f}{secs / total * 100:>9.1f}%")
    for name, secs in (derived or {}).items():
        lines.append(f"{name:<34}{'':>7}{secs:>10.1f}{secs / total * 100:>9.1f}%")
    tool = sum(s for n, (_, s) in snap.items() if n.startswith("tool:"))
    lines.append(f"{'':<34}{'':>7}{'':>10}{'':>10}")
    lines.append(f"{'ELAPSED (wall clock)':<34}{'':>7}{total:>10.1f}{100.0:>9.1f}%")
    if tool > total:
        lines.append(f"    tool time sums to {tool:.0f}s over {total:.0f}s elapsed "
                     f"= {tool / total:.1f}x concurrency actually achieved")
    return lines


def seconds(name: str) -> float:
    """Measured seconds recorded under `name`, or 0."""
    return snapshot().get(name, (0, 0.0))[1]


def outside(total_phase: str, *inner_prefixes: str) -> float:
    """Seconds a phase spent NOT inside the phases named by these prefixes.

    A difference of two measurements, which is why callers label the result as derived rather
    than measured: "proposing, outside the model" is the proposing total minus the model time
    within it, and it is only as trustworthy as the instrumentation on both sides.
    """
    total = seconds(total_phase)
    if not total:
        return 0.0
    inner = sum(secs for name, (_, secs) in snapshot().items()
                if any(name.startswith(p) for p in inner_prefixes))
    return max(0.0, total - inner)
