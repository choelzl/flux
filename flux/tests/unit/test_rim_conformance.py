"""Every application is a document on the one loop (D519, D541).

Every `applications/<name>/` holds a `<name>.problem.yaml` that loads, names its campaign, and
declares its stages and objectives; a world, if named, is named once as `flux_<x>:World`; a
document may have no world (D579); the loop's entry has the feedback seam; and no application
carries a `demo.py` or its own `Problem` subclass.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

FLUX_ROOT = Path(__file__).resolve().parents[2]
APPLICATIONS = sorted(p.name for p in (FLUX_ROOT / "applications").iterdir() if p.is_dir())


def test_every_application_is_a_document():
    assert APPLICATIONS == ["adder16", "bankmap", "gelu_fp16", "interconnect_mapping", "macarray", "mul8", "nlu", "npu_gemm", "prefetcher", "prefetcher_invent", "primes"]
    for app in APPLICATIONS:
        doc = FLUX_ROOT / "applications" / app / "problem.yaml"
        assert doc.is_file(), f"{app}: no problem document"
        assert not (FLUX_ROOT / "applications" / app / "demo.py").exists(), f"{app}: a demo beside the document"


@pytest.mark.parametrize("app", APPLICATIONS)
def test_the_document_loads_and_names_its_campaign_and_gate(app):
    from flux_loop import PromptProblem, load_task

    task = load_task(FLUX_ROOT / "applications" / app / "problem.yaml")
    assert task.id == app
    assert task.record == app, f"{app}: the campaign is not named after the document"
    assert task.stages, f"{app}: no stages"
    problem = PromptProblem(task)
    if task.world:
        assert task.world.startswith("flux_") and task.world.endswith(":World"), f"{app}: the world is not flux_<x>:World"
        assert problem.world is not None
        bound = [n for n in problem.__dict__ if callable(problem.__dict__[n]) and not n.startswith("_")]
        assert "judge" in bound or "build" in bound, f"{app}: the world binds no gate"
    else:
        assert task.gate, f"{app}: no world and no gate command (D579)"
        assert not (FLUX_ROOT / "applications" / app / "lib").exists(), f"{app}: a world-less document with a package beside it"
    assert problem.objective(__import__("flux_loop").request_for(task, db=""))["study"] == app


def test_no_application_subclasses_the_problem_any_more():
    """A world is an object of hooks named once under `world:`; no application subclasses `Problem`."""
    for app in APPLICATIONS:
        for src in (FLUX_ROOT / "applications" / app / "lib" / "src").rglob("*.py"):
            text = src.read_text()
            assert "(Problem)" not in text.replace("(Problem)\n", "(Problem)"), f"{src}: a Problem subclass"


def test_the_loop_entry_accepts_the_operator_channel():
    from flux_loop import run_loop

    assert "feedback" in set(inspect.signature(run_loop).parameters), "no feedback seam (D398)"


def test_the_world_contract_is_small_legible_and_complete():
    """Every public method of Problem belongs to the world CONTRACT, the document or the loop,
    and every world fills only the contract (D561)."""
    from flux_loop import Problem
    from flux_loop.document import CORE, DOCUMENT_OWNED, contract_lines, loop_owned, world_hooks

    assert len(CORE) == 14 and set(CORE) <= set(world_hooks())
    public = {n for n in dir(Problem) if not n.startswith("_") and callable(getattr(Problem, n, None))}
    assert public == set(world_hooks()) | DOCUMENT_OWNED | loop_owned()
    assert not (set(world_hooks()) & DOCUMENT_OWNED) and not (set(world_hooks()) & loop_owned())
    assert {"route", "cutoff", "good_enough", "decompose", "improve"} <= loop_owned()
    lines = contract_lines({"build", "judge"})
    assert lines[4] == "test: [*build], *fast_check, [*judge], describe_failure" and lines[0].startswith("knowledge: *prepare")
    import importlib

    for app in APPLICATIONS:
        from flux_loop import load_task, PromptProblem

        task = load_task(FLUX_ROOT / "applications" / app / "problem.yaml")
        world = PromptProblem(task).world
        if world is None:
            continue                      # D579: a document with no world fills nothing
        filled = {n for n in type(world).__dict__ if callable(type(world).__dict__[n]) and not n.startswith("_")}
        assert not (filled & loop_owned()), f"{app} fills the loop's own: {filled & loop_owned()}"
        assert filled & set(CORE), f"{app} fills no core hook"
    del importlib


def test_a_world_that_takes_the_loops_own_hook_is_refused():
    from flux_loop import PromptProblem, TaskError, TaskSpec

    class Greedy:
        def __init__(self, problem):
            pass

        def build(self, cand, subgoal, state):
            return cand.artifact

        def route(self, stage, scored, state):           # the loop's, not a world's
            return []

    import sys

    sys.modules[__name__].Greedy = Greedy
    doc = {"id": "g",
           "statement": "g",
           "parts": ["a"],
           "world": __name__ + ":Greedy",
           "objectives": [{"metric": "fmax_mhz", "direction": "maximize"}],
           "flow": {"measure": {"screen": {"metrics": ["fmax_mhz"]}}}}
    with pytest.raises(TaskError, match="route is the loop's, not a world's hook"):
        PromptProblem(TaskSpec.from_dict(doc))
