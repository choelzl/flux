"""The costed chain, stage by stage, with a cutoff between the stages (docs/decisions.md D454).

A chain exists because the stages cost different amounts, so there has to be a rule about what
climbs. Until D454 the loop measured everything on the first stage and then jumped the frontier's
spread straight to the LAST stage: a three-stage chain could not be expressed, and a candidate
that clearly could not be the answer still cost a placement to confirm it.

What these pin: every stage is its own pool (a placed number is never compared with a screened
one), the cutoff drops what cannot be the answer and says why, the frontier and the finalists
choose who climbs, and the decision is made on the highest stage that produced results.
"""

from __future__ import annotations

import pytest
from flux_loop import (Candidate, LoopRequest, Problem, Verdict, above, below, run_loop,
                       within_best)


class Chain(Problem):
    """Three stages over the same four candidates, each stage a little less optimistic."""

    name = "chain"

    def __init__(self, *, cutoff=None, stages=("coarse", "middle", "fine")) -> None:
        self._cutoff = cutoff
        self._stages = list(stages)
        self.measured: list[tuple[str, int]] = []       # (stage, how many) per call

    def objective(self, request):
        return {"study": "chain"}

    def search(self, state):
        yield [Candidate(name=f"d{i}", knobs={"size": i}) for i in (1, 2, 3, 4)]

    def build(self, cand, subgoal, state):
        return cand.knobs["size"]

    def judge(self, built, cand, subgoal, state):
        return Verdict(True, 0.0)

    def stages(self):
        return list(self._stages)

    def measure_batch(self, cands, stage, state):
        self.measured.append((stage, len(cands)))
        # each stage costs the design a little of its quality, the way wires cost frequency
        penalty = {"coarse": 0.0, "middle": 1.0, "fine": 2.0}[stage]
        return [{"value": c.knobs["size"] * 10.0 - penalty, "cost": float(c.knobs["size"])}
                for c in cands]

    def frontier_axes(self):
        return (lambda p: p.metrics["value"], lambda p: p.metrics["cost"])

    def cutoff(self, stage, scored, state):
        return self._cutoff(stage, scored) if self._cutoff else list(scored)


def test_the_chain_climbs_stage_by_stage():
    """Three stages means three measurements, each over who survived the one below -- not a
    jump from the first stage to the last."""
    problem = Chain()
    out = run_loop(problem, LoopRequest(steps=1, finalists=2), log=lambda _m: None)
    assert [stage for stage, _n in problem.measured] == ["coarse", "middle", "fine"]
    assert [n for _r, n in problem.measured] == [4, 2, 2], "the finalist spread bounds each stage"
    assert out.decision is not None and out.decision.stage == "fine"
    assert {s.stage for s in out.scored} == {"coarse", "middle", "fine"}
    assert all(s.stage == "fine" for s in out.confirmed)
    assert all(s.stage == "fine" for s in out.frontier), "the frontier is the decision's stage"


def test_a_floor_cuts_before_the_next_stage_and_says_why():
    """An absolute floor is a requirement: a design under it cannot be the answer whatever else
    it does, so measuring it again is spending to confirm a refusal."""
    problem = Chain(cutoff=lambda stage, scored: (
        above(scored, "value", 25.0, unit=" units") if stage == "coarse" else (list(scored), "")))
    out = run_loop(problem, LoopRequest(steps=1, finalists=4), log=lambda _m: None)
    assert problem.measured[0] == ("coarse", 4)
    assert problem.measured[1] == ("middle", 2), "d1 and d2 were cut, d3 and d4 climbed"
    assert any("2 of 4 measured design(s) went no further" in l and "value below 25" in l
               for l in out.lessons)


def test_a_band_cuts_relative_to_this_runs_own_best():
    """The threshold a study wants when the achievable range is what the run discovers: the
    prefetcher's retention floor is a fraction of whatever the best measured speedup was."""
    problem = Chain(cutoff=lambda stage, scored: (
        within_best(scored, "value", 0.75) if stage == "coarse" else (list(scored), "")))
    out = run_loop(problem, LoopRequest(steps=1, finalists=4), log=lambda _m: None)
    # the best is 40; 75% of it is 30, so d3 (30) and d4 (40) climb
    assert problem.measured[1] == ("middle", 2)
    assert any("75% of this run's best" in l for l in out.lessons)


def test_a_budget_cuts_the_other_way():
    problem = Chain(cutoff=lambda stage, scored: (
        below(scored, "cost", 2.0) if stage == "coarse" else (list(scored), "")))
    problem_out = run_loop(problem, LoopRequest(steps=1, finalists=4), log=lambda _m: None)
    assert problem.measured[1] == ("middle", 2), "only the two cheapest climbed"
    assert problem_out.decision is not None


def test_a_cutoff_that_keeps_nothing_stops_the_chain_and_says_so():
    """The honest end: the decision is made on the last stage that produced results, and the
    report says the numbers are that stage's."""
    problem = Chain(cutoff=lambda stage, scored: above(scored, "value", 1e9))
    out = run_loop(problem, LoopRequest(steps=1), log=lambda _m: None)
    assert [stage for stage, _n in problem.measured] == ["coarse"]
    assert out.decision is not None and out.decision.stage == "coarse"
    assert any("worth the middle stage" in n for n in out.not_established)


def test_a_cutoff_that_raises_costs_nothing():
    """Knowledge about spending is help, not a gate: a broken cutoff must not end a run that
    has already measured things."""
    def boom(stage, scored):
        raise RuntimeError("the rule is wrong")

    problem = Chain(cutoff=boom)
    out = run_loop(problem, LoopRequest(steps=1, finalists=2), log=lambda _m: None)
    assert [stage for stage, _n in problem.measured] == ["coarse", "middle", "fine"]
    assert out.decision is not None


def test_screen_only_stops_after_the_first_stage():
    problem = Chain()
    out = run_loop(problem, LoopRequest(steps=1, screen_only=True), log=lambda _m: None)
    assert [stage for stage, _n in problem.measured] == ["coarse"]
    assert out.decision is not None and out.decision.stage == "coarse" and not out.confirmed
    assert any("every number is from the coarse stage" in n for n in out.not_established)


def test_the_finalists_hook_is_told_which_stage_it_is_choosing_for():
    """A three-stage chain can afford more candidates on a cheap middle stage than on the
    expensive last one, which the problem can only decide if it knows which is next."""
    asked: list[str] = []

    class Told(Chain):
        def finalists(self, front, state, stage=""):
            asked.append(stage)
            return list(front) if stage == "middle" else list(front)[:1]

    problem = Told()
    run_loop(problem, LoopRequest(steps=1, finalists=4), log=lambda _m: None)
    assert asked == ["middle", "fine"]
    assert problem.measured == [("coarse", 4), ("middle", 4), ("fine", 1)]


def test_a_task_document_can_declare_the_cutoff():
    """Whoever owns the requirement can say the rule without writing code (D454)."""
    from flux_loop import TaskError, TaskSpec

    spec = TaskSpec.from_dict({
        "id": "t", "statement": "s", "gate": {"test": ["true"]}, 
        "stages": [{"name": "screen", "command": ["true"], "metrics_re": {"fmax": r"f=(\d+)"},
                   "cutoff": {"metric": "fmax", "at": 400}},
                  {"name": "place", "command": ["true"], "metrics_re": {"fmax": r"f=(\d+)"}}],
    })
    assert spec.stages[0].cutoff == {"metric": "fmax", "at": 400}
    assert TaskSpec.from_dict(spec.to_dict()).stages[0].cutoff == spec.stages[0].cutoff
    for bad, why in (({"metric": "fmax"}, "exactly one"),
                     ({"at": 1}, "needs a `metric`"),
                     ({"metric": "fmax", "at": 1, "within": 0.5}, "exactly one"),
                     ({"metric": "fmax", "within": 2}, "fraction")):
        with pytest.raises(TaskError, match=why):
            TaskSpec.from_dict({"id": "t", "statement": "s", "gate": {"test": ["true"]},
                                "stages": [{"name": "screen", "command": ["true"],
                                           "metrics_re": {"fmax": r"f=(\d+)"}, "cutoff": bad}]})


def test_the_declared_cutoff_is_applied_by_the_document_problem():
    from flux_loop import PromptProblem, TaskSpec
    from flux_loop.types import Scored

    spec = TaskSpec.from_dict({
        "id": "t", "statement": "s", "gate": {"test": ["true"]}, 
        "objectives": [{"metric": "area", "direction": "minimize"}],
        "stages": [{"name": "screen", "command": ["true"], "metrics_re": {"area": r"a=(\d+)"},
                   "cutoff": {"metric": "area", "within": 0.5}},
                  {"name": "place", "command": ["true"], "metrics_re": {"area": r"a=(\d+)"}}],
    })
    problem = PromptProblem(spec)
    scored = [Scored(Candidate(name=f"d{a}"), "screen", {"area": float(a)})
              for a in (100, 150, 500)]
    kept, why = problem.cutoff("screen", scored, None)
    assert [s.name for s in kept] == ["d100", "d150"], "lower is better here (the objective)"
    assert "best 100" in why
    assert problem.cutoff("place", scored, None) == scored, "no rule on the last stage"
