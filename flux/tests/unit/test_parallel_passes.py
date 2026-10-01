"""D747: `budget.parallel` -- passes at once, a wave at a time, each its own design and branch,
sharing the run's search; then one decision over everything; one at a time when capped."""

from __future__ import annotations

import json
import threading
import time
from dataclasses import make_dataclass

import flux_profile
from flux_loop import Candidate, LoopRequest, Problem, Verdict, run_loop
from flux_loop.journal import Journal, read_events
from flux_loop.passes import run_passes

Request = make_dataclass("Request", [("passes", int, 0), ("explore", int, 0), ("parallel", int, 1), ("steps", int, 1)])


@__import__("dataclasses").dataclass
class Out:
    at_rest: bool = False
    explorable: bool = True
    decision: object = None


def test_passes_run_in_waves_tagged_then_one_decision_over_all(tmp_path, monkeypatch):
    monkeypatch.delenv("FLUX_PARALLEL_MAX", raising=False)
    seen, lock = [], threading.Lock()

    def run(req, _feedback):
        with flux_profile.phase("DSE: batch"):
            with lock:
                seen.append((threading.current_thread().name, req.steps, time.monotonic()))
            time.sleep(0.2)
        return Out()

    j = Journal(str(tmp_path / "events.jsonl"))
    flux_profile.add_listener(j)
    said = []
    try:
        run_passes(run, Request(parallel=3), passes=5, say=said.append)
    finally:
        flux_profile.remove_listener(j)
    names = [s[0] for s in seen]
    assert sorted(names[:3]) == ["flux-pass-1", "flux-pass-2", "flux-pass-3"] and sorted(names[3:5]) == ["flux-pass-4", "flux-pass-5"]
    assert max(s[2] for s in seen[:3]) - min(s[2] for s in seen[:3]) < 0.15, "a wave's passes start together"
    assert seen[-1][1] == 0 and len(seen) == 6, "then one pass that only decides, over what all of them recorded"
    assert "\n── passes 4–5 at once ──" in said and any("the decision, over every pass" in s for s in said)
    ev = read_events(str(tmp_path / "events.jsonl"))[0]
    tags = sorted(e["params"].get("pass") or 0 for e in ev if e["ev"] == "start")
    assert tags == [0, 1, 2, 3, 4, 5], "each pass's phases carry its number; the decision's none"
    marks = [json.loads(e["why"]) for e in ev if e["ev"] == "mark" and e["name"] == "pass"]
    assert marks[-1] == {"n": 6, "conclude": True} and {"n": 4, "explore": 0, "together": [4, 5]} in marks


def test_a_server_holds_passes_to_one_at_a_time_unless_allowed(monkeypatch):
    monkeypatch.setenv("FLUX_PARALLEL_MAX", "1")
    seen, said = [], []
    run_passes(lambda r, f: seen.append(threading.current_thread().name) or Out(), Request(parallel=3), passes=3, say=said.append)
    assert len(seen) == 3 and not any(n.startswith("flux-pass-") for n in seen), "one after another, on the run's thread"
    assert any("one pass at a time: the document asks 3 at once" in s for s in said)


class Sweep(Problem):
    """Six points, each measured once: passes at once share the search and never take the same."""

    name = "sweep"

    def __init__(self) -> None:
        self.measured: list[int] = []
        self.lock = threading.Lock()

    def search(self, state):
        yield [Candidate(name=f"p{i}", knobs={"x": i}) for i in range(6)]

    def build(self, cand, subgoal, state):
        return cand.knobs["x"]

    def judge(self, built, cand, subgoal, state):
        return Verdict(True, 0.0)

    def stages(self):
        return ["coarse"]

    def measure_batch(self, cands, stage, state):
        with self.lock:
            self.measured.extend(c.knobs["x"] for c in cands)
        time.sleep(0.05)
        return [{"value": float(c.knobs["x"] + 1), "cost": 1.0} for c in cands]


def test_passes_at_once_share_one_search_and_each_measures_its_own(tmp_path, monkeypatch):
    monkeypatch.delenv("FLUX_PARALLEL_MAX", raising=False)
    prob = Sweep()
    req = LoopRequest(db=str(tmp_path / "s.db"), steps=1, finalists=0, screen_only=True, parallel=3)
    out = run_passes(lambda r, f: run_loop(prob, r, log=lambda _m: None), req, passes=6, say=lambda _m: None)
    assert sorted(prob.measured) == [0, 1, 2, 3, 4, 5], "every point once, none twice"
    assert out.decision is not None and len(out.scored) >= 6, "the decision sees every pass's designs"
