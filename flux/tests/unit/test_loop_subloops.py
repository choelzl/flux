"""A part whose generator is another loop (docs/decisions.md D455).

Cedric's second drawing: a composition orchestrator over sub-loops, each with its own gate, its
own stages and its own record, whose answers the parent composes. Both halves of the division are
supported on purpose ("the user can specify the division or the LLM-orchestrator can decide"):
a problem may DECLARE its children, and an orchestrator may decide them at runtime.

What these pin: a sub-loop is run as a part and its DECISION becomes the parent's admitted piece,
the parent composes what the children decided, a child that decides nothing leaves the part
unproven and says so, the nesting is bounded, and a task document can carry the division either
way.
"""

from __future__ import annotations

import pytest
from flux_loop import (Candidate, LoopRequest, Problem, SubLoop, Verdict, run_loop)


class Leaf(Problem):
    """A whole little study: one candidate, one gate, one stage."""

    def __init__(self, name: str, *, value: float, refuse: bool = False) -> None:
        self.name = name
        self.value = value
        self.refuse = refuse
        self.runs = 0

    def objective(self, request):
        return {"study": self.name}

    def search(self, state):
        self.runs += 1
        yield [Candidate(name=f"{self.name}-design", artifact=f"<{self.name}>")]

    def build(self, cand, subgoal, state):
        return cand.artifact

    def judge(self, built, cand, subgoal, state):
        return Verdict(not self.refuse, 0.0 if not self.refuse else 1.0,
                       "" if not self.refuse else f"{self.name}: nothing works here")

    def stages(self):
        return [f"{self.name}-stage"]

    def measure(self, cand, stage, state):
        return {"value": self.value}


class Composition(Problem):
    """The drawing's parent: it declares two sub-loops and joins what they decided."""

    name = "composition"

    def __init__(self, children: list[SubLoop]) -> None:
        self.children = children

    def objective(self, request):
        return {"study": "composition"}

    def subproblems(self, state):
        return list(self.children)

    def compose(self, admitted, state):
        names = [c.name for c in self.children]
        if any(n not in admitted for n in names):
            return None
        return Candidate(name="composed",
                         artifact="".join(admitted[n].artifact for n in names))

    def build(self, cand, subgoal, state):
        return cand.artifact

    def judge(self, built, cand, subgoal, state):
        return Verdict(True, 0.0)

    def stages(self):
        return ["joint"]

    def measure(self, cand, stage, state):
        return {"pieces": float(cand.artifact.count("<"))}


def _children(**kw):
    return [SubLoop(name="sim", problem=Leaf("sim", value=10.0), statement="model it"),
            SubLoop(name="arch", problem=Leaf("arch", value=20.0, **kw), statement="build it")]


def test_a_declared_sub_loop_runs_and_its_decision_becomes_the_parents_part(tmp_path):
    kids = _children()
    problem = Composition(kids)
    out = run_loop(problem, LoopRequest(db=str(tmp_path / "c.db"), steps=4),
                   log=lambda _m: None)
    assert all(k.problem.runs == 1 for k in kids), "each child ran its own loop once"
    assert sorted(out.admitted) == ["arch", "sim"]
    assert out.admitted["sim"].name == "sim-design"
    assert out.decision is not None and out.decision.stage == "joint"
    assert out.decision.metrics["pieces"] == 2.0, "the parent measured what it composed"


def test_a_child_that_decides_nothing_leaves_the_part_unproven(tmp_path):
    kids = _children(refuse=True)
    out = run_loop(Composition(kids), LoopRequest(db=str(tmp_path / "c.db"), steps=4),
                   log=lambda _m: None)
    assert list(out.admitted) == ["sim"], "the refused child admitted nothing"
    assert out.decision is None, "the parent could not compose, so there was nothing to measure"
    assert any("1 part(s) not yet proven: arch" in n for n in out.not_established)
    assert any(name.startswith("arch: ") for name, _why in out.refused), (
        "the child's own refusal travels with the parent's report"


    )


def test_a_childs_lessons_and_limits_arrive_named(tmp_path):
    kids = _children()
    out = run_loop(Composition(kids), LoopRequest(db=str(tmp_path / "c.db"), steps=4),
                   log=lambda _m: None)
    assert any(l.startswith("[sim] ") for l in out.lessons), out.lessons
    assert any(l.startswith("[arch] ") for l in out.lessons)


def test_the_orchestrator_can_decide_the_division_at_runtime(tmp_path):
    """The other half of the answer: the same work item, decided rather than declared."""
    asked: list[str] = []

    class Splitting(Composition):
        def subproblems(self, state):
            return []

        def decompose(self, state, critique=None):
            asked.append("decompose")
            # a model would answer here; what matters is that the items are sub-loops
            return [SubLoop(name=c.name, problem=c.problem, statement=c.statement)
                    for c in self.children]

    kids = _children()
    out = run_loop(Splitting(kids), LoopRequest(db=str(tmp_path / "c.db"), steps=4,
                                                critique_rounds=0), log=lambda _m: None)
    assert asked == ["decompose"]
    assert sorted(out.admitted) == ["arch", "sim"] and out.decision is not None


def test_the_nesting_is_bounded(tmp_path):
    """A loop that returns itself as its own sub-task stops at `max_depth` instead of forever."""

    class Recursive(Problem):
        name = "recursive"

        def objective(self, request):
            return {"study": "recursive"}

        def subproblems(self, state):
            return [SubLoop(name="again", problem=Recursive(), statement="the same thing")]

        def build(self, cand, subgoal, state):
            return cand.artifact

        def judge(self, built, cand, subgoal, state):
            return Verdict(True, 0.0)

    out = run_loop(Recursive(), LoopRequest(steps=2, max_depth=2), log=lambda _m: None)
    assert any("max_depth is 2" in n for n in out.not_established)


def test_the_record_links_what_each_sub_loop_decided(tmp_path):
    db = str(tmp_path / "c.db")
    problem = Composition(_children())
    run_loop(problem, LoopRequest(db=db, steps=4), log=lambda _m: None)
    from flux_records import Records

    parent = Records(db, objective={"study": "composition"})
    linked = parent.recall("subloop")
    assert [c["name"] for c in linked] == ["sim", "arch"]
    assert linked[0]["decision"] == "sim-design" and linked[0]["stage"] == "sim-stage"
    # each child kept its own campaign in the same store, resumable on its own
    child = Records(db, objective={"study": "sim"})
    assert child.resumed and child.known(stage="sim-stage", metric="value")


def test_a_task_document_can_carry_the_division(tmp_path):
    """The user's half, in document form: sub-tasks are nested documents that inherit what they
    do not say, and never inherit `subtasks` (which would divide again, forever)."""
    from flux_loop import PromptProblem, TaskError, TaskSpec

    spec = TaskSpec.from_dict({
        "id": "top", "statement": "make a thing", "contract": "be careful",
        "gate": {"test": ["true"]},
        "stages": [{"name": "screen", "command": ["true"], "metrics_re": {"m": r"m=(\d+)"}}],
        "subtasks": [{"id": "sim", "statement": "model it"},
                     {"id": "arch", "statement": "build it", "gate": {"test": ["false"]}}],
    })
    assert [c.id for c in spec.subtasks] == ["sim", "arch"]
    assert spec.subtasks[0].contract == "be careful" and spec.subtasks[0].gate.test == ("true",)
    assert spec.subtasks[1].gate.test == ("false",), "a child may say its own gate"
    assert [r.name for r in spec.subtasks[0].stages] == ["screen"]
    assert spec.subtasks[0].subtasks == ()
    problem = PromptProblem(spec)
    work = problem.decompose(None)
    assert [w.name for w in work] == ["sim", "arch"]
    assert all(isinstance(w, SubLoop) for w in work)
    with pytest.raises(TaskError, match="cannot both"):
        TaskSpec.from_dict({"id": "t", "statement": "s", "gate": {"test": ["true"]},
                            "parts": "decompose", "subtasks": "decompose"})


def test_a_document_can_ask_the_orchestrator_to_split_it():
    """`"subtasks": "decompose"`: the children inherit everything but the statement, which is
    what a sub-task is, and the division is remembered so a resume works on the same one."""
    from flux_loop import LoopState, PromptProblem, TaskSpec
    from flux_llm import ScriptedProposer

    spec = TaskSpec.from_dict({
        "id": "top", "statement": "make a thing", "gate": {"test": ["true"]},
        "subtasks": "decompose", "max_subtasks": 3,
    })
    problem = PromptProblem(spec)
    proposer = ScriptedProposer(['{"subtasks": [{"name": "front", "statement": "the front"},'
                                 ' {"name": "back", "statement": "the back"}], "why": "two ends"}'])
    state = LoopState(request=LoopRequest(), say=lambda _m: None, proposer=proposer,
                      feedback=None)
    work = problem.decompose(state)
    assert [w.name for w in work] == ["front", "back"]
    assert work[0].statement == "the front"
    child = work[0].problem.task
    assert child.id == "top/front" and child.gate.test == ("true",) and not child.split
    assert "1 to 3 SUB-TASKS" in proposer.prompts[0]
    again = problem.decompose(state)
    assert [w.name for w in again] == ["front", "back"] and len(proposer.prompts) == 1, (
        "asked once: the division a pass works on does not change under it")
