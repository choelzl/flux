"""The DSE box (D553): policies over a declared space, as orchestrators a document names."""

from __future__ import annotations

import pytest

from flux_loop import LoopRequest, LoopState, Problem, Verdict, run_loop
from flux_loop.dse import Anneal, Genetic, Gradient, ModelSearch, MonteCarlo, Pareto, Phases, Sweep, neighbour, neighbours, points

#: D738: these tests measure what one pass does with a whole search; one design a pass is the default
WHOLE = 10_000

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
    out = run_loop(prob, LoopRequest(batch=WHOLE, steps=steps, finalists=0, screen_only=True), proposer=None, log=lambda _m: None)
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
    state = LoopState(request=LoopRequest(batch=WHOLE), say=said.append, proposer=None, feedback=None)

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
    from flux_loop.document import describe_flow

    doc = {"id": "grid",
           "statement": "a grid",
           "objectives": [{"metric": "cost", "direction": "minimize"}],
           "flow": {"orchestrate": {"montecarlo": {"samples": 4, "seed": 1}, "space": {"x": [1, 2, 3], "y": ["a", "b"]}},
                    "test": {"test": ["true"]}}}
    task = TaskSpec.from_dict(doc)
    assert task.space == {"x": [1, 2, 3], "y": ["a", "b"]}
    prob = PromptProblem(task)
    assert prob.roles().orchestrator.name == "montecarlo" and prob.roles().orchestrator.samples == 4
    assert any(line.startswith("dse: montecarlo {'samples': 4, 'seed': 1} over 6 point(s): x[3] x y[2]")
               for line in describe_flow(task, prob))
    assert prob.instantiate(points(task.space)[:2], None)[1].name == "x=1-y=b"
    with pytest.raises(TaskError, match="available: agent, anneal, command, control, genetic"):
        TaskSpec.from_dict({**doc, "flow": {**doc.get("flow", {}), "orchestrate": "hillclimb"}})
    with pytest.raises(TaskError, match="space.y: a non-empty list"):
        TaskSpec.from_dict({**doc, "flow": {**doc.get("flow", {}), "orchestrate": {"space": {"x": [1], "y": []}}}})


def test_the_model_names_the_next_points_and_bad_ones_are_dropped():
    """`flow: {orchestrate: {by: model}}`: the model names new points each round; out-of-space or repeated points
    are dropped and said; an unusable round is retried once, then the walk ends (D554)."""
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
    out = run_loop(prob, LoopRequest(batch=WHOLE, steps=10, finalists=0, screen_only=True), proposer=proposer, log=said.append)
    assert prob.measured[:2] == [{"x": 0, "y": 0}, {"x": 5, "y": 3}] and prob.measured[2:] == [{"x": 3, "y": 2}]
    assert out.decision.candidate.knobs == {"x": 3, "y": 2} and len(proposer.prompts) == 4
    assert "DESIGN-SPACE EXPLORATION" in proposer.prompts[0] and "MEASURED SO FAR: nothing" in proposer.prompts[0]
    assert '{"x": 3, "y": 2} -> cost 0' in proposer.prompts[2] and "LAST ROUND:" in proposer.prompts[3]
    assert any("2 point(s) -- the corners first; 1 dropped" in m for m in said)
    assert any("is measured already" in m for m in said)


def test_dse_llm_is_the_documents_word_for_the_model_policy():
    from flux_loop import PromptProblem, TaskSpec
    from flux_loop.document import describe_flow

    doc = {"id": "g",
           "statement": "g",
           "objectives": [{"metric": "cost", "direction": "minimize"}],
           "flow": {"orchestrate": {"by": "model", "batch_size": 3, "space": {"x": [1, 2]}}, "test": {"test": ["true"]}}}
    prob = PromptProblem(TaskSpec.from_dict(doc))
    assert isinstance(prob.roles().orchestrator, ModelSearch) and prob.roles().orchestrator.batch_size == 3
    assert any(line.startswith("dse: model {'batch_size': 3} over 2 point(s)") for line in describe_flow(prob.task, prob))
    said = []
    state = LoopState(request=LoopRequest(batch=WHOLE), say=said.append, proposer=None, feedback=None)
    assert list(prob.search(state)) == [] and any("this run has none" in m for m in said)


# ---- phases, floors, margins, seeds, moves, control, pareto (D583) ---------------------------

GRID = {"x": list(range(8)), "y": list(range(8))}


class Two(Problem):
    """speed = x + y (maximise), size = 2x + y (minimise): a speed/size trade on an 8x8 grid."""

    name = "two"

    def __init__(self, policy, seeds=(), moves=None):
        self.policy, self._seeds, self._moves = policy, [dict(p) for p in seeds], moves
        self.measured: list[dict] = []
        self.state = None

    def roles(self):
        from flux_loop.roles import Roles

        return Roles(orchestrator=self.policy)

    def space(self, state):
        return GRID

    def seeds(self, state):
        return list(self._seeds)

    def moves(self, point, phase, state):
        return self._moves(point, phase) if self._moves else None

    def objectives(self):
        from flux_loop import Objectives
        from flux_loop.objective import Objective

        return Objectives([Objective("speed", "maximize"), Objective("size", "minimize")])

    def build(self, cand, subgoal, state):
        return cand.knobs

    def judge(self, built, cand, subgoal, state):
        return Verdict(True, 0.0)

    def stages(self):
        return ["eval"]

    def measure(self, cand, stage, state):
        self.state = state
        self.measured.append(dict(cand.knobs))
        x, y = cand.knobs["x"], cand.knobs["y"]
        return {"speed": x + y, "size": 2 * x + y}

    def frontier(self, scored, state):
        return list(scored)

    def finalists(self, front, state, stage=""):
        return []


def _two(policy, **kw):
    prob = Two(policy, **kw)
    out = run_loop(prob, LoopRequest(batch=WHOLE, steps=60, finalists=0, screen_only=True), proposer=None, log=lambda _m: None)
    return prob, out


def test_a_wave_is_round_robin_across_knobs_and_patience_and_budget_end_the_walk():
    from flux_loop.dse import around

    assert around(GRID, {"x": 3, "y": 3}, ["x", "y"], "any")[:4] == [{"x": 4, "y": 3}, {"x": 3, "y": 4},
                                                                  {"x": 2, "y": 3}, {"x": 3, "y": 2}]
    prob, _ = _two(Gradient(wave=2, budget=6, patience=2, steps=50), seeds=[{"x": 0, "y": 0}])
    assert len(prob.measured) == 1 + 6, "the seed, then a budget of six"
    assert {tuple(p) for p in [sorted(m) for m in prob.measured[1:3]]} == {("x", "y")}
    assert {(m["x"], m["y"]) for m in prob.measured[1:3]} == {(1, 0), (0, 1)}, "one move per knob in the first wave"


def test_phases_run_in_order_from_the_last_incumbent_and_a_relative_floor_refuses():
    """Speed first over x, then y, then shrink holding half the speed gained: the walk ends on
    the smallest design above the floor, and designs under it are refused, never ranked."""
    phases = Phases(phases=(
        {"name": "fast-x", "knobs": ["x"], "wave": 2, "patience": 1},
        {"name": "fast-y", "hold": ["x"], "wave": 2, "patience": 1},
        {"name": "shrink", "metric": "size", "direction": "minimize", "floor": {"metric": "speed", "keep": 0.5, "above": 0},
         "wave": 4, "patience": 2, "reach": "any"},
    ))
    prob, out = _two(phases, seeds=[{"x": 0, "y": 0}])
    dse = [d for d in prob.state.dse if d.get("label") != "seed"]
    assert [d["label"] for d in dse] == ["fast-x", "fast-y", "shrink"]
    assert dse[0]["end"] == {"x": 7, "y": 0} and dse[1]["start"] == {"x": 7, "y": 0} and dse[1]["end"] == {"x": 7, "y": 7}
    end = dse[2]["end"]
    assert end["x"] + end["y"] >= 7 and 2 * end["x"] + end["y"] < 21, end
    assert any("below the floor" in why for _n, why in out.refused)
    assert any(line.startswith("[fast-y] speed 7 -> 14") for line in out.lessons)
    assert all(m["y"] == 0 for m in prob.measured[1:8]), "fast-x holds y"


def test_a_margin_keeps_the_incumbent_against_a_small_gain_and_moves_and_seeds_are_the_worlds():
    def by_three(point, phase):
        return [{**point, "x": min(7, point["x"] + 3)}]           # the world's own move

    prob, _ = _two(Gradient(margin=5, patience=1), seeds=[{"x": 1, "y": 1}], moves=by_three)
    assert prob.measured == [{"x": 1, "y": 1}, {"x": 4, "y": 1}], "a gain of 3 is under the margin of 5"
    d = [d for d in prob.state.dse if d.get("label") == "gradient"][0]
    assert d["end"] == {"x": 1, "y": 1} and d["stopped"] == "1 flat step(s)"


def test_the_control_measures_the_first_seed_with_the_incumbents_kept_knobs():
    phases = Phases(phases=({"name": "climb", "wave": 0, "patience": 1}, {"policy": "control", "name": "ref", "keep": ["y"]}))
    prob, out = _two(phases, seeds=[{"x": 0, "y": 0}])
    ref = [d for d in prob.state.dse if d.get("label") == "ref"][0]
    assert ref["control"] == {"x": 0, "y": ref["end"]["y"]} and ref["control"] in prob.measured
    assert any(line.startswith("[ref] the starting design with the incumbent's y") for line in out.lessons)


def test_the_pareto_tree_spends_its_budget_and_holds_a_front():
    prob, _ = _two(Pareto(budget=12, wave=4, reach="any"), seeds=[{"x": 0, "y": 0}, {"x": 7, "y": 7}, {"x": 3, "y": 3}])
    d = [d for d in prob.state.dse if d.get("label") == "pareto"][0]
    assert d["measured"] == 12 and len(prob.measured) == 3 + 12 and d["front"]


def test_the_document_says_phases_and_a_typo_in_one_is_a_load_error():
    from flux_loop import PromptProblem, TaskError, TaskSpec

    doc = {"id": "g",
           "statement": "a grid",
           "objectives": [{"metric": "cost", "direction": "minimize"}],
           "flow": {"orchestrate": {"policy": [{"name": "a", "wave": 2}, {"policy": "llm", "knobs": ["x"], "rounds": 1}, "control"],
                            "space": {"x": [1, 2, 3]}},
                    "test": {"test": ["true"]}}}
    prob = PromptProblem(TaskSpec.from_dict(doc))
    assert prob.roles().orchestrator.name == "phases" and len(prob.roles().orchestrator.phases) == 3
    with pytest.raises(TaskError, match=r"flow.dse\[0\]: gradient: wavee is not one of its fields"):
        TaskSpec.from_dict({**doc, "flow": {**doc.get("flow", {}), "orchestrate": [{"wavee": 2}]}})
    with pytest.raises(TaskError, match=r"flow.dse\[0\]: phase policy 'hill' is not one of"):
        TaskSpec.from_dict({**doc, "flow": {**doc.get("flow", {}), "orchestrate": [{"policy": "hill"}]}})


def test_the_models_phase_moves_only_its_knobs():
    import json

    from flux_llm import ScriptedProposer

    phases = Phases(phases=({"policy": "llm", "knobs": ["x"], "rounds": 1, "batch": 2},))
    prob = Two(phases, seeds=[{"x": 0, "y": 5}])
    proposer = ScriptedProposer([json.dumps({"points": [{"x": 6}, {"x": 7}], "why": "x up"})])
    run_loop(prob, LoopRequest(batch=WHOLE, steps=10, finalists=0, screen_only=True), proposer=proposer, log=lambda _m: None)
    assert prob.measured == [{"x": 0, "y": 5}, {"x": 6, "y": 5}, {"x": 7, "y": 5}]
    assert "held at the incumbent's" in proposer.prompts[0] and '"y": 5' in proposer.prompts[0]


def test_the_trees_estimate_votes_with_the_nearest_measured_points():
    """A numeric knob's distance is its log2 step; under five points there is no estimate; a measured point is its own (D583)."""
    from flux_loop.dse import _estimate

    space = {"pht": [1024, 2048, 4096, 8192, 16384], "ft": [64, 128]}
    measured = [({"pht": p, "ft": 64}, (1.0 + i / 100, -float(p))) for i, p in enumerate(space["pht"])]
    assert _estimate({"pht": 4096, "ft": 64}, measured[:4], space) is None
    assert _estimate({"pht": 4096, "ft": 64}, measured, space) == (1.02, -4096.0)
    q = _estimate({"pht": 8192, "ft": 128}, measured, space)
    assert 1.02 < q[0] < 1.04


def test_a_resumed_search_goes_on_from_the_record(tmp_path):
    """D682: a search run again on its record starts from what the record measured -- a sweep
    that measured every point proposes none, a sampler draws new points -- instead of walking
    from the start, taking every number from the cache and calling the old answer rest."""
    db = str(tmp_path / "c.db")
    req = LoopRequest(batch=WHOLE, steps=40, finalists=0, screen_only=True, db=db)
    first = Bowl(MonteCarlo(samples=6, batch_size=3, seed=0))
    run_loop(first, req, proposer=None, log=lambda _m: None)
    again = Bowl(MonteCarlo(samples=6, batch_size=3, seed=0))
    out = run_loop(again, req, proposer=None, log=lambda _m: None)
    key = lambda ps: {tuple(sorted(p.items())) for p in ps}                 # noqa: E731
    assert len(again.measured) == 6 and not key(again.measured) & key(first.measured)
    assert len({s.candidate.key() for s in out.scored}) == 12, "the decision is over the whole record"

    db = str(tmp_path / "s.db")
    run_loop(Bowl(Sweep()), LoopRequest(batch=WHOLE, steps=40, finalists=0, screen_only=True, db=db), proposer=None, log=lambda _m: None)
    swept = Bowl(Sweep())
    out = run_loop(swept, LoopRequest(batch=WHOLE, steps=40, finalists=0, screen_only=True, db=db), proposer=None, log=lambda _m: None)
    assert swept.measured == [], "every point is on the record: nothing is walked again"
    assert out.decision is not None and out.decision.candidate.knobs == {"x": 3, "y": 2}


def test_a_search_a_command_runs_proposes_rounds_and_concludes(tmp_path):
    """D799: `orchestrate: {command: ...}` -- each round the command reads the history and its own
    state, prints the next candidates (or none: done), lessons and a conclusion; `{params}` is the
    document's params as a JSON file."""
    from flux_loop import PromptProblem, load_task, request_for, run_loop

    home = tmp_path / "words"
    home.mkdir()
    (home / "search.py").write_text(
        "import json, sys\n"
        "hist, st_path, params = json.load(open(sys.argv[1])), sys.argv[2], json.load(open(sys.argv[3]))\n"
        "try: st = json.load(open(st_path))\nexcept Exception: st = {'n': 0}\n"
        "st['n'] += 1; json.dump(st, open(st_path, 'w'))\n"
        "seen = {m['name'] for m in hist['measured']} | {r['name'] for r in hist['refused']}\n"
        "todo = [w for w in params['words'] if w not in seen]\n"
        "if not todo: print(json.dumps({'candidates': [], 'lessons': ['tried ' + ', '.join(sorted(seen))], "
        "'conclusion': {'decision': 'all tried'}}))\n"
        "else: print('thinking...'); print(json.dumps({'candidates': [{'name': todo[0], 'artifact': todo[0] + '\\n'}]}))\n")
    (home / "problem.yaml").write_text(
        "statement: the longest word the check admits\nlanguage: text\n"
        "params: {words: [ab, xyz, abcd]}\n"
        "objectives: [{metric: chars, direction: maximize}]\n"
        "flow:\n  orchestrate: {command: \"{python} {home}/search.py {history} {state} {params}\"}\n"
        "  test: \"{python} -c \\\"import sys; print(int('x' in open(sys.argv[1]).read()), 'failing')\\\" {artifact}\"\n"
        "  measure:\n    len: {command: \"{python} -c \\\"import sys; print('chars=' + str(len(open(sys.argv[1]).read().strip())))\\\" {artifact}\","
        " metrics: [chars]}\n"
        "budget: {prototype: false}\n")
    task = load_task(home)
    assert task.flow["dse"] == {"command": {"run": "{python} {home}/search.py {history} {state} {params}"}}
    assert task.to_dict()["flow"]["orchestrate"] == {"command": "{python} {home}/search.py {history} {state} {params}"}
    prob = PromptProblem(task)
    said: list[str] = []
    refused = []
    for _ in range(4):
        out = run_loop(prob, request_for(task, db=str(tmp_path / "w.db")), log=said.append)
        refused += out.refused
    assert out.decision is not None and out.decision.name == "abcd"
    assert [n for n, _ in refused] == ["xyz"], "the gate judged the command's candidate, once: a resumed record's refusals are history"
    assert any("tried ab, abcd, xyz" in line for line in out.lessons)
