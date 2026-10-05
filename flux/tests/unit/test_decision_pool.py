"""D809: the decision is over every design measured on the deepest stage so far -- this pass's
and the record's -- so a better design measured in an earlier pass is never dropped when the
cheap stage's finalists move, nor replaced by a cheap stage's numbers when a pass's deep stage
measured nothing."""

from __future__ import annotations

import pytest

from flux_loop import LoopRequest, Objectives, Problem, Verdict, run_loop
from flux_loop.dse import Sweep
from flux_loop.objective import Objective


class Two(Problem):
    """The screen says high x is better; placement says low x is (it is the truth). Two new points a pass."""
    name = "two"

    def __init__(self, place_fails_from=None):
        self.policy, self.fails = Sweep(batch_size=2), place_fails_from

    def roles(self):
        from flux_loop.roles import Roles

        return Roles(orchestrator=self.policy)

    def space(self, state):
        return {"x": [0, 1, 2, 3, 4, 5]}

    def objectives(self):
        return Objectives([Objective("cost", "minimize")])

    def build(self, cand, subgoal, state):
        return cand.knobs

    def judge(self, built, cand, subgoal, state):
        return Verdict(True, 0.0)

    def stages(self):
        return ["screen", "place"]

    def measure(self, cand, stage, state):
        x = cand.knobs["x"]
        if stage == "place" and self.fails is not None and x >= self.fails:
            raise RuntimeError("place failed")
        return {"cost": float(10 - x) if stage == "screen" else float(x)}


@pytest.mark.parametrize("fails", [None, 4])
def test_the_best_placed_design_stays_the_decision_across_passes(tmp_path, fails):
    db = str(tmp_path / "two.db")
    picks = []
    for _ in range(3):
        out = run_loop(Two(fails), LoopRequest(batch=2, steps=2, finalists=1, db=db), proposer=None, log=lambda _m: None)
        d = out.decision
        picks.append((d.candidate.knobs["x"], d.stage))
    assert picks == [(1, "place")] * 3, picks
