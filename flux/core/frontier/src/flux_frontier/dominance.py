"""The single definition of N-objective Pareto dominance and the non-dominated set (D429).

The two-axis `frontier`/`spread` in this package are a sorted sweep; this is the general rule.
"""

from __future__ import annotations

from typing import Callable, Iterable, Sequence, TypeVar

T = TypeVar("T")

__all__ = ["dominates", "pareto"]


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
