"""D511: the objective as a vector -- one rule for "better", and the frontier, the decision,
the early stop and the reload preference derived from it."""

from __future__ import annotations

import pytest

from flux_loop import Candidate, Objective, Objectives, Scored


def _nlu(goal=800.0) -> Objectives:
    return Objectives([Objective("fmax_mhz", "maximize", goal=goal, stage="confirm", tie=0.03),
                       Objective("area_um2", "minimize"), Objective("power_w", "minimize")])


def _sc(name, **metrics) -> Scored:
    return Scored(Candidate(name, ""), "confirm", metrics, {})


def test_a_document_names_the_goal_the_direction_and_the_band():
    objs = Objectives.from_doc([{"metric": "fmax_mhz", "goal": ">= 800", "stage": "confirm", "tie": 0.03},
                                {"metric": "area_um2", "direction": "minimize"}, "power_w"])
    assert objs[0] == Objective("fmax_mhz", "maximize", 800.0, "confirm", 0.03)
    assert objs[1].direction == "minimize" and objs[2] == Objective("power_w")
    assert Objectives.from_doc([{"metric": "latency", "goal": "<= 12"}])[0].direction == "minimize"
    assert objs.goal is objs[0] and objs.describe() == "fmax_mhz >= 800 (confirm), then least area_um2, then most power_w"
    with pytest.raises(ValueError, match="needs a `metric`"):
        Objectives.from_doc([{"direction": "maximize"}])
    with pytest.raises(ValueError, match="direction must be one of"):
        Objective("x", "sideways")
    with pytest.raises(ValueError, match="a number, or"):
        Objectives.from_doc([{"metric": "x", "goal": "about 8"}])


def test_the_one_rule_is_the_nlu_rule_it_replaces():
    """The cases D506 wrote for `_better`, verbatim, against the vector."""
    objs = _nlu()

    def better(f, a, of, oa):
        return objs.better({"fmax_mhz": f, "area_um2": a}, {"fmax_mhz": of, "area_um2": oa})

    assert not better(511.6, 1120.0, 507.3, 708.0)          # a tie in clock, bigger: no
    assert better(507.3, 708.0, 511.6, 1120.0)              # a tie in clock, smaller: yes
    assert better(560.0, 1120.0, 507.3, 708.0)              # clearly faster: yes, whatever the area
    assert not better(400.0, 100.0, 507.3, 708.0)           # slower: no, however small
    assert better(850.0, 900.0, 507.3, 708.0)               # reaches the goal: yes
    assert not better(850.0, 900.0, 820.0, 700.0) and better(850.0, 600.0, 820.0, 700.0)   # both at it: the smaller
    assert not better(700.0, 100.0, 820.0, 700.0)           # the old reaches the goal, the new does not
    assert better(1.0, None, None, None)                    # nothing measured to beat
    assert not better(None, 1.0, 500.0, 1.0)                # nothing measured cannot displace a measurement
    # no goal: the first objective by more than its band, then the next strictly
    plain = Objectives([Objective("fmax_mhz", tie=0.03), Objective("area_um2", "minimize")])
    assert plain.better({"fmax_mhz": 520, "area_um2": 900}, {"fmax_mhz": 500, "area_um2": 100})
    assert not plain.better({"fmax_mhz": 505, "area_um2": 100}, {"fmax_mhz": 500, "area_um2": 100})   # a dead heat is not better
    assert plain.better({"fmax_mhz": 505, "area_um2": 90}, {"fmax_mhz": 500, "area_um2": 100})


def test_the_tournament_keeps_what_the_numbers_say():
    objs = _nlu()
    a, b, c = Candidate("a", ""), Candidate("b", ""), Candidate("c", "")
    rows = [(a, {"fmax_mhz": 466.0, "area_um2": 500.0}), (b, {"fmax_mhz": 341.0, "area_um2": 400.0})]
    assert objs.best_of(rows) is a and objs.best_of(rows[::-1]) is a
    assert objs.best_of([(a, None), (b, {"fmax_mhz": 1.0})]) is b        # unmeasured cannot win
    assert objs.best_of([(a, {"fmax_mhz": 5.0}), (c, {"fmax_mhz": 5.0})]) is c   # a dead heat: the later
    assert objs.best_of([(a, None)]) is None


def test_the_decision_follows_the_vector():
    objs = _nlu()
    pool = [_sc("slow-small", fmax_mhz=700, area_um2=100, power_w=1), _sc("fast-big", fmax_mhz=820, area_um2=900, power_w=3),
            _sc("fast-mid", fmax_mhz=805, area_um2=600, power_w=2)]
    pick, why = objs.decide(pool)
    assert pick.candidate.name == "fast-mid" and why == "the least area_um2 at fmax_mhz >= 800"
    pick, why = objs.decide(pool[:1])
    assert pick.candidate.name == "slow-small" and why.startswith("nothing reaches fmax_mhz 800; the most fmax_mhz")
    assert objs.decide([]) == (None, "nothing measured")
    one = Objectives([Objective("bytes", "minimize")])
    pick, why = one.decide([_sc("x", bytes=3), _sc("y", bytes=2)])
    assert pick.candidate.name == "y" and why == "the smallest bytes"
    knee = Objectives([Objective("value"), Objective("cost", "minimize")])
    pick, why = knee.decide([_sc("p", value=1, cost=1), _sc("q", value=10, cost=2), _sc("r", value=11, cost=9)])
    assert pick.candidate.name == "q" and why == "the knee of value / cost"
    assert Objectives().decide([_sc("only")])[1] == "the only kind of answer this problem has"


def test_the_frontier_axes_and_the_early_stop_come_from_the_first_objectives():
    objs = _nlu()
    better, cost = objs.frontier_axes()
    p = _sc("p", fmax_mhz=700, area_um2=100)
    assert better(p) == 700 and cost(p) == 100
    assert Objectives([Objective("x")]).frontier_axes() is None
    # D543: a goal met with area and power still to shrink is a floor reached, not a search finished
    assert objs.good_enough({"fmax_mhz": 812.0}) is None
    only = Objectives.from_doc([{"metric": "fmax_mhz", "direction": "maximize", "goal": 800}])
    assert only.good_enough({"fmax_mhz": 812.0}) == "fmax_mhz is 812, the 800 asked for"
    assert objs.good_enough({"fmax_mhz": 700.0}) is None and Objectives([Objective("x")]).good_enough({"x": 1}) is None


def test_a_task_document_carries_the_full_objective_and_the_loop_derives_the_rest(tmp_path):
    from flux_loop import PromptProblem, TaskSpec

    task = TaskSpec.from_dict({
        "id": "t", "statement": "s", "language": "text", "extension": ".txt",
        "gate": {"test": ["true"]},
        "stages": [{"name": "screen", "command": ["echo", "fmax_mhz=1 area_um2=1"], "metrics_re": {"fmax_mhz": r"fmax_mhz=(\d+)", "area_um2": r"area_um2=(\d+)"}}],
        "objectives": [{"metric": "fmax_mhz", "goal": ">= 800", "tie": 0.03}, {"metric": "area_um2", "direction": "minimize"}],
    })
    assert task.objectives[0].goal == 800.0 and task.objectives[0].tie == 0.03
    assert task.to_dict()["objectives"][0] == {"metric": "fmax_mhz", "direction": "maximize", "goal": 800.0, "tie": 0.03}
    problem = PromptProblem(task)
    assert problem.objectives().goal.metric == "fmax_mhz" and problem.frontier_axes() is not None
    pick, why = problem.decide([_sc("a", fmax_mhz=820, area_um2=5), _sc("b", fmax_mhz=830, area_um2=9)], None)
    assert pick.candidate.name == "a" and why == "the least area_um2 at fmax_mhz >= 800"


def test_a_shallower_stage_must_clear_the_goal_plus_the_margin():
    """D522 (Cedric: "800 MHz means full P&R but we want some margin before a design is
    under-estimated by model/placement"): the goal is judged on its own stage; a number from
    a shallower stage must clear the goal plus the margin, and the words say which."""
    o = Objective.from_doc({"metric": "fmax_mhz", "direction": "maximize", "goal": 800, "stage": "route", "margin": 0.03, "unit": "MHz"})
    chain = ["screen", "confirm", "route"]
    assert o.goal_at("route", chain) == 800 and o.goal_at(None, chain) == 800 and o.goal_at("confirm", chain) == pytest.approx(824.0)
    assert o.goal_at("screen") == pytest.approx(824.0), "a stage that is not the goal's counts as shallower when the chain is unknown"
    assert o.meets({"fmax_mhz": 810}, "route", chain) and not o.meets({"fmax_mhz": 810}, "confirm", chain) and o.meets({"fmax_mhz": 825}, "confirm", chain)
    assert o.to_doc()["margin"] == 0.03 and Objective.from_doc(o.to_doc()) == o
    assert "+3% on a shallower stage" in o.describe()
    lo = Objective.from_doc({"metric": "area_um2", "direction": "minimize", "goal": 1000, "stage": "route", "margin": 0.1})
    assert lo.goal_at("screen", chain) == pytest.approx(1000 / 1.1) and lo.meets({"area_um2": 900}, "screen", chain) and not lo.meets({"area_um2": 950}, "screen", chain)
    objs = Objectives([o, Objective("area_um2", "minimize")])
    # the same pool decides differently by where it was measured
    placed = [Scored(Candidate("a", ""), stage="confirm", metrics={"fmax_mhz": 810, "area_um2": 5}),
              Scored(Candidate("b", ""), stage="confirm", metrics={"fmax_mhz": 830, "area_um2": 9})]
    pick, why = objs.decide(placed, chain)
    assert pick.candidate.name == "b" and "824" in why and "plus the margin on the confirm stage" in why
    routed = [Scored(Candidate("a", ""), stage="route", metrics={"fmax_mhz": 810, "area_um2": 5}),
              Scored(Candidate("b", ""), stage="route", metrics={"fmax_mhz": 830, "area_um2": 9})]
    pick, why = objs.decide(routed, chain)
    assert pick.candidate.name == "a" and why == "the least area_um2 at fmax_mhz >= 800"
    assert objs.better({"fmax_mhz": 830, "area_um2": 9}, {"fmax_mhz": 810, "area_um2": 5}, "confirm", chain)
    assert not objs.better({"fmax_mhz": 830, "area_um2": 9}, {"fmax_mhz": 810, "area_um2": 5}, "route", chain)
    assert objs.good_enough({"fmax_mhz": 810}, "confirm", chain) is None
    # D543: with area still to shrink the vector is never "good enough"; alone, the goal's words carry the margin
    assert objs.good_enough({"fmax_mhz": 830}, "confirm", chain) is None
    alone = Objectives([o])
    assert alone.good_enough({"fmax_mhz": 830}, "confirm", chain) == "fmax_mhz is 830, the 824 asked for on the confirm stage (800 route with a 3% margin)"
    assert alone.good_enough({"fmax_mhz": 810}, "route", chain) == "fmax_mhz is 810, the 800 asked for"
    with pytest.raises(ValueError, match="margin"):
        Objective("fmax_mhz", margin=-0.1)


def test_the_measured_margin_on_a_shallow_stage_is_used_and_the_documents_is_the_floor():
    """D562: the record's ratio between the stages sets the margin on the shallower one; the
    document's margin is never lowered by it."""
    from flux_loop.objective import Objective

    o = Objective("fmax_mhz", "maximize", goal=800.0, stage="route", margin=0.03, margins=(("confirm", 0.139),))
    stages = ["screen", "confirm", "route"]
    assert abs(o.goal_at("confirm", stages) - 800.0 * 1.139) < 1e-9 and o.margin_at("confirm") == 0.139
    assert abs(o.goal_at("screen", stages) - 824.0) < 1e-9                 # no measured margin: the document's
    assert o.goal_at("route", stages) == 800.0
    floor = Objective("fmax_mhz", "maximize", goal=800.0, stage="route", margin=0.2, margins=(("confirm", 0.139),))
    assert abs(floor.goal_at("confirm", stages) - 960.0) < 1e-9
    small = Objective("area_um2", "minimize", goal=100.0, stage="route", margins=(("screen", 0.25),))
    assert abs(small.goal_at("screen", stages) - 80.0) < 1e-9
