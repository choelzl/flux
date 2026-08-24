"""Storage is the second axis of the prefetcher study, not a tie-breaker (docs/decisions.md D362).

A run confirmed bingo+sms+invented2 at 1.0671 for 206 KB; the next confirmed bingo+invented2 at
1.0626 for 97 KB. Both are honest answers, and the study cannot say which to build -- what 109 KB
of SRAM is worth is the reader's judgement. What it CAN do is lay the trade-off out on one rung,
spend its confirmation budget along it rather than at one corner, and, when told a budget, spend
the search inside it.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

FLUX_ROOT = Path(__file__).resolve().parents[2]

from flux_prefetcher.config import DEFAULT, BingoConfig  # noqa: E402
from flux_prefetcher.objective import (  # noqa: E402
    BENCHMARKS, Baseline, best_within, frontier, score, spread,
)
from flux_prefetcher.study import ScoredConfig  # noqa: E402

BASE = Baseline(ipc={b: 1.0 for b in BENCHMARKS})


def _pt(g: float, storage: int, who: str = "x", cfg: BingoConfig = DEFAULT,
        types=("bingo",)) -> ScoredConfig:
    return ScoredConfig(config=cfg, score=score({b: g for b in BENCHMARKS}, BASE, storage),
                        provenance=who, types=types)


# ---- the frontier -------------------------------------------------------------------------

def test_the_frontier_is_every_point_faster_than_everything_smaller():
    pts = [_pt(1.0439, 35_096, "incumbent"), _pt(1.0626, 97_208, "b"),
           _pt(1.0671, 206_496, "c"),
           _pt(1.0600, 150_000, "dominated: slower than b and bigger"),
           _pt(1.0620, 97_208, "dominated: same size as b, slower")]
    front = frontier(pts)
    assert [p.provenance for p in front] == ["incumbent", "b", "c"]
    assert [p.storage_bytes for p in front] == sorted(p.storage_bytes for p in front)


def test_a_bigger_design_that_is_not_faster_is_dominated():
    pts = [_pt(1.05, 100, "small"), _pt(1.05, 200, "same speed, twice the size")]
    assert [p.provenance for p in frontier(pts)] == ["small"]


def test_spread_confirms_both_ends_then_the_widest_gaps():
    front = [_pt(1.04, 32_000, "a"), _pt(1.05, 40_000, "b"), _pt(1.06, 100_000, "c"),
             _pt(1.065, 400_000, "d"), _pt(1.067, 800_000, "e")]
    picked = [p.provenance for p in spread(front, 3)]
    assert picked[0] == "a" and picked[-1] == "e", "the ends bracket the trade-off"
    assert picked[1] == "c", "the middle pick is the point farthest in log storage from both ends"


def test_spread_always_keeps_the_decision():
    front = [_pt(1.04, 32_000, "a"), _pt(1.05, 40_000, "decision"), _pt(1.06, 100_000, "c"),
             _pt(1.067, 800_000, "e")]
    picked = spread(front, 2, keep=[front[1]])
    assert front[1] in picked and len(picked) == 2


def test_spread_asks_for_more_than_exist_returns_what_exists():
    front = [_pt(1.04, 32_000, "a"), _pt(1.05, 40_000, "b")]
    assert len(spread(front, 6)) == 2


def test_best_within_a_budget_is_the_fastest_that_fits():
    pts = [_pt(1.0439, 35_096, "incumbent"), _pt(1.0626, 97_208, "b"), _pt(1.0671, 206_496, "c")]
    assert best_within(pts, 100_000).provenance == "b"
    assert best_within(pts, None).provenance == "c"
    assert best_within(pts, 1_000) is None


# ---- where the budget bites ---------------------------------------------------------------

def test_an_over_budget_design_is_refused_before_any_simulation():
    from flux_prefetcher.flow import Measurer, _score_designs
    from flux_prefetcher.config import storage_bytes

    waves = []

    def spy(jobs, parallelism):
        waves.append(len(jobs))
        return [{"ipc": 1.05, "cycles": 1.0, "instructions": 1.0, "wall_clock_s": 0.1}
                for _ in jobs]

    big = replace(DEFAULT, pht_size=65536, pht_ways=32)
    assert storage_bytes(big) > storage_bytes(DEFAULT)
    traces = {b: Path(f"/nonexistent/{b}") for b in BENCHMARKS}
    refused: list[tuple[str, str]] = []
    m = Measurer(None, spy, warmup=1, simulation=1, parallelism=8)
    got = _score_designs([(DEFAULT, ("bingo",), {}), (big, ("bingo",), {})], traces, BASE, m,
                         "test", refused, max_storage=storage_bytes(DEFAULT))
    assert [s.config for s in got] == [DEFAULT]
    assert waves == [3], "only the design within budget was simulated"
    assert refused and "over the storage budget" in refused[0][1]


def test_finalists_are_spread_along_the_frontier_not_the_top_by_speedup():
    from flux_prefetcher.flow import _finalists

    def cfg(pht):
        return replace(DEFAULT, pht_size=pht)

    small = _pt(1.050, 40_000, "shrink", cfg(1024))
    mid = _pt(1.060, 100_000, "climb", cfg(2048))
    big = _pt(1.067, 800_000, "llm", cfg(65536))
    also_big = _pt(1.066, 790_000, "llm", cfg(32768))      # second-fastest, but dominated
    incumbent = _pt(1.0439, 35_096, "incumbent")
    finalists = _finalists([small, mid, big, also_big, incumbent], small, 3)
    names = [f.provenance for f in finalists]
    assert "incumbent" in names
    assert names.count("llm") == 1, "the dominated near-duplicate is not confirmed"
    assert "shrink" in names and "climb" in names, "the trade-off's other points are"
    assert finalists[0] is big, "fastest first: the mis-rank lesson keys on finalists[0]"


def test_finalists_under_a_budget_come_from_inside_it():
    from flux_prefetcher.flow import _finalists

    def cfg(pht):
        return replace(DEFAULT, pht_size=pht)

    small = _pt(1.050, 40_000, "shrink", cfg(1024))
    big = _pt(1.067, 800_000, "llm", cfg(65536))
    finalists = _finalists([small, big], small, 2, max_storage=100_000)
    assert big not in finalists


def test_the_proposer_is_told_the_budget():
    from flux_prefetcher.propose import build_prompt

    text = build_prompt({b: 1.0 for b in BENCHMARKS}, 4, max_storage=98_304)
    assert "98,304 bytes" in text and "HARD BUDGET" in text
    assert "HARD BUDGET" not in build_prompt({b: 1.0 for b in BENCHMARKS}, 4)
