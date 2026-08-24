"""Parts drafted at once (D569, review item 2's second half): several parts' generation
boxes run on worker threads while admission, the record and the state keep one writer."""

from __future__ import annotations

import threading
import time

from flux_llm import Reply
from flux_loop import Candidate, LoopRequest, Problem, Verdict, run_loop


class Slow:
    """A model that takes a while per turn and notes which thread asked."""

    def __init__(self, delay: float = 0.25) -> None:
        self.delay = delay
        self.threads: list[str] = []
        self.started: list[float] = []

    def propose(self, prompt, *, schema=None, tools=None, budget=None):
        self.threads.append(threading.current_thread().name)
        self.started.append(time.monotonic())
        time.sleep(self.delay)
        part = prompt.split()[-1]
        return Reply.of(f"design-of-{part}")


class Parts(Problem):
    name = "parts"

    def __init__(self, names=("a", "b", "c", "d")):
        self.names = list(names)

    def subgoals(self):
        return list(self.names)

    def design_prompt(self, subgoal, method, state, human, prior, prior_why):
        if state.records is not None:                     # a record write from the drafting thread
            state.records.remember("draft", {"part": subgoal, "thread": threading.current_thread().name})
        return f"write {subgoal}", None

    def parse_design(self, reply, subgoal):
        return Candidate(f"{subgoal}#1", reply, subgoal=subgoal), ""

    def build(self, cand, subgoal, state):
        return cand.artifact

    def judge(self, built, cand, subgoal, state):
        return Verdict(True, 0.0)

    def compose(self, admitted, state):
        return Candidate("whole", "+".join(admitted[k].artifact for k in sorted(admitted)))


def _run(parallel: int, tmp_path, delay: float = 0.25):
    model = Slow(delay)
    prob = Parts()
    t0 = time.monotonic()
    out = run_loop(prob, LoopRequest(db=str(tmp_path / f"p{parallel}.db"), steps=8, prototype=False, critique_rounds=0,
                                     patching=False, finalists=0, parallel_parts=parallel),
                   proposer=model, log=lambda _m: None)
    return out, model, time.monotonic() - t0


def test_parts_drafted_at_once_overlap_and_are_all_admitted(tmp_path):
    from flux_records import Records

    out, model, wall = _run(4, tmp_path)
    assert set(out.admitted) == {"a", "b", "c", "d"}                # no stage to measure: the admissions are the point
    drafts = [t for t, name in zip(model.started, model.threads) if name != "MainThread"]
    assert len(drafts) == 4 and max(drafts) - min(drafts) < 0.2, "the four drafts did not start together"
    assert len({t for t in model.threads if t != "MainThread"}) >= 2
    _out, _model, sequential = _run(1, tmp_path)
    assert wall < sequential * 0.8, f"drafted at once: {wall:.2f}s; one at a time: {sequential:.2f}s"
    rec = Records(str(tmp_path / "p4.db"), objective=out.provenance.get("objective") or {"study": "parts"})
    drafts = rec.recall("draft")
    assert {d["part"] for d in drafts} == {"a", "b", "c", "d"}, "the record took every drafting thread's write"


def test_one_at_a_time_is_the_default_and_the_step_budget_counts_each_part(tmp_path):
    out, model, wall = _run(1, tmp_path)
    assert set(out.admitted) == {"a", "b", "c", "d"} and wall >= 4 * 0.25
    assert all(t == "MainThread" for t in model.threads)
    prob = Parts()
    out = run_loop(prob, LoopRequest(steps=2, prototype=False, critique_rounds=0, patching=False, finalists=0, parallel_parts=4),
                   proposer=Slow(0.01), log=lambda _m: None)
    assert len(out.admitted) == 2 and out.stopped == "the step budget", "two steps: two parts, drafted together"
