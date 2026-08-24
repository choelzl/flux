"""Cutoffs: which candidates are worth the next, more expensive stage (D454).

* an absolute floor or budget (`above`, `below`): a requirement the answer must meet.
* a band relative to this run's own leader (`within_best`), when the achievable range is only
  known after measuring (D349).

Each returns `(survivors, why)` so the rule in words travels with the cut.
"""

from __future__ import annotations

from typing import Callable

from .types import Scored

__all__ = ["above", "below", "within_best"]


def above(scored: list[Scored], metric: str, at: float, *, unit: str = "") -> tuple[list[Scored], str]:
    """Keep candidates whose `metric` is at or above `at`: a floor the answer must clear."""
    kept = [s for s in scored if s.metrics.get(metric, float("-inf")) >= at]
    return kept, f"{metric} below {at:g}{unit}"


def below(scored: list[Scored], metric: str, at: float, *, unit: str = "") -> tuple[list[Scored], str]:
    """Keep candidates whose `metric` is at or below `at`: a budget the answer must fit."""
    kept = [s for s in scored if s.metrics.get(metric, float("inf")) <= at]
    return kept, f"{metric} above {at:g}{unit}"


def within_best(scored: list[Scored], metric: str, fraction: float, *,
                higher_is_better: bool = True) -> tuple[list[Scored], str]:
    """Keep candidates within `fraction` of this run's own best `metric`.

    `fraction=0.9` with a higher-is-better metric keeps everything at or above 90% of the
    leader; with a lower-is-better one (area, storage, cost) it keeps everything at or below
    the leader divided by 0.9.
    """
    values = [v for v in (s.metrics.get(metric) for s in scored) if v is not None]
    if not values:
        return list(scored), ""
    if higher_is_better:
        best = max(values)
        threshold = best * fraction if best >= 0 else best / max(fraction, 1e-9)
        kept = [s for s in scored if s.metrics.get(metric, float("-inf")) >= threshold]
        return kept, (f"{metric} below {fraction:.0%} of this run's best "
                      f"({threshold:g} of {best:g})")
    best = min(values)
    threshold = best / fraction if best >= 0 else best * fraction
    kept = [s for s in scored if s.metrics.get(metric, float("inf")) <= threshold]
    return kept, (f"{metric} worse than {threshold:g}, this run's best {best:g} over "
                  f"{fraction:.0%}")


#: A cutoff as a problem writes it: the stage's results in, the survivors and the rule out.
Cutoff = Callable[[list[Scored]], "tuple[list[Scored], str]"]
