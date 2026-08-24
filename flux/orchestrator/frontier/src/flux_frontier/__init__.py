"""Two-objective bookkeeping shared by the studies (docs/decisions.md D362, D365).

A frontier is the set of points no other point beats on both axes. `spread` picks which of
them to confirm on an expensive rung; `best_within` applies a budget on the cost axis. Written
once here because two applications (prefetcher: speedup vs storage; MAC array: fmax vs area)
had the same three functions to write, and a frontier that is computed two ways is a frontier
that disagrees with itself somewhere.

`better` is the quality axis (higher is better), `cost` the cost axis (lower is better); both
are accessor callables so the points can be whatever the study measures.
"""

from __future__ import annotations

import math
from typing import Callable, TypeVar

T = TypeVar("T")


def frontier(points: list[T], *, better: Callable[[T], float],
             cost: Callable[[T], float]) -> list[T]:
    """The non-dominated points, cheapest first: each is better than everything cheaper.

    Ties on both axes keep the first seen, so two designs that coincide are one point.
    """
    ordered = sorted(points, key=lambda p: (cost(p), -better(p)))
    out: list[T] = []
    best = -math.inf
    for p in ordered:
        if better(p) > best:
            out.append(p)
            best = better(p)
    return out


def spread(front: list[T], count: int, *, keep: list[T] | tuple[T, ...] = (),
           cost: Callable[[T], float]) -> list[T]:
    """`count` points of a frontier worth confirming: both ends, then the widest gaps.

    Each further pick is the point farthest (in log cost) from every point already chosen, so
    the confirmation traces the curve rather than one corner. `keep` is always included.
    """
    chosen: list[T] = []
    for p in keep:                      # by equality, not hash: a point may carry a dict
        if p not in chosen:
            chosen.append(p)
    rest = [p for p in front if p not in chosen]
    for end in ((front[0], front[-1]) if front else ()):
        if len(chosen) < count and end in rest:
            chosen.append(end)
            rest.remove(end)

    def gap(p: T) -> float:
        return min(abs(math.log(max(1e-12, cost(p))) - math.log(max(1e-12, cost(c))))
                   for c in chosen) if chosen else 0.0

    while len(chosen) < count and rest:
        pick = max(rest, key=gap)
        chosen.append(pick)
        rest.remove(pick)
    return sorted(chosen, key=cost)


def best_within(points: list[T], budget: float | None, *, better: Callable[[T], float],
                cost: Callable[[T], float]) -> T | None:
    """The best point whose cost fits `budget`, or None. No budget: the best of all."""
    fit = [p for p in points if budget is None or cost(p) <= budget]
    return max(fit, key=better) if fit else None
