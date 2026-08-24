"""What a measurement MEANS: geomean speedup, and the two stages the project actually asks for.

Kept apart from the evaluator on purpose. `flux_evaluator_champsim_bingo` reports what ChampSim
said about one trace. Nothing in that number is an objective: a speedup needs a no-prefetcher
baseline, and the baseline is a property of this study (which traces, which instruction counts,
which binary), not of the tool. Putting the geomean here means the evaluator stays reusable for
any prefetcher question, and the definition of "better" lives in one readable place.

THE TWO STAGES (proj/README.md, carried over verbatim in intent):

  1. maximise geomean IPC speedup over the no-prefetcher baseline
  2. minimise hardware storage while keeping at least 90% of stage 1's speedup

Stage 2 is not "stage 1 with a different objective". It is constrained: a configuration that is
smaller but drops below the retention floor is not a worse point on a frontier, it is a REJECTED
point, and reporting it as a trade-off would misrepresent the requirement.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

#: The three traces the study scores, in the order `score_ipc.py` lists them.
BENCHMARKS = ("fdd_su_v1_0", "tdd_dl_mu_v1_0", "tdd_ul_mu_v1_0")

#: Stage 2's floor: keep at least this fraction of stage 1's best geomean speedup.
RETENTION_FLOOR = 0.90


class IncompleteMeasurement(ValueError):
    """A geomean was asked for over traces that were not all measured.

    Raised rather than quietly averaging what is present. `score_ipc.py` prints "MISSING" and
    scores the rest, which is right for a human watching a run and wrong for a search: a
    configuration that crashed on the hardest trace would otherwise score as the best one, on the
    strength of the two traces it survived.
    """


def geomean(values: list[float]) -> float:
    """Geometric mean. Speedups are ratios, so they compose multiplicatively, not additively."""
    if not values:
        raise IncompleteMeasurement("geomean of no values")
    if any(v <= 0 for v in values):
        raise IncompleteMeasurement(f"geomean needs positive values, got {values}")
    return math.exp(sum(math.log(v) for v in values) / len(values))


def retention_threshold(best_geomean: float, floor: float = RETENTION_FLOOR) -> float:
    """The geomean a stage-2 candidate must reach: `floor` of the GAIN, not of the ratio.

    This distinction decides whether stage 2 means anything. Speedups here are ratios near 1.0, so
    90% of the RATIO of a 1.062x winner is 0.956x -- which a prefetcher that does nothing at all
    (exactly 1.000x, every trace) clears comfortably. Read that way, stage 2's answer is "switch
    the prefetcher off" whenever stage 1 gains less than 11%, and it is the smallest configuration
    by construction. The first end-to-end run of this study returned exactly that: region_size=64,
    pattern_len=1, geomean 1.0000, and it was correct under the rule it was given.

    "Achieving 90% of the original speedup" means keeping 90% of the IMPROVEMENT. A 1.062x winner
    admits candidates at or above 1.056x, and a prefetcher that does nothing is refused.
    """
    return 1.0 + floor * (best_geomean - 1.0)


@dataclass(frozen=True)
class Baseline:
    """No-prefetcher IPC per trace: the denominator of every speedup this study reports."""

    ipc: dict[str, float] = field(default_factory=dict)

    def require(self, benchmarks: tuple[str, ...] = BENCHMARKS) -> None:
        missing = [b for b in benchmarks if b not in self.ipc]
        if missing:
            raise IncompleteMeasurement(f"no baseline IPC for {missing}")


@dataclass(frozen=True)
class Score:
    """One configuration's full standing: per-trace speedups, their geomean, and its cost."""

    speedups: dict[str, float]
    geomean_speedup: float
    storage_bytes: int

    def retains(self, best_geomean: float, floor: float = RETENTION_FLOOR) -> bool:
        """Does this hold enough of `best_geomean` to be admissible in stage 2?"""
        return self.geomean_speedup >= retention_threshold(best_geomean, floor)


def score(measured_ipc: dict[str, float], baseline: Baseline, storage: int,
          benchmarks: tuple[str, ...] = BENCHMARKS) -> Score:
    """Turn per-trace IPC into a `Score`, or refuse if any trace is missing."""
    baseline.require(benchmarks)
    missing = [b for b in benchmarks if b not in measured_ipc]
    if missing:
        raise IncompleteMeasurement(
            f"cannot score: no IPC for {missing}. A partial geomean would rank a configuration "
            "that failed on one trace above one that ran on all three.")
    speedups = {b: measured_ipc[b] / baseline.ipc[b] for b in benchmarks}
    return Score(speedups=speedups,
                 geomean_speedup=geomean(list(speedups.values())),
                 storage_bytes=storage)


def stage2_admissible(candidate: Score, stage1_best: float,
                      floor: float = RETENTION_FLOOR) -> bool:
    """Stage 2's constraint, named so a caller cannot accidentally treat it as a soft preference."""
    return candidate.retains(stage1_best, floor)


__all__ = [
    "BENCHMARKS", "RETENTION_FLOOR", "Baseline", "IncompleteMeasurement", "Score", "geomean",
    "retention_threshold", "score", "stage2_admissible",
]


# ---- the second axis --------------------------------------------------------------------------
# Storage is not a tie-breaker applied after speedup has been maximised; it is the other
# coordinate of every design. A 206 KB configuration at 1.0671 and a 97 KB one at 1.0626 are two
# points of one trade-off, and which to build is a judgement about what 109 KB of SRAM is worth
# -- a judgement the study cannot make, but must lay out (D362).

def frontier(points, *, speedup=lambda p: p.geomean_speedup,
             storage=lambda p: p.storage_bytes) -> list:
    """The non-dominated points, smallest first: each one is faster than everything smaller."""
    from flux_frontier import frontier as _frontier

    return _frontier(list(points), better=speedup, cost=storage)


def spread(front: list, count: int, *, keep=(), storage=lambda p: p.storage_bytes) -> list:
    """`count` points of a frontier worth confirming: both ends first, then the widest gaps in
    log storage; `keep` (the decision) is always included. See `flux_frontier`."""
    from flux_frontier import spread as _spread

    return _spread(list(front), count, keep=list(keep), cost=storage)


def best_within(points, budget: int | None, *, speedup=lambda p: p.geomean_speedup,
                storage=lambda p: p.storage_bytes):
    """The fastest point that fits `budget` bytes, or None. No budget: the fastest of all."""
    from flux_frontier import best_within as _best_within

    return _best_within(list(points), budget, better=speedup, cost=storage)
