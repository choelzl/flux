"""D525 (review step 8a, Cedric: parallelism is "the cheapest large speed-up left, and it
needs no model"): a stage's candidates and a sweep's points are measured `workers` at a
time; the cache and the record are written on the caller's thread."""

from __future__ import annotations

import threading
import time

from flux_loop import Candidate, Ladder, LoopRequest, LoopState, Objective, Objectives, Problem, Verdict
from flux_loop.ladder import pipeline_sweep
from flux_loop.measure import measure_many, measure_pool
from flux_loop.pool import run_parallel, workers


class _Counter:
    """How many workers are inside at once, at most."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.inside = 0
        self.peak = 0
        self.calls = 0

    def __enter__(self):
        with self.lock:
            self.inside += 1
            self.calls += 1
            self.peak = max(self.peak, self.inside)

    def __exit__(self, *exc):
        with self.lock:
            self.inside -= 1


class Slow(Problem):
    name = "slow"

    def __init__(self, delay: float = 0.25) -> None:
        self.delay = delay
        self.count = _Counter()

    def subgoals(self):
        return ["p"]

    def stages(self):
        return ["screen"]

    def objectives(self):
        return Objectives([Objective("fmax_mhz", "maximize", goal=800), Objective("area_um2", "minimize")])

    def measure(self, cand, stage, state):
        with self.count:
            time.sleep(self.delay)
        if cand.name == "boom":
            raise RuntimeError("the tool died")
        return {"fmax_mhz": float(len(cand.artifact)) * 100, "area_um2": 1.0}


def _state(**kw):
    return LoopState(request=LoopRequest(**kw), say=lambda _m: None, proposer=None, feedback=None)


def test_run_parallel_keeps_the_order_and_returns_an_exception_beside_its_item():
    def fn(x):
        if x == 3:
            raise ValueError("three")
        time.sleep(0.05 * (5 - x))
        return x * 10

    out = run_parallel([1, 2, 3, 4], fn, 4)
    assert [v for v, _e in out] == [10, 20, None, 40]
    assert isinstance(out[2][1], ValueError) and out[0][1] is None
    assert run_parallel([1], fn, 8) == [(10, None)] and run_parallel([], fn, 2) == []


def test_a_stages_candidates_are_measured_workers_at_a_time_through_the_cache():
    prob = Slow(0.25)
    cands = [Candidate(f"c{i}", "x" * (i + 1), subgoal="p") for i in range(6)] + [Candidate("boom", "y", subgoal="p")]
    st = _state(workers=7)
    t0 = time.monotonic()
    got = measure_pool(prob, st, cands, "screen")
    took = time.monotonic() - t0
    assert prob.count.peak >= 4 and took < 0.25 * 3, f"seven measurements took {took:.2f}s at peak {prob.count.peak}"
    assert [m["fmax_mhz"] for m in got[:6]] == [100.0, 200.0, 300.0, 400.0, 500.0, 600.0]
    assert got[6] == {"error": "RuntimeError: the tool died"}
    assert workers(LoopRequest(workers=0)) >= 1 and workers(LoopRequest(workers=3)) == 3
    # through measure_many: the failed one is refused with the stage's reason, the rest scored
    scored = measure_many(prob, st, cands, "screen")
    assert [s.candidate.name for s in scored] == [f"c{i}" for i in range(6)]
    assert any(name == "boom" and "the tool died" in why for name, why in st.refused)
    # one worker runs inline, in order
    prob2 = Slow(0.0)
    measure_pool(prob2, _state(workers=1), cands[:3], "screen")
    assert prob2.count.peak == 1 and prob2.count.calls == 3


class Swept(Slow):
    """A part whose register count sets its clock: k registers -> 100 * k MHz."""

    def __init__(self) -> None:
        super().__init__(0.2)
        self.built = _Counter()

    def ladder(self):
        return Ladder(sweep=(2, 4, 8, 16, 24, 32))

    def transpile(self, prototype, subgoal, state, pipeline=None):
        k = int(pipeline or 0)
        return Candidate(f"{subgoal}_p{k}", f"module p{k}", subgoal=subgoal, meta={"pipeline": k, "latency": k})

    def build(self, cand, subgoal, state):
        with self.built:
            time.sleep(0.2)
        return cand.artifact

    def judge(self, built, cand, subgoal, state):
        return Verdict(True, 0.0, "")

    def measure(self, cand, stage, state):
        with self.count:
            time.sleep(0.2)
        return {"fmax_mhz": 100.0 * int(cand.meta["pipeline"]), "area_um2": float(cand.meta["pipeline"])}


def test_a_register_sweep_fans_out_and_still_takes_the_least_count_that_reaches_the_goal():
    prob = Swept()
    st = _state(workers=6)
    t0 = time.monotonic()
    got = pipeline_sweep(prob, prob.ladder(), "p", "def design(x):\n    return x\n", st)
    took = time.monotonic() - t0
    assert got is not None and got[1].name == "p_p8" and got[0] == 800.0, "8 registers is the least that reaches 800"
    # six points built and measured at once (0.4 s each in series would be 2.4 s), then the
    # bisection between 4 and 8 (6, then 7 or 5): under the time of three points in series
    assert prob.built.peak >= 4 and took < 0.4 * 3.5, f"the sweep took {took:.2f}s at peak {prob.built.peak}"
    assert st.part("p").alone["fmax_mhz"] == 800.0
