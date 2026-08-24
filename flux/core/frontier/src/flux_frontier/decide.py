"""The shared decision arithmetic: three moves every decider repeats.

Deciding stays per loop (each has its own metrics, wording and report); these are the moves
underneath:

* `corner`: an extreme of the frontier with deterministic tie-breaks -- equal on the
  primary cost goes to the next cost, never to iteration order.
* `knee_ranked`: the balanced pick as minimal equal-weight normalized distance to the
  ideal over all costs. Counting frontier rows instead would favour whichever extreme
  collects ties.
* `cheapest_meeting`: the cheapest point whose value makes the floor (within a tolerance,
  because a measured floor is noisier than a requested one, D366), else the best value as
  the fallback. Returns a rule tag, not a sentence: the wording belongs to each loop's report.

Everything here takes plain callables over the loop's own point type; nothing imports
a domain.
"""

from __future__ import annotations

from typing import Callable, Iterable, Sequence, TypeVar

T = TypeVar("T")
Cost = Callable[["T"], float]        # lower is better, by convention; negate to maximise

__all__ = ["cheapest_meeting", "corner", "knee_ranked", "normalizer"]


def corner(points: Iterable[T], *costs: Cost) -> T | None:
    """The point minimising the first cost, ties broken by the next cost in turn."""
    pts = list(points)
    if not pts or not costs:
        return pts[0] if pts else None
    return min(pts, key=lambda p: tuple(c(p) for c in costs))


def normalizer(values: Sequence[float]) -> Callable[[float], float]:
    """Map this cost's observed range onto [0, 1]; a degenerate spread contributes 0,
    so a cost every candidate ties on cannot tip the knee."""
    lo, hi = min(values), max(values)
    if hi <= lo:
        return lambda _v: 0.0
    return lambda v: (v - lo) / (hi - lo)


def knee_ranked(points: Iterable[T], costs: Sequence[Cost]) -> list[T]:
    """All points, best-balanced first: minimal equal-weight normalized distance to the
    ideal over `costs`. Stable, so equally balanced points keep the caller's order."""
    pts = list(points)
    if not pts:
        return []
    norms = [normalizer([c(p) for p in pts]) for c in costs]
    return sorted(pts, key=lambda p: sum(n(c(p)) for n, c in zip(norms, costs)))


def cheapest_meeting(points: Iterable[T], *, cost: Cost, value: Cost,
                     floor: float | None, tolerance: float = 0.0
                     ) -> tuple[T | None, str]:
    """The cheapest point whose `value` makes `floor` (within `tolerance`, a fraction);
    the rule tag says which rule actually applied so the report can word it:

    * "cheapest-meeting": a point makes the floor; the cheapest of those is returned.
    * "fallback-best-value": nothing makes the floor; the best value is returned.
    * "best-value": no floor was set; the best value is returned.
    * "nothing": no points.
    """
    pts = list(points)
    if not pts:
        return None, "nothing"
    if floor is None:
        return max(pts, key=value), "best-value"
    fit = [p for p in pts if value(p) >= floor * (1.0 - tolerance)]
    if fit:
        return min(fit, key=cost), "cheapest-meeting"
    return max(pts, key=value), "fallback-best-value"
