"""What a cheap stage got WRONG, where a costly one settled it (D464).

The drawing's `calibrate (CI)` box: the slow evaluator corrects the fast one. Every stage in a
chain measures the same designs at a different fidelity, so wherever two stages measured the same
candidate the loop can say, from its own numbers, how far apart they were -- and that is the one
thing that tells an orchestrator which estimates to distrust and therefore which design is worth
spending the costly stage on next (D305 found this in the interconnect study, which computed it by
hand; the campaign path calibrates screening the same way, D222).

`bias(scored, fast=..., against=...)` is the whole computation: per metric, the ratio between
what the costly stage measured and what the cheap one predicted, over the candidates BOTH
measured, with its own spread and count. What comes back is a `Bias` per metric:

    the estimate stage reads fmax_mhz 1.18x the placement's, over 4 design(s) (spread 0.06)

Three rules keep it honest:

* it is computed from THIS pass's own measurements, never assumed;
* a correction never rewrites a recorded number -- a measurement stays what the tool said, and
  `Bias.apply` is offered to whoever wants to predict, which is an estimate and tagged as one;
* fewer than two shared candidates produces NOTHING rather than a ratio from one point, because
  a single pair cannot tell a systematic bias from a lucky design.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

__all__ = ["Bias", "bias"]


@dataclass(frozen=True)
class Bias:
    """How one stage's numbers relate to another's, for one metric."""

    metric: str
    stage: str                  # the cheap stage, the one being corrected
    against: str               # the costly stage that settled it
    ratio: float               # costly / cheap: multiply the cheap number by this
    spread: float              # the ratio's own standard deviation over the shared designs
    n: int                     # how many designs both stages measured

    def apply(self, value: float) -> float:
        """The cheap number, corrected. An ESTIMATE of what the costly stage would say -- never
        a measurement, and never written back over one."""
        return value * self.ratio

    def render(self) -> str:
        """Said the way round that cannot be misread: what the costly stage measured, against
        what the cheap one predicted."""
        return (f"the {self.against} stage measures {self.metric} {self.ratio:.3g}x what the "
                f"{self.stage} stage predicts, over {self.n} design(s) "
                f"(spread {self.spread:.2g})")


def bias(scored: Iterable[Any], *, fast: str, against: str,
         metrics: Iterable[str] = ()) -> list[Bias]:
    """One `Bias` per metric both stages measured on at least two shared candidates.

    `scored` is any iterable of `Scored` (the loop hands its whole pass). Candidates are paired
    by their key, so the same design measured on both stages is one pair however it was named.
    A zero or negative cheap number is skipped rather than made into an infinite ratio.
    """
    cheap: dict[str, dict[str, float]] = {}
    costly: dict[str, dict[str, float]] = {}
    for s in scored:
        into = cheap if s.stage == fast else costly if s.stage == against else None
        if into is None:
            continue
        into.setdefault(s.candidate.key(), {}).update(s.metrics)
    shared = sorted(set(cheap) & set(costly))
    if len(shared) < 2:
        return []
    wanted = list(metrics) or sorted({m for key in shared for m in costly[key]})
    out: list[Bias] = []
    for metric in wanted:
        ratios = []
        for key in shared:
            low, high = cheap[key].get(metric), costly[key].get(metric)
            if low is None or high is None or low <= 0:
                continue
            ratios.append(high / low)
        if len(ratios) < 2:
            continue
        mean = sum(ratios) / len(ratios)
        var = sum((r - mean) ** 2 for r in ratios) / (len(ratios) - 1)
        out.append(Bias(metric=metric, stage=fast, against=against, ratio=mean,
                        spread=var ** 0.5, n=len(ratios)))
    return out
