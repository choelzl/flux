"""Pass 0 stays a reference in live standings and resumed search state."""

import dataclasses

import pytest

from flux_loop import Candidate, LoopRequest, Objectives, Problem
from flux_loop.baseline import _restore
from flux_loop.objective import Objective
from flux_loop.observe import _design_rows, _front_points
from flux_loop.types import LoopState, Scored


class Measurements(Problem):
    name = "measurements"

    def stages(self):
        return ["screen", "confirm"]

    def objectives(self):
        return Objectives([Objective("cost", "minimize"), Objective("area", "minimize")])


def _state():
    return LoopState(request=LoopRequest(), say=lambda m: None, proposer=None, feedback=None)


@pytest.mark.parametrize("searching", [False, True])
def test_live_baseline_standings_select_retained_designs_instead_of_current_pass(searching):
    problem, state = Measurements(), _state()
    retained = Scored(Candidate("retained", artifact="real", meta={"composed": ["part"]}),
                      "screen", {"cost": 10, "area": 10})
    baseline = Scored(Candidate("baseline", artifact="reference", meta={"baseline": True, "composed": ["part"]}),
                      "confirm", {"cost": 1, "area": 1})
    state.scored = [baseline]
    state.on_stage = {"screen": [retained], "confirm": [baseline]}
    if searching:
        rows, front, axes = _design_rows(problem, state)
        assert [row["name"] for row in rows if row["decision"]] == ["retained"]
    else:
        front, axes = _front_points(problem, state)
    assert axes == ["cost", "area"]
    assert [point["name"] for point in front] == ["retained"]
    assert front[0]["decision"]


def test_live_baseline_only_has_no_selected_design():
    problem, state = Measurements(), _state()
    state.scored = [Scored(Candidate("baseline", meta={"baseline": True, "composed": ["part"]}),
                           "confirm", {"cost": 1, "area": 1})]
    assert _design_rows(problem, state) == ([], [], [])
    assert _front_points(problem, state) == ([], ["cost", "area"])


def test_legacy_baseline_snapshot_cannot_restore_an_admitted_search_design():
    state = _state()
    cand = Candidate("baseline", artifact="reference", meta={"baseline": True})
    scored = Scored(cand, "confirm", {"cost": 1})
    _restore(state, {"scored": [dataclasses.asdict(scored)], "admitted": {"*": cand.to_record()},
                     "failures": [], "reached": "confirm", "stopped": "baseline checked and measured"})
    assert state.scored == [scored]  # reference measurements remain available
    assert not state.admitted and not state.on_stage
