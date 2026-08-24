"""The DSE box as orchestrators anyone can select (docs/decisions.md D465).

Cedric's drawing has a DSE box feeding the orchestrator -- montecarlo, gradient, genetic, tree
search. Those policies existed here only as study code, so they could not be switched on for
anything else. Given a DECLARED SPACE they are generic: `sweep` proves the answer, `montecarlo`
samples it reproducibly, `anneal` walks it and needs the numbers of the batch it just proposed.

What these pin: a policy proposes points and the problem turns a point into something buildable,
a space nothing declared is a loud refusal rather than an empty search, a sweep is exhaustive and
batched, montecarlo is bounded and repeatable and never proposes the same point twice, annealing
reads the results it was handed and needs to be told which way is better, and each policy names
itself in the record.
"""

from __future__ import annotations

import pytest
from flux_loop import (Anneal, Candidate, LoopRequest, MonteCarlo, Problem, Sweep, Verdict,
                       available_roles, make_role, neighbour, points, rig, run_loop)


class Knobs(Problem):
    """Two knobs, one metric, and a build that turns a point into the thing measured."""

    name = "knobs"

    def __init__(self, roles=None) -> None:
        self._roles = roles
        self.built: list[dict] = []

    def objective(self, request):
        return {"study": "knobs"}

    def space(self):
        return {"width": [8, 16, 32], "banks": [2, 4]}

    def build(self, cand, subgoal, state):
        self.built.append(dict(cand.knobs))
        return f"w{cand.knobs['width']}b{cand.knobs['banks']}"

    def judge(self, built, cand, subgoal, state):
        return Verdict(True, 0.0)

    def stages(self):
        return ["served"]

    def measure(self, cand, stage, state):
        # a ridge: 16 wide with 4 banks is the best point
        w, b = float(cand.knobs["width"]), float(cand.knobs["banks"])
        return {"served": 100.0 - abs(w - 16.0) - 10.0 * abs(b - 4.0)}

    def decide(self, pool, state):
        best = max(pool, key=lambda s: s.metrics["served"])
        return best, "the most served"


def _request(tmp_path, **kw):
    kw.setdefault("steps", 8)
    return LoopRequest(db=str(tmp_path / "d.db"), prototype=False, compute=False,
                       critique_rounds=0, **kw)


def test_the_three_policies_are_selectable():
    assert available_roles("orchestrator") == ["anneal", "given", "llm", "montecarlo",
                                               "rules", "sweep"]
    with pytest.raises(ValueError, match="takes"):
        make_role("orchestrator", {"montecarlo": {"sampels": 4}})
    with pytest.raises(ValueError, match="needs the metric it anneals on"):
        list(make_role("orchestrator", "anneal").search(Knobs(), None))


def test_a_space_nothing_declared_is_a_loud_refusal(tmp_path):
    class Blank(Knobs):
        def space(self):
            return {}

    with pytest.raises(ValueError, match="declares no space"):
        run_loop(Blank(rig(orchestrator="sweep")), _request(tmp_path), log=lambda _m: None)

    class Broken(Knobs):
        def space(self):
            return {"width": []}

    with pytest.raises(ValueError, match="non-empty list of choices"):
        run_loop(Broken(rig(orchestrator="sweep")), _request(tmp_path), log=lambda _m: None)


def test_a_sweep_measures_every_point_in_batches(tmp_path):
    problem = Knobs(rig(orchestrator={"sweep": {"batch": 2}}))
    out = run_loop(problem, _request(tmp_path), log=lambda _m: None)
    assert len(problem.built) == 6, "3 widths x 2 bank counts"
    assert out.decision is not None and out.decision.candidate.knobs == {"width": 16, "banks": 4}
    assert {s.candidate.meta["strategy"] for s in out.scored} == {"sweep"}


def test_montecarlo_is_bounded_reproducible_and_never_repeats_a_point(tmp_path):
    first = Knobs(rig(orchestrator={"montecarlo": {"samples": 4, "seed": 7, "batch": 2}}))
    run_loop(first, _request(tmp_path), log=lambda _m: None)
    assert len(first.built) == 4, "the sample bounds the search, not the space"
    keys = {tuple(sorted(p.items())) for p in first.built}
    assert len(keys) == 4, "no point twice"

    again = Knobs(rig(orchestrator={"montecarlo": {"samples": 4, "seed": 7, "batch": 2}}))
    run_loop(again, _request(tmp_path), log=lambda _m: None)
    assert again.built == first.built, "the same seed draws the same sample"

    other = Knobs(rig(orchestrator={"montecarlo": {"samples": 4, "seed": 8, "batch": 2}}))
    run_loop(other, _request(tmp_path), log=lambda _m: None)
    assert other.built != first.built, "a different seed is a different sample"


def test_montecarlo_stops_when_the_space_runs_out(tmp_path):
    problem = Knobs(rig(orchestrator={"montecarlo": {"samples": 50, "seed": 1}}))
    run_loop(problem, _request(tmp_path, steps=30), log=lambda _m: None)
    assert len(problem.built) == 6, "a space of six points cannot yield fifty samples"


def test_annealing_reads_the_numbers_it_was_handed(tmp_path):
    """The point of the loop handing results back to a generator: the walk needs them."""
    problem = Knobs(rig(orchestrator={"anneal": {"metric": "served", "seed": 4,
                                                 "temperature": 0.2}}))
    said: list[str] = []
    out = run_loop(problem, _request(tmp_path, steps=6), log=said.append)
    assert 5 <= len(problem.built) <= 6, "one neighbour per step, until the space runs out"
    assert any("every neighbour of" in m for m in said), (
        "and it says so rather than proposing something it has already measured")
    steps = [tuple(sorted(p.items())) for p in problem.built]
    assert len(set(steps)) == len(steps), "it never re-measures a point it has already walked"
    for a, b in zip(problem.built, problem.built[1:]):
        changed = [k for k in a if a[k] != b[k]]
        assert len(changed) <= 1, "a move changes one knob"
    assert out.decision is not None


def test_annealing_needs_to_be_told_which_way_is_better(tmp_path):
    """Minimising is not guessable, so it is declared -- and it changes the walk."""
    up = Knobs(rig(orchestrator={"anneal": {"metric": "served", "seed": 2}}))
    run_loop(up, _request(tmp_path, steps=4), log=lambda _m: None)
    down = Knobs(rig(orchestrator={"anneal": {"metric": "served", "minimize": True,
                                              "seed": 2}}))
    run_loop(down, _request(tmp_path, steps=4), log=lambda _m: None)
    assert up.built != down.built


def test_the_move_generator_is_adjacency_in_the_declared_order():
    import random

    space = {"width": [8, 16, 32]}
    rng = random.Random(0)
    got = {neighbour(space, {"width": 16}, rng)["width"] for _ in range(20)}
    assert got <= {8, 32}, "one step either way, never a jump across the range"
    edge = {neighbour(space, {"width": 8}, rng)["width"] for _ in range(20)}
    assert edge <= {8, 16}, "and the ends of the range stay inside it"
    assert len(points(space)) == 3


def test_a_policy_can_also_be_built_in_code(tmp_path):
    from flux_loop import Roles

    problem = Knobs(Roles(orchestrator=Sweep(batch=6)))
    run_loop(problem, _request(tmp_path), log=lambda _m: None)
    assert len(problem.built) == 6
    assert isinstance(make_role("orchestrator", "montecarlo"), MonteCarlo)
    assert isinstance(make_role("orchestrator", {"anneal": {"metric": "x"}}), Anneal)
