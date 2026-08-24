"""One definition of Pareto dominance and of the non-dominated set (D429).

Four copies of "no worse everywhere, better somewhere" had grown across the tree -- the
Pareto-UCT policy (maximise form), the interconnect annealer (minimise form), the mapping
study's four-objective front and the campaign's objective-oriented `point_dominates` --
and a frontier computed two ways is a frontier that disagrees with itself somewhere. The
two-axis `frontier`/`spread` in this package stay as they are (they are the sorted sweep
every study reports); this module is the N-objective rule they all share.
"""

from __future__ import annotations

from typing import Callable, Iterable, Sequence, TypeVar

T = TypeVar("T")

__all__ = ["dominates", "interval_dominates", "overlaps", "pareto"]


def dominates(a: Sequence[float], b: Sequence[float], *, minimize: bool = True) -> bool:
    """`a` beats `b`: no worse on every objective and strictly better on at least one.
    Minimise form by default (costs); `minimize=False` reads the tuples as gains."""
    if len(a) != len(b):
        raise ValueError(f"dominance needs equal lengths, got {len(a)} and {len(b)}")
    if minimize:
        return all(x <= y for x, y in zip(a, b)) and any(x < y for x, y in zip(a, b))
    return all(x >= y for x, y in zip(a, b)) and any(x > y for x, y in zip(a, b))


def pareto(points: Iterable[T], *, key: Callable[[T], Sequence[float]],
           minimize: bool = True) -> list[T]:
    """The points no other point dominates, in input order. `key` maps a point to its
    objective tuple. Coincident points do not dominate each other, so all of them stay:
    a frontier that dropped one of two equal designs would be choosing for the caller."""
    items = list(points)
    keys = [tuple(key(p)) for p in items]
    return [p for i, p in enumerate(items)
            if not any(j != i and dominates(keys[j], keys[i], minimize=minimize)
                       for j in range(len(items)))]


def overlaps(a: Sequence[float], b: Sequence[float]) -> bool:
    """Closed-interval overlap of `(lo, hi)` pairs: neither strictly above nor strictly below
    the other. Touching intervals overlap (D105's `<=` test)."""
    return a[0] <= b[1] and b[0] <= a[1]


def interval_dominates(a: Sequence[Sequence[float]], b: Sequence[Sequence[float]]) -> bool:
    """`a` rules `b` out under uncertainty, minimise form, each objective a `(lo, value, hi)`
    triple (D218, one rule since D439): interval-better on at least one objective (`a.hi <
    b.lo`), interval-worse on none (`a.lo > b.hi`), and point-worse on none -- the last clause
    is what keeps elimination safe under overlap: an interval can strictly beat another on one
    objective while its point value quietly loses on another inside overlapping intervals."""
    if len(a) != len(b):
        raise ValueError(f"interval dominance needs equal lengths, got {len(a)} and {len(b)}")
    better = False
    for (a_lo, a_v, a_hi), (b_lo, b_v, b_hi) in zip(a, b):
        if a_lo > b_hi or a_v > b_v:
            return False
        if a_hi < b_lo:
            better = True
    return better
