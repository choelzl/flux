"""What a measurement means, and what the study refuses to conclude from a partial one.

The dangerous case here is not arithmetic, it is the missing trace. `score_ipc.py` prints MISSING
and scores whatever ran, which is right for a human reading a table and wrong for a search: a
configuration that crashed the simulator on the hardest of three traces would be scored on the two
it survived, and would then look like the best design in the run.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

FLUX_ROOT = Path(__file__).resolve().parents[2]

from flux_prefetcher.objective import (  # noqa: E402
    BENCHMARKS, RETENTION_FLOOR, Baseline, IncompleteMeasurement, geomean,
    retention_threshold, score, stage2_admissible,
)

BASE = Baseline(ipc={"fdd_su_v1_0": 0.69602, "tdd_dl_mu_v1_0": 0.99071,
                     "tdd_ul_mu_v1_0": 0.80232})


def test_geomean_is_multiplicative_not_arithmetic():
    """Speedups are ratios: 2x then 0.5x is no change, which the arithmetic mean gets wrong."""
    assert geomean([2.0, 0.5]) == pytest.approx(1.0)
    assert geomean([1.0, 1.0, 1.0]) == pytest.approx(1.0)
    assert geomean([1.1, 1.2, 1.3]) == pytest.approx(math.exp(
        (math.log(1.1) + math.log(1.2) + math.log(1.3)) / 3))


def test_score_reproduces_the_projects_own_speedup_arithmetic():
    measured = {"fdd_su_v1_0": 0.75, "tdd_dl_mu_v1_0": 1.05, "tdd_ul_mu_v1_0": 0.86}
    got = score(measured, BASE, storage=35096)
    for bench in BENCHMARKS:
        assert got.speedups[bench] == pytest.approx(measured[bench] / BASE.ipc[bench])
    assert got.geomean_speedup == pytest.approx(geomean(list(got.speedups.values())))
    assert got.storage_bytes == 35096


def test_a_missing_trace_refuses_to_score_rather_than_averaging_the_rest():
    """The failure mode this module exists to prevent."""
    partial = {"fdd_su_v1_0": 0.75, "tdd_dl_mu_v1_0": 1.05}      # third trace crashed
    with pytest.raises(IncompleteMeasurement) as caught:
        score(partial, BASE, storage=35096)
    assert "tdd_ul_mu_v1_0" in str(caught.value)


def test_a_missing_baseline_refuses_too():
    with pytest.raises(IncompleteMeasurement):
        score({b: 1.0 for b in BENCHMARKS}, Baseline(ipc={"fdd_su_v1_0": 0.69602}), storage=1)


def test_zero_or_negative_ipc_is_not_a_speedup():
    """A simulator that reports 0 IPC did not run; the geomean must not swallow it as 'slow'."""
    with pytest.raises(IncompleteMeasurement):
        geomean([1.2, 0.0])
    with pytest.raises(IncompleteMeasurement):
        geomean([])


def test_the_floor_is_a_fraction_of_the_GAIN_not_of_the_ratio():
    """The distinction that decides whether stage 2 means anything.

    The first end-to-end run of this study read "90% of the speedup" as 90% of the RATIO. Stage 1
    won at 1.0620x, so the floor came out at 0.9558x, and stage 2 duly returned region_size=64 /
    pattern_len=1 — a prefetcher that does nothing, geomean exactly 1.0000 on every trace, and the
    smallest configuration by construction. It was correct under the rule it was given.
    """
    assert retention_threshold(1.0620, 0.90) == pytest.approx(1.0558)
    assert retention_threshold(1.20, 0.90) == pytest.approx(1.18)
    # Not the other reading, which is what produced the do-nothing answer:
    assert retention_threshold(1.0620, 0.90) != pytest.approx(0.90 * 1.0620)


def test_a_prefetcher_that_does_nothing_is_refused():
    """The regression. 1.0000x is not '94% as good as 1.0620x', it is no speedup at all."""
    does_nothing = score(dict(BASE.ipc), BASE, storage=1)        # measured IPC == baseline IPC
    assert does_nothing.geomean_speedup == pytest.approx(1.0)
    assert stage2_admissible(does_nothing, 1.0620) is False
    # And it stays refused however small it is: size never buys the floor.
    assert does_nothing.storage_bytes == 1


def test_the_retention_floor_is_a_constraint_not_a_preference():
    """Stage 2 rejects below the floor; it does not rank it lower."""
    best = 1.20                                                   # threshold 1.18
    just_over = score({b: BASE.ipc[b] * 1.19 for b in BENCHMARKS}, BASE, storage=100)
    just_under = score({b: BASE.ipc[b] * 1.17 for b in BENCHMARKS}, BASE, storage=10)
    assert stage2_admissible(just_over, best) is True
    assert stage2_admissible(just_under, best) is False
    # The refused one is smaller by 10x and is STILL not admissible.
    assert just_under.storage_bytes < just_over.storage_bytes


def test_retention_floor_default_matches_the_projects_stated_requirement():
    """proj/README.md stage 2: 'while still achieving 90% of the original speedup'."""
    assert RETENTION_FLOOR == 0.90


def test_met_requirement_compares_against_the_incumbent_not_no_prefetcher():
    """The question is whether tuning was worth doing, and it has a `no` answer.

    The first version asked `geomean_speedup > 1.0` — beat NO PREFETCHER — which Bingo clears in
    almost any legal configuration. A run that searched hard and handed back the shipped
    `bingo.ini` unchanged reported "met requirement: yes", and its stage 1 winner was labelled
    `proposed by incumbent` right above it.
    """
    from flux_prefetcher.study import PrefetcherResult

    shipped = score({b: BASE.ipc[b] * 1.0440 for b in BENCHMARKS}, BASE, storage=35096)
    better = score({b: BASE.ipc[b] * 1.0542 for b in BENCHMARKS}, BASE, storage=37776)

    found_nothing = PrefetcherResult(decision_score=shipped, incumbent_score=shipped)
    assert found_nothing.met_requirement is False, "returning the incumbent is not an improvement"

    improved = PrefetcherResult(decision_score=better, incumbent_score=shipped)
    assert improved.met_requirement is True


def test_without_an_incumbent_measurement_nothing_is_claimed():
    """No reference on this rung means the question is unanswered, not answered `yes`."""
    from flux_prefetcher.study import PrefetcherResult

    only_decision = PrefetcherResult(
        decision_score=score({b: BASE.ipc[b] * 1.20 for b in BENCHMARKS}, BASE, storage=1))
    assert only_decision.met_requirement is False


def test_a_worse_decision_than_the_incumbent_is_not_an_improvement():
    """Stage 2 trades speed for area, so the decision can legitimately be slower than the
    incumbent — and that must not read as a win."""
    from flux_prefetcher.study import PrefetcherResult

    shipped = score({b: BASE.ipc[b] * 1.0607 for b in BENCHMARKS}, BASE, storage=35096)
    smaller = score({b: BASE.ipc[b] * 1.0600 for b in BENCHMARKS}, BASE, storage=34856)
    assert PrefetcherResult(decision_score=smaller, incumbent_score=shipped).met_requirement is False
