"""Generation is a sub-loop, and WHO drafts is a role (docs/decisions.md D456).

Cedric's drawing again: "Generation can be considered a sub-loop: iterate on generate/edit/test.
With again LLM (generation) or no LLM (template, pre-existing design, ...)". So the loop owns the
draft/build/fast-check iteration and `Problem.generator` names the source -- model, template,
catalog, solver -- without the surrounding loop changing.

What these pin: the shared sub-loop iterates against the fast check and feeds the failure back,
each source kind works, a source that cannot do better is stopped rather than left to spend the
whole budget, a source that raises is a refusal, the model stays the default, and a task document
can declare a generator that needs no model at all.
"""

from __future__ import annotations

import json

import pytest
from flux_loop import (Attempt, Candidate, Catalog, LoopRequest, Model, Problem, PromptProblem,
                       Solver, TaskError, TaskSpec, Template, Verdict, run_loop)


class Wanted(Problem):
    """One part, whose fast check wants the artifact to be exactly "good"."""

    name = "wanted"

    def __init__(self, source) -> None:
        self.source = source
        self.checks: list[str] = []

    def objective(self, request):
        return {"study": "wanted"}

    def subgoals(self):
        return ["piece"]

    def generator(self, subgoal, state):
        return self.source

    def build(self, cand, subgoal, state):
        return cand.artifact

    def fast_check(self, built, cand, subgoal, state):
        self.checks.append(built)
        return (0, "") if built == "good" else (1, f"wanted 'good', got {built!r}")

    def judge(self, built, cand, subgoal, state):
        return Verdict(built == "good", 0.0 if built == "good" else 1.0)

    def compose(self, admitted, state):
        return admitted.get("piece")

    def stages(self):
        return ["stage"]

    def measure(self, cand, stage, state):
        return {"value": float(len(cand.artifact))}


def _run(problem, tmp_path, **kw):
    kw.setdefault("steps", 2)
    return run_loop(problem, LoopRequest(db=str(tmp_path / "g.db"), prototype=False,
                                         compute=False, critique_rounds=0, **kw),
                    proposer=None, log=lambda _m: None)


def test_a_template_drafts_with_no_model_in_the_loop(tmp_path):
    drafts: list[Attempt] = []

    def render(attempt):
        drafts.append(attempt)
        return Candidate(name="rendered", artifact="good")

    problem = Wanted(Template(render))
    out = _run(problem, tmp_path)
    assert [a.index for a in drafts] == [0], "one draft was enough"
    assert out.decision is not None and out.decision.metrics["value"] == 4.0
    assert problem.checks == ["good"], "the framework's code went through the same fast check"


def test_the_failure_goes_back_to_the_source_and_the_next_draft_uses_it(tmp_path):
    seen: list[str] = []

    def render(attempt):
        seen.append(attempt.failure)
        return Candidate(name=f"d{attempt.index}",
                         artifact="good" if "wanted 'good'" in attempt.failure else "bad")

    out = _run(Wanted(Template(render)), tmp_path, repair_attempts=2)
    assert seen[0] == "", "the first draft has nothing to go on"
    assert "wanted 'good'" in seen[1], "the second was told what the fast check said"
    assert out.decision is not None, "the repaired draft was admitted and measured"


def test_a_source_that_cannot_do_better_is_stopped_rather_than_repeated(tmp_path):
    """A renderer that ignores the failure produces the same text forever; spending the whole
    repair budget on it teaches nothing and the report should say which it was."""
    calls = []

    def render(attempt):
        calls.append(attempt.index)
        return Candidate(name="same", artifact="bad")

    out = _run(Wanted(Template(render)), tmp_path, steps=1, repair_attempts=5)
    assert calls == [0, 1], "stopped at the repeat, not after six drafts"
    assert any("the same design again" in why for _name, why in out.refused), out.refused


def test_two_drafts_that_differ_only_in_their_knobs_are_two_designs(tmp_path):
    """A problem whose candidates are points, not text: every artifact is "" and the repeat
    guard must not mistake the second point for the first."""
    seen: list[int] = []

    class Points(Wanted):
        def fast_check(self, built, cand, subgoal, state):
            seen.append(cand.knobs["banks"])
            return (0, "") if cand.knobs["banks"] == 8 else (1, "too few banks")

        def judge(self, built, cand, subgoal, state):
            return Verdict(cand.knobs["banks"] == 8, 0.0)

        def measure(self, cand, stage, state):
            return {"value": float(cand.knobs["banks"])}

    def render(attempt):
        return Candidate(name=f"p{attempt.index}", artifact="",
                         knobs={"banks": 4 + 4 * attempt.index})

    out = _run(Points(Template(render)), tmp_path, steps=1, repair_attempts=2)
    assert seen == [4, 8], "the second point was tried, not refused as a repeat"
    assert out.decision is not None


def test_a_catalog_tries_the_designs_that_already_exist_in_order(tmp_path):
    made: list[str] = []

    def make(item, attempt):
        made.append(item)
        return Candidate(name=item, artifact=item)

    out = _run(Wanted(Catalog(["bad", "good"], make)), tmp_path, repair_attempts=2)
    assert made == ["bad", "good"], "the next shipped design after the first failed"
    assert out.decision is not None and out.decision.candidate.name == "good"


def test_an_exhausted_catalog_is_a_refusal_with_its_reason(tmp_path):
    out = _run(Wanted(Catalog(["bad", "worse"], lambda i, a: Candidate(name=i, artifact=i))),
               tmp_path, repair_attempts=4)
    assert out.decision is None
    assert any("the catalog holds 2 design(s)" in why for _name, why in out.refused), out.refused


def test_a_solver_is_told_the_counter_example(tmp_path):
    """The bankmap chain's shape: the failure of the last candidate is the next constraint."""
    forbidden: list[str] = []

    def solve(attempt):
        if attempt.failure:
            forbidden.append(attempt.prior.artifact)
        return Candidate(name=f"s{attempt.index}",
                         artifact="good" if forbidden else "bad")

    out = _run(Wanted(Solver(solve)), tmp_path, repair_attempts=2)
    assert forbidden == ["bad"], "the solver saw which candidate was refused"
    assert out.decision is not None


def test_a_source_that_raises_is_a_refusal_not_a_crash(tmp_path):
    def render(attempt):
        raise RuntimeError("the renderer is broken")

    out = _run(Wanted(Template(render)), tmp_path)
    assert out.decision is None
    assert any("the renderer is broken" in why for _name, why in out.refused), out.refused


def test_the_model_stays_the_default_and_can_be_named(tmp_path):
    asked: list[str] = []

    class Modelled(Wanted):
        def design_prompt(self, subgoal, method, state, human, prior, prior_why):
            asked.append(subgoal or "*")
            return "write it", None

        def parse_design(self, reply, subgoal):
            return Candidate(name="from-model", artifact=reply.strip()), ""

    problem = Modelled(Model())
    out = run_loop(problem, LoopRequest(db=str(tmp_path / "m.db"), steps=2, prototype=False,
                                        compute=False, critique_rounds=0, patching=False),
                   proposer=lambda _p: "good", log=lambda _m: None)
    assert asked == ["piece"], "naming the model source keeps the model inner loop"
    assert out.decision is not None
    assert Modelled(None).generator("piece", None) is None, "None means the same thing"


# ------------------------------------------------------------------ the document
def _doc(**kw):
    doc = {"id": "drafted", "statement": "produce the word good", "extension": ".txt",
           "parts": [{"name": "piece", "statement": "the word"}],
           "gate": {"test": ["grep", "-q", "good", "{artifact}"]},
           "stages": [{"name": "size", "command": ["wc", "-c", "{artifact}"],
                      "metrics_re": {"bytes": r"(\d+)"}}],
           "objectives": [{"metric": "bytes", "direction": "minimize"}]}
    doc.update(kw)
    return doc


def test_a_document_can_declare_a_generator_command_and_run_with_no_model(tmp_path):
    script = tmp_path / "gen.py"
    script.write_text("import sys; open(sys.argv[1], 'w').write('good\\n')\n")
    task = TaskSpec.from_dict(_doc(generator={"command": ["{python}", str(script), "{artifact}"]}))
    out = run_loop(PromptProblem(task),
                   LoopRequest(db=str(tmp_path / "d.db"), steps=2, prototype=False,
                               compute=False, critique_rounds=0),
                   proposer=None, log=lambda _m: None)
    assert out.decision is not None, out.not_established
    assert out.decision.candidate.artifact.strip() == "good", "the script's file is the candidate"
    assert out.admitted["piece"].knobs["generator"] == "command", (
        "the drafted part remembers that no model wrote it")


def test_a_generator_command_that_writes_nothing_says_so(tmp_path):
    task = TaskSpec.from_dict(_doc(generator={"command": ["true"]}))
    out = run_loop(PromptProblem(task),
                   LoopRequest(db=str(tmp_path / "d.db"), steps=1, prototype=False,
                               compute=False, critique_rounds=0),
                   proposer=None, log=lambda _m: None)
    assert out.decision is None
    assert any("wrote no draft-" in why for _name, why in out.refused), out.refused


def test_a_document_can_point_at_designs_that_already_exist(tmp_path):
    (tmp_path / "old.txt").write_text("bad\n")
    (tmp_path / "shipped.txt").write_text("good\n")
    task = TaskSpec.from_dict(_doc(generator={"catalog": [str(tmp_path / "old.txt"),
                                                          str(tmp_path / "shipped.txt")]}))
    out = run_loop(PromptProblem(task),
                   LoopRequest(db=str(tmp_path / "d.db"), steps=2, prototype=False,
                               compute=False, critique_rounds=0, repair_attempts=2),
                   proposer=None, log=lambda _m: None)
    assert out.decision is not None, out.not_established
    assert out.decision.candidate.artifact.strip() == "good", "the second shipped design"


def test_a_generator_the_document_cannot_mean_is_a_load_error():
    with pytest.raises(TaskError, match="exactly one of `command`"):
        TaskSpec.from_dict(_doc(generator={"command": ["true"], "catalog": ["a.txt"]}))
    with pytest.raises(TaskError, match="only \"model\""):
        TaskSpec.from_dict(_doc(generator="template"))
    with pytest.raises(TaskError, match="list of strings"):
        TaskSpec.from_dict(_doc(generator={"command": "true"}))
    assert TaskSpec.from_dict(_doc(generator="model")).generator == {}, "the default, said out loud"


def test_a_sub_task_inherits_who_drafts():
    parent = _doc(generator={"command": ["true"]})
    parent.pop("parts")
    parent["subtasks"] = [{"id": "child", "statement": "the child's own artifact"}]
    task = TaskSpec.from_dict(parent)
    assert task.subtasks[0].generator == {"command": ["true"]}
    assert json.loads(json.dumps(task.to_dict()))["generator"] == {"command": ["true"]}
