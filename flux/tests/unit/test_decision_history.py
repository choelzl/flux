"""D861: a refined or explored design that beats the standing one takes its place. A whole design
from a refine or explore was gated and measured but never recorded as admitted, so each pass's
reload stood the FIRST design again (D504 weighed only admitted ones), and a pass whose cheap stage
let only its new design climb decided on that design alone. gelu_fp16: 316 MHz placed, yet
spelled3 (156 MHz) stood for nine passes and the decision fell to 230 MHz."""

from __future__ import annotations

import sqlite3

from flux_loop import Candidate, LoopRequest, Objectives, Problem, Template, Verdict, run_loop
from flux_loop.objective import Objective


class Drafts(Problem):
    """One whole design a pass, written by a generator; its size is the cost."""
    name = "drafts"

    def __init__(self, size, newest_only=False):
        self.size, self.newest_only = size, newest_only

    def objectives(self):
        return Objectives([Objective("size", "minimize")])

    def generator(self, subgoal, state):
        # an agent's name: it drafts a new design every pass (D593), as gelu's codex did
        return Template(lambda attempt: Candidate(name=f"d{self.size}", artifact="x" * self.size), name="agent:test")

    def build(self, cand, subgoal, state):
        return cand.artifact

    def judge(self, built, cand, subgoal, state):
        return Verdict(True, 0.0)

    def stages(self):
        return ["screen", "place"]

    def cutoff(self, stage, scored, state):
        # gelu's case: only the pass's new design climbs, the standing one is not placed again
        return [s for s in scored if s.candidate.name == f"d{self.size}"] if self.newest_only else scored

    def measure(self, cand, stage, state):
        return {"size": float(len(cand.artifact))}


def _passes(db, sizes, newest_only=False):
    out = []
    for size in sizes:
        said: list[str] = []
        got = run_loop(Drafts(size, newest_only), LoopRequest(db=db, prototype=False, critique_rounds=0),
                       proposer=None, log=said.append)
        assert any(s.candidate.name == f"d{size}" for s in got.scored), "each pass measured its own design"
        stood = next((m.split()[2] for m in said if "direction:" in m), None)
        out.append((stood, got.decision.candidate.name))
    return out


def test_the_better_design_stands_and_stays_the_decision(tmp_path):
    got = _passes(str(tmp_path / "d.db"), (5, 2, 9, 7))
    assert got == [(None, "d5"), ("d5", "d2"), ("d2", "d2"), ("d2", "d2")], got


def test_a_pass_that_places_only_its_new_design_decides_over_the_record(tmp_path):
    got = _passes(str(tmp_path / "d.db"), (5, 2, 9, 7), newest_only=True)
    assert [d for _s, d in got] == ["d5", "d2", "d2", "d2"], got
    assert got[2][0] == "d2" and got[3][0] == "d2", got


def test_a_record_from_before_still_finds_its_best_design(tmp_path):
    """Rows made before D861 have no admission for an explored design; its measurements say it
    passed the gate, so the reload weighs it."""
    db = str(tmp_path / "d.db")
    _passes(db, (5, 2, 9))
    with sqlite3.connect(db) as c:                    # as an older Flux left it: the first admission only
        first = c.execute("select min(id) from trials where stage='admit'").fetchone()[0]
        c.execute("delete from trials where stage='admit' and id != ?", (first,))
    assert _passes(db, (7,)) == [("d2", "d2")]
