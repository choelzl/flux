"""A wave costs what its slowest simulation costs, so what shares a wave is what matters.

`Measurer.ipc` took the partner knobs as ONE argument for a whole batch, so candidates differing
in their stack or their partners' settings could not be measured together. `compose` therefore
issued six waves of three simulations at a parallelism of eighteen — roughly forty-five seconds
doing seven seconds of work, and the same again in `tune-partners`. Measured end to end, fixing it
took an identical run from 268s to 69s.

These tests assert the SHAPE of the dispatch rather than the timing: how many waves, and how wide.
"""

from __future__ import annotations

from pathlib import Path

FLUX_ROOT = Path(__file__).resolve().parents[2]

from flux_prefetcher.config import DEFAULT  # noqa: E402
from flux_prefetcher.flow import Measurer, _score_designs  # noqa: E402
from flux_prefetcher.objective import BENCHMARKS, Baseline  # noqa: E402

BASE = Baseline(ipc={b: 1.0 for b in BENCHMARKS})
TRACES = {b: Path(f"/nonexistent/{b}.gz") for b in BENCHMARKS}


class SpyBackend:
    """Records each wave's width without running anything."""

    def __init__(self):
        self.waves: list[int] = []

    def __call__(self, jobs, parallelism):
        self.waves.append(len(jobs))
        return [{"ipc": 1.05, "cycles": 1.0, "instructions": 1.0, "wall_clock_s": 0.1}
                for _ in jobs]


def _measurer(spy):
    return Measurer(None, spy, warmup=1, simulation=1, parallelism=18)


def test_designs_with_different_stacks_share_one_wave():
    """The compose case: six partners, one wave of eighteen, not six waves of three."""
    spy = SpyBackend()
    designs = [(DEFAULT, ("bingo", p), {}) for p in
               ("sms", "ampm", "stride", "streamer", "spp_ppf_dev", "power7")]
    scored = _score_designs(designs, TRACES, BASE, _measurer(spy), "compose", [])
    assert len(scored) == 6
    assert spy.waves == [18], f"expected one 18-wide wave, got {spy.waves}"


def test_designs_with_different_partner_knobs_share_one_wave():
    """The tune case: same stack, different knobs — the other half of the batch-wide problem."""
    spy = SpyBackend()
    designs = [(DEFAULT, ("bingo", "sms"), {"sms_pref_degree": d}) for d in (1, 2, 4, 8, 16, 32)]
    scored = _score_designs(designs, TRACES, BASE, _measurer(spy), "tune-partner", [])
    assert len(scored) == 6
    assert spy.waves == [18], f"expected one 18-wide wave, got {spy.waves}"


def test_each_design_keeps_its_own_stack_and_knobs():
    """Batching must not blur identity: every result carries what IT was measured with."""
    spy = SpyBackend()
    designs = [(DEFAULT, ("bingo",), {}),
               (DEFAULT, ("bingo", "sms"), {"sms_pref_degree": 8}),
               (DEFAULT, ("bingo", "stride"), {"stride_pref_degree": 4})]
    scored = _score_designs(designs, TRACES, BASE, _measurer(spy), "compose", [])
    assert [s.types for s in scored] == [("bingo",), ("bingo", "sms"), ("bingo", "stride")]
    assert dict(scored[1].partner_knobs) == {"sms_pref_degree": 8}
    assert dict(scored[2].partner_knobs) == {"stride_pref_degree": 4}
    assert dict(scored[0].partner_knobs) == {}


def test_the_jobs_carry_per_job_knobs_not_a_batch_wide_value():
    """The regression itself: one dict for the whole wave would collapse these into one design."""
    spy = SpyBackend()
    designs = [(DEFAULT, ("bingo", "sms"), {"sms_pref_degree": 1}),
               (DEFAULT, ("bingo", "sms"), {"sms_pref_degree": 16})]

    seen = []

    def capture(jobs, parallelism):
        seen.extend(j["partner_knobs"] for j in jobs)
        return spy(jobs, parallelism)

    _score_designs(designs, TRACES, BASE, _measurer(capture), "tune-partner", [])
    degrees = {k["sms_pref_degree"] for k in seen}
    assert degrees == {1, 16}, f"jobs shared one knob dict: {degrees}"


def test_a_failed_design_refuses_without_taking_the_wave_with_it():
    """One crashed candidate must not discard the seventeen that ran beside it."""
    def half_broken(jobs, parallelism):
        return [{"error": "SimulationFailedError: boom"} if i < len(BENCHMARKS)
                else {"ipc": 1.05, "cycles": 1.0, "instructions": 1.0, "wall_clock_s": 0.1}
                for i in range(len(jobs))]

    refused: list[tuple[str, str]] = []
    designs = [(DEFAULT, ("bingo", p), {}) for p in ("scooby", "sms", "ampm")]
    scored = _score_designs(designs, TRACES, BASE, _measurer(half_broken), "compose", refused)
    assert len(scored) == 2, "the surviving designs must still be scored"
    assert len(refused) == 1 and "scooby" in refused[0][0]


def test_a_scorer_bound_to_a_rung_divides_by_that_rungs_baseline():
    """The confirm phase once divided full-length IPCs by the SCREEN baseline.

    Every confirmed number came out 1.3% high -- the geomean of the two baselines' ratio --
    including the shipped default it was compared with, so the deltas looked right and the
    absolutes were fiction. `Study.scorer` takes the baseline explicitly now; this pins that a
    scorer handed a baseline uses it and not the one lying in the context.
    """
    import random

    from flux_prefetcher.flow import Study
    from flux_prefetcher.study import PrefetcherRequest

    screen_base = Baseline(ipc={b: 0.5 for b in BENCHMARKS})
    full_base = Baseline(ipc={b: 1.0 for b in BENCHMARKS})
    s = Study(request=PrefetcherRequest(db="x"), say=lambda _m: None, started=0.0,
              rng=random.Random(0))
    spy = SpyBackend()                                     # returns ipc 1.05 for everything
    s.screen = s.decide = _measurer(spy)
    s.traces = TRACES
    s.baseline = screen_base                               # what the context still holds
    got = s.scorer(s.decide, baseline=full_base)([(DEFAULT, ("bingo",), {})], "confirmed")
    assert abs(got[0].geomean_speedup - 1.05) < 1e-9, "must divide by the FULL baseline (1.0)"
    wrong = s.scorer(s.decide)([(DEFAULT, ("bingo",), {})], "screen")
    assert abs(wrong[0].geomean_speedup - 2.10) < 1e-9, "without one, the context's baseline"


def test_the_job_carries_the_binary_the_rung_was_built_for():
    """A backend that resolves its own binary ran invented partners on the stock simulator.

    The flow had just built a simulator with the inventions installed; the CHIA node's measure
    closure passed `binary=None`, the stock `pythia` on PATH ran instead, and every invented
    partner "exited 1" because that binary had never heard of them. The binary is part of the
    job now, and a backend uses it in preference to anything it would resolve itself.
    """
    seen = []

    def capture(jobs, parallelism):
        seen.extend(j.get("binary") for j in jobs)
        return [{"ipc": 1.0, "cycles": 1.0, "instructions": 1.0, "wall_clock_s": 0.1}
                for _ in jobs]

    m = Measurer(None, capture, warmup=1, simulation=1, parallelism=4,
                 binary="/nix/store/xyz-pythia-invented/bin/pythia")
    _score_designs([(DEFAULT, ("bingo", "invented4"), {})], TRACES, BASE, m, "compose", [])
    assert seen and all(b == "/nix/store/xyz-pythia-invented/bin/pythia" for b in seen)


# ---- what a measurement's identity is keyed on ------------------------------------------------
# The same stack measured on the stock simulator and on two rebuilds with different invention
# libraries installed agreed to the last digit: twelve identities over three binaries in one
# campaign's cache. Keying the cache on the binary threw all of it away every time the library
# changed -- a design added or filtered out -- including the no-prefetcher baseline (D361).

def test_a_stock_stack_has_the_same_identity_whatever_else_is_installed():
    from flux_prefetcher.measure import _identity

    plain = _identity(DEFAULT, "t", ["bingo", "sms"], 1, 2, {"sms_pref_degree": 4})
    beside = _identity(DEFAULT, "t", ["bingo", "sms"], 1, 2, {"sms_pref_degree": 4},
                       sources={"invented2": "abc", "invented4": "def"})
    assert plain == beside


def test_an_invented_prefetcher_in_the_stack_enters_by_its_header_digest():
    from flux_prefetcher.measure import _identity

    v1 = _identity(DEFAULT, "t", ["bingo", "invented2"], 1, 2, {}, sources={"invented2": "abc"})
    v2 = _identity(DEFAULT, "t", ["bingo", "invented2"], 1, 2, {}, sources={"invented2": "xyz"})
    assert v1 != v2, "a rewritten header is a different prefetcher"
    assert "invented2@abc" in v1
    stock = _identity(DEFAULT, "t", ["bingo", "invented2"], 1, 2, {})
    assert stock != v1, "an unknown source is not the same as a known one"


def test_a_measurer_serves_a_stock_stack_across_rebuilds(tmp_path):
    """Two measurers, two binaries, one cache namespace: the second run measures nothing."""
    from flux_cache import MeasurementCache

    cache = MeasurementCache(tmp_path / "x.db", {"champsim": "stock@1"}, suffix="c.json")
    first, second = SpyBackend(), SpyBackend()
    a = Measurer(cache, first, warmup=1, simulation=1, parallelism=4,
                 binary="/build/one", sources={"invented2": "abc"})
    b = Measurer(cache, second, warmup=1, simulation=1, parallelism=4,
                 binary="/build/two", sources={"invented2": "abc", "invented5": "q"})
    wanted = [(DEFAULT, bench, TRACES[bench], ["bingo", "sms"], {}) for bench in BENCHMARKS]
    a.ipc(wanted)
    b.ipc(wanted)
    assert first.waves == [3] and second.waves == [], "the rebuild re-measured a stock stack"
    invented = [(DEFAULT, bench, TRACES[bench], ["bingo", "invented2"], {}) for bench in BENCHMARKS]
    a.ipc(invented)
    b.ipc(invented)
    assert first.waves == [3, 3] and second.waves == [], "same header digest, same number"
    c = Measurer(cache, SpyBackend(), warmup=1, simulation=1, parallelism=4,
                 binary="/build/three", sources={"invented2": "REWRITTEN"})
    c.ipc(invented)
    assert c.runs == 3, "a rewritten invention must be measured again"
