"""The tools work while the model thinks (D563, review item 2): an admitted part is measured
alone on a worker thread while the loop writes the next part; whoever measures it next takes
the numbers instead of running the tool again."""

from __future__ import annotations

import threading
import time

from flux_llm import ScriptedProposer
from flux_loop import Candidate, LoopRequest, LoopState, Problem, Verdict, run_loop
from flux_loop.ladder import measure_alone
from flux_loop.measure import Ahead, cached_measure


class Parts(Problem):
    """Two parts, one screen stage; `measure` notes which thread took each number."""

    name = "parts"

    def __init__(self):
        self.measured: list[tuple[str, str, str]] = []
        self.lock = threading.Lock()

    def subgoals(self):
        return ["a", "b"]

    def roles(self):
        from flux_loop.roles import Roles, Rules

        return Roles(orchestrator=Rules())              # no planning prompt: the replies are the designs

    def ladder(self):
        from flux_loop import Ladder

        return Ladder(alone="screen")                     # the parts ARE measured alone: worth working ahead

    def objectives(self):
        from flux_loop import Objectives
        from flux_loop.objective import Objective

        return Objectives([Objective("fmax_mhz", "maximize")])

    def design_prompt(self, subgoal, method, state, human, prior, prior_why):
        return f"write {subgoal}", None

    def parse_design(self, reply, subgoal):
        return Candidate(f"{subgoal}#1", f"{subgoal}:{reply}", subgoal=subgoal), ""     # distinct text per part

    def build(self, cand, subgoal, state):
        return cand.artifact

    def judge(self, built, cand, subgoal, state):
        return Verdict(True, 0.0)

    def stages(self):
        return ["screen"]

    def compose(self, admitted, state):
        return Candidate("whole", "+".join(admitted[k].artifact for k in sorted(admitted)))

    def measure(self, cand, stage, state):
        time.sleep(0.05)
        with self.lock:
            self.measured.append((cand.name, stage, threading.current_thread().name))
        return {"fmax_mhz": 100.0 + len(cand.artifact)}


def test_an_admitted_part_is_measured_alone_while_the_next_part_is_written():
    prob = Parts()
    said = []
    out = run_loop(prob, LoopRequest(steps=4, prototype=False, critique_rounds=0, patching=False, finalists=0, screen_only=True),
                   proposer=ScriptedProposer(["aaaa", "bb"]), log=said.append)
    assert out.decision is not None
    ahead = [(n, t) for n, _s, t in prob.measured if t.startswith("flux-ahead")]
    assert {n for n, _t in ahead} == {"a#1", "b#1"}, prob.measured           # both parts, on worker threads
    assert any("measuring a alone on screen while the next part is written" in m for m in said)
    assert [n for n, _s, t in prob.measured if not t.startswith("flux-ahead")] == ["whole"]   # the whole, in line


def test_the_numbers_taken_ahead_are_used_once_and_never_measured_twice():
    prob = Parts()
    state = LoopState(request=LoopRequest(), say=lambda _m: None, proposer=None, feedback=None)
    state.ahead = Ahead(2)
    cand = Candidate("a#1", "aaaa", subgoal="a")
    assert state.ahead.start(prob, state, cand, "screen") and not state.ahead.start(prob, state, cand, "screen")
    got = cached_measure(prob, state, cand, "screen")
    assert got == {"fmax_mhz": 104.0} and len(prob.measured) == 1 and prob.measured[0][2].startswith("flux-ahead")
    assert state.ahead.take(cand, "screen") is None                                  # taken once
    m = measure_alone(prob, cand, state)
    assert m == {"fmax_mhz": 104.0} and len(prob.measured) == 2, "no stash left: measured in line, on this thread"
    assert not prob.measured[1][2].startswith("flux-ahead")
    assert state.ahead.drain() == 0
    state.ahead.close()


def test_ahead_can_be_switched_off_by_the_budget():
    prob = Parts()
    run_loop(prob, LoopRequest(steps=4, prototype=False, critique_rounds=0, patching=False, finalists=0, screen_only=True, ahead=False),
             proposer=ScriptedProposer(["aaaa", "bb"]), log=lambda _m: None)
    assert not any(t.startswith("flux-ahead") for _n, _s, t in prob.measured)
