"""The cost axis is a second objective, not a tie-breaker (D362): the loop lays the speed/cost
trade-off out, confirms along it rather than at one corner, and a goal relative to the best
(`keep`/`above`) or a phase's `floor` refuses what falls under it. The frontier arithmetic on
plain points, the loop's use of it on the toy document."""

from __future__ import annotations

from typing import NamedTuple

from flux_frontier import best_within, frontier, spread


class P(NamedTuple):
    speed: float
    cost: int
    who: str


def _front(pts):
    return frontier(pts, better=lambda p: p.speed, cost=lambda p: p.cost)


def _spread(front, n, keep=()):
    return spread(front, n, keep=list(keep), cost=lambda p: p.cost)


# ---- the frontier -------------------------------------------------------------------------

def test_the_frontier_is_every_point_faster_than_everything_cheaper():
    pts = [P(1.0439, 35_096, "incumbent"), P(1.0626, 97_208, "b"), P(1.0671, 206_496, "c"),
           P(1.0600, 150_000, "dominated: slower than b and bigger"),
           P(1.0620, 97_208, "dominated: same size as b, slower")]
    assert [p.who for p in _front(pts)] == ["incumbent", "b", "c"]


def test_a_bigger_design_that_is_not_faster_is_dominated():
    assert [p.who for p in _front([P(1.05, 100, "small"), P(1.05, 200, "same speed, twice the size")])] == ["small"]


def test_spread_confirms_both_ends_then_the_widest_gaps():
    front = [P(1.04, 32_000, "a"), P(1.05, 40_000, "b"), P(1.06, 100_000, "c"),
             P(1.065, 400_000, "d"), P(1.067, 800_000, "e")]
    picked = [p.who for p in _spread(front, 3)]
    assert picked[0] == "a" and picked[-1] == "e", "the ends bracket the trade-off"
    assert picked[1] == "c", "the middle pick is the point farthest in log cost from both ends"


def test_spread_always_keeps_what_it_is_told_and_never_invents_points():
    front = [P(1.04, 32_000, "a"), P(1.05, 40_000, "decision"), P(1.06, 100_000, "c"), P(1.067, 800_000, "e")]
    picked = _spread(front, 2, keep=[front[1]])
    assert front[1] in picked and len(picked) == 2
    assert len(_spread(front[:2], 6)) == 2


def test_best_within_a_budget_is_the_fastest_that_fits():
    pts = [P(1.0439, 35_096, "incumbent"), P(1.0626, 97_208, "b"), P(1.0671, 206_496, "c")]
    best = lambda budget: best_within(pts, budget, better=lambda p: p.speed, cost=lambda p: p.cost)  # noqa: E731
    assert best(100_000).who == "b" and best(None).who == "c" and best(1_000) is None


# ---- in the loop, on the toy document -----------------------------------------------------

def test_a_shrink_under_a_relative_floor_refuses_and_the_goal_keeps_a_share_of_the_gain():
    """Climb the table, then shrink bytes holding 90% of the gain over 1.0: the table cannot
    halve (under the floor, refused, never ranked), the ways can, and the decision is the least
    bytes within 90% of the best's gain."""
    from toy_document import run_toy, toy

    flow = [{"name": "climb", "knobs": ["table"], "reach": "any", "wave": 8, "patience": 1, "budget": 8},
            {"name": "shrink", "metric": "bytes", "direction": "minimize", "knobs": ["table", "ways"],
             "floor": {"metric": "speedup", "keep": 0.9, "above": 1.0}, "wave": 8, "patience": 2, "budget": 24}]
    _prob, out = run_toy(toy(flow={"orchestrate": flow}, objectives=[
        {"metric": "speedup", "direction": "maximize", "keep": 0.9, "above": 1.0},
        {"metric": "bytes", "direction": "minimize"}]))
    assert any(line.startswith("[shrink] bytes 32768 -> 8192") for line in out.lessons), out.lessons
    assert out.refused and all("shrink: below the floor" in why for _n, why in out.refused)
    best = max(s.metrics["speedup"] for s in out.scored)
    assert out.decision.metrics["bytes"] == 8192 and out.decision.metrics["speedup"] >= 1 + 0.9 * (best - 1)
    assert "the least bytes at speedup >=" in out.decided_by


def test_finalists_are_spread_along_the_frontier_not_the_top_by_speed():
    from toy_document import STAGE, run_toy, toy

    doc = toy(stages=[{"name": "screen", "command": STAGE, "metrics": ["speedup", "bytes"]},
                      {"name": "confirm", "command": STAGE, "metrics": ["speedup", "bytes"]}],
              flow={"orchestrate": [{"name": "climb", "reach": "any", "wave": 6, "budget": 18, "patience": 3}]})
    _prob, out = run_toy(doc, finalists=3, screen_only=False)
    screened = [s for s in out.scored if s.stage == "screen"]
    front = frontier(screened, better=lambda s: s.metrics["speedup"], cost=lambda s: s.metrics["bytes"])
    assert len(front) > 3 and max(screened, key=lambda s: s.metrics["speedup"]) is front[-1]
    confirmed = {s.candidate.key() for s in out.confirmed}
    assert len(out.confirmed) == 3 and out.decision.stage == "confirm"
    assert {front[0].candidate.key(), front[-1].candidate.key()} <= confirmed, "both ends of the trade-off"
