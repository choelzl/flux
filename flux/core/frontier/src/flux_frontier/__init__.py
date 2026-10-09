"""Frontier package: two-objective bookkeeping, N-objective dominance, the UCT tree policy,
and the decision arithmetic (`decide`).

A frontier is the set of points no other point beats on both axes. `spread` picks which of
them to confirm on an expensive stage.

`better` is the quality axis (higher is better), `cost` the cost axis (lower is better); both
are accessor callables so the points can be whatever the study measures.
"""

from __future__ import annotations

import math
from typing import Callable, TypeVar

from . import pareto_uct  # noqa: F401  (the tree policy, reachable from the package)
from .decide import cheapest_meeting, corner, knee_ranked, normalizer  # noqa: F401
from .dominance import dominates, pareto  # noqa: F401
from .pareto_uct import ParetoUCT, hypervolume  # noqa: F401

T = TypeVar("T")

__all__ = ["ParetoUCT", "cheapest_meeting", "corner", "dominates", "frontier",
           "hypervolume", "knee_ranked", "normalizer", "pareto",
           "pareto_uct", "spread"]


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
