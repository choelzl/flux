"""The DSE box (D553): policies over a declared space, as orchestrators a document names."""

from __future__ import annotations

import pytest

from flux_loop import LoopRequest, LoopState, Problem, Verdict, run_loop
from flux_loop.dse import Anneal, Genetic, Gradient, ModelSearch, MonteCarlo, Sweep, neighbour, neighbours, points

SPACE = {"x": [0, 1, 2, 3, 4, 5], "y": [0, 1, 2, 3]}


class Bowl(Problem):
    """cost = |x - 3| + |y - 2|, minimised; the point (3, 2) is the floor."""

    name = "bowl"

    def __init__(self, policy):
        self.policy = policy
        self.measured: list[dict] = []

    def roles(self):
        from flux_loop.roles import Roles

        return Roles(orchestrator=self.policy)

    def space(self, state):
        return SPACE

    def objectives(self):
        from flux_loop import Objectives
        from flux_loop.objective import Objective

        return Objectives([Objective("cost", "minimize")])

    def build(self, cand, subgoal, state):
        return cand.knobs

    def judge(self, built, cand, subgoal, state):
        return Verdict(True, 0.0)

    def stages(self):
        return ["eval"]

    def measure(self, cand, stage, state):
        self.measured.append(dict(cand.knobs))
        return {"cost": abs(cand.knobs["x"] - 3) + abs(cand.knobs["y"] - 2)}

    def frontier(self, scored, state):
        return list(scored)

    def finalists(self, front, state, stage=""):
        return []


def _run(policy, steps=40):
    prob = Bowl(policy)
    out = run_loop(prob, LoopRequest(steps=steps, finalists=0, screen_only=True), proposer=None, log=lambda _m: None)
    return prob, out


def test_the_grid_and_its_neighbours():
    import random

    grid = points(SPACE)
    assert len(grid) == 24 and grid[0] == {"x": 0, "y": 0} and grid[1] == {"x": 0, "y": 1}
    assert points({}) == []
    around = neighbours(SPACE, {"x": 0, "y": 3})
    assert around == [{"x": 1, "y": 3}, {"x": 0, "y": 2}]                # the ends stay inside
    rng = random.Random(1)
    for _ in range(50):
        n = neighbour(SPACE, {"x": 5, "y": 0}, rng)
        assert n in ({"x": 4, "y": 0}, {"x": 5, "y": 1})


def test_a_sweep_measures_every_point_once_in_batches():
    prob, out = _run(Sweep(batch_size=10))
    assert len(prob.measured) == 24 and len({tuple(sorted(p.items())) for p in prob.measured}) == 24
    assert out.decision is not None and out.decision.candidate.knobs == {"x": 3, "y": 2}


def test_montecarlo_samples_without_repeats_from_a_seed():
    prob, _ = _run(MonteCarlo(samples=10, batch_size=4, seed=7))
    assert len(prob.measured) == 10 and len({tuple(sorted(p.items())) for p in prob.measured}) == 10
    again, _ = _run(MonteCarlo(samples=10, batch_size=4, seed=7))
    key = lambda ps: sorted(tuple(sorted(p.items())) for p in ps)           # noqa: E731 -- measured in parallel
    assert key(again.measured) == key(prob.measured)                        # reproducible


def test_gradient_walks_down_to_the_floor_and_stops():
    prob, out = _run(Gradient(steps=20))
    assert out.decision.candidate.knobs == {"x": 3, "y": 2}
    assert len(prob.measured) < 24                                            # it did not sweep
    assert prob.measured[0] == {"x": 0, "y": 0}                               # the grid's first point starts it


def test_anneal_and_genetic_reach_the_floor_on_a_bowl():
    prob, out = _run(Anneal(steps=30, seed=3, temperature=0.5))
    assert out.decision.candidate.knobs == {"x": 3, "y": 2}
    prob, out = _run(Genetic(population=6, generations=5, seed=3))
    assert out.decision.candidate.knobs == {"x": 3, "y": 2} and len(prob.measured) <= 24


def test_a_policy_without_a_space_or_an_objective_says_so():
    said = []
    state = LoopState(request=LoopRequest(), say=said.append, proposer=None, feedback=None)

    class Flat(Bowl):
        def space(self, state):
            return {}

    assert Sweep().search(Flat(None), state) is None and any("nothing to search" in m for m in said)

    class Blind(Bowl):
        def objectives(self):
            from flux_loop import Objectives

            return Objectives()

    said.clear()
    assert list(Gradient().search(Blind(None), state)) == [] and any("declares no objective" in m for m in said)


def test_the_document_names_the_policy_on_its_dse_line():
    from flux_loop import PromptProblem, TaskError, TaskSpec
    from flux_loop.task import describe_flow

    doc = {"id": "grid", "statement": "a grid", "space": {"x": [1, 2, 3], "y": ["a", "b"]},
           "gate": {"test": ["true"]}, "flow": {"dse": {"montecarlo": {"samples": 4, "seed": 1}}},
           "objectives": [{"metric": "cost", "direction": "minimize"}]}
    task = TaskSpec.from_dict(doc)
    assert task.space == {"x": [1, 2, 3], "y": ["a", "b"]}
    prob = PromptProblem(task)
    assert prob.roles().orchestrator.name == "montecarlo" and prob.roles().orchestrator.samples == 4
    assert any(line.startswith("dse: montecarlo {'samples': 4, 'seed': 1} over 6 point(s): x[3] x y[2]")
               for line in describe_flow(task, prob))
    assert prob.instantiate(points(task.space)[:2], None)[1].name == "1-b"
    with pytest.raises(TaskError, match="registered: anneal, genetic"):
        TaskSpec.from_dict({**doc, "flow": {"dse": "hillclimb"}})
    with pytest.raises(TaskError, match="space.y: a non-empty list"):
        TaskSpec.from_dict({**doc, "space": {"x": [1], "y": []}, "flow": {}})


def test_the_model_names_the_next_points_and_bad_ones_are_dropped():
    """D554 (`flow: {dse: llm}`): each round the model reads the space, the objective and
    the measured points, and names new ones; a point outside the space or measured already
    is dropped and said; a round with nothing usable is asked once more, then the walk ends."""
    import json

    from flux_llm import ScriptedProposer

    replies = [
        json.dumps({"points": [{"x": 0, "y": 0}, {"x": "5", "y": 3}, {"x": 9, "y": 0}], "why": "the corners first"}),
        json.dumps({"points": [{"x": 0, "y": 0}, {"x": 3, "y": 2}], "why": "toward the middle"}),
        json.dumps({"points": [{"x": 3, "y": 2}]}),                    # measured already: nothing usable
        json.dumps({"points": []}),                                    # still nothing: the walk ends
    ]
    proposer = ScriptedProposer(replies)
    prob = Bowl(ModelSearch(batch_size=2, rounds=6))
    said = []
    out = run_loop(prob, LoopRequest(steps=10, finalists=0, screen_only=True), proposer=proposer, log=said.append)
    assert prob.measured[:2] == [{"x": 0, "y": 0}, {"x": 5, "y": 3}] and prob.measured[2:] == [{"x": 3, "y": 2}]
    assert out.decision.candidate.knobs == {"x": 3, "y": 2} and len(proposer.prompts) == 4
    assert "DESIGN-SPACE EXPLORATION" in proposer.prompts[0] and "MEASURED SO FAR: nothing" in proposer.prompts[0]
    assert '{"x": 3, "y": 2} -> cost 0' in proposer.prompts[2] and "LAST ROUND:" in proposer.prompts[3]
    assert any("2 point(s) -- the corners first; 1 dropped" in m for m in said)
    assert any("is measured already" in m for m in said)


def test_dse_llm_is_the_documents_word_for_the_model_policy():
    from flux_loop import PromptProblem, TaskSpec
    from flux_loop.task import describe_flow

    doc = {"id": "g", "statement": "g", "space": {"x": [1, 2]}, "gate": {"test": ["true"]},
           "objectives": [{"metric": "cost", "direction": "minimize"}], "flow": {"dse": {"llm": {"batch_size": 3}}}}
    prob = PromptProblem(TaskSpec.from_dict(doc))
    assert isinstance(prob.roles().orchestrator, ModelSearch) and prob.roles().orchestrator.batch_size == 3
    assert any(line.startswith("dse: llm {'batch_size': 3} over 2 point(s)") for line in describe_flow(prob.task, prob))
    said = []
    state = LoopState(request=LoopRequest(), say=said.append, proposer=None, feedback=None)
    assert list(prob.search(state)) == [] and any("this run has none" in m for m in said)
