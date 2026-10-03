"""A part whose generator is another loop (D455).

A sub-loop runs as a part and its decision becomes the parent's admitted piece; the parent
composes what the children decided; a child that decides nothing leaves the part unproven; nesting
is bounded; a task document can declare the division or let the orchestrator decide it.
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
    """A parent that declares two sub-loops and joins what they decided."""

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
    """The same work item, decided by the orchestrator rather than declared."""
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
    """Sub-tasks are nested documents that inherit what they do not say, never `subtasks` itself."""
    from flux_loop import PromptProblem, TaskError, TaskSpec

    spec = TaskSpec.from_dict({"id": "top",
                               "statement": "make a thing",
                               "contract": "be careful",
                               "subtasks": [{"id": "sim", "statement": "model it"}, {"id": "arch",
                                                                                     "statement": "build it",
                                                                                     "flow": {"test": {"test": ["false"]}}}],
                               "flow": {"test": {"test": ["true"]},
                                        "measure": {"screen": {"command": ["true"], "metrics_re": {"m": 'm=(\\d+)'}}}}})
    assert [c.id for c in spec.subtasks] == ["sim", "arch"]
    assert spec.subtasks[0].contract == "be careful" and spec.subtasks[0].gate.named("test").run == ("true",)
    assert spec.subtasks[1].gate.named("test").run == ("false",), "a child may say its own gate"
    assert [r.name for r in spec.subtasks[0].stages] == ["screen"]
    assert spec.subtasks[0].subtasks == ()
    problem = PromptProblem(spec)
    work = problem.decompose(None)
    assert [w.name for w in work] == ["sim", "arch"]
    assert all(isinstance(w, SubLoop) for w in work)
    with pytest.raises(TaskError, match="cannot both"):
        TaskSpec.from_dict({"id": "t",
                            "statement": "s",
                            "parts": "decompose",
                            "subtasks": "decompose",
                            "flow": {"test": {"test": ["true"]}}})


def test_a_document_can_ask_the_orchestrator_to_split_it():
    """`"subtasks": "decompose"` children inherit all but the statement, and the division is remembered for resume."""
    from flux_loop import LoopState, PromptProblem, TaskSpec
    from flux_llm import ScriptedProposer

    spec = TaskSpec.from_dict({"id": "top",
                               "statement": "make a thing",
                               "subtasks": "decompose",
                               "max_subtasks": 3,
                               "flow": {"test": {"test": ["true"]}}})
    problem = PromptProblem(spec)
    proposer = ScriptedProposer(['{"subtasks": [{"name": "front", "statement": "the front"},'
                                 ' {"name": "back", "statement": "the back"}], "why": "two ends"}'])
    state = LoopState(request=LoopRequest(), say=lambda _m: None, proposer=proposer,
                      feedback=None)
    work = problem.decompose(state)
    assert [w.name for w in work] == ["front", "back"]
    assert work[0].statement == "the front"
    child = work[0].problem.task
    assert child.id == "top/front" and child.gate.named("test").run == ("true",) and not child.split
    assert "1 to 3 SUB-TASKS" in proposer.prompts[0]
    again = problem.decompose(state)
    assert [w.name for w in again] == ["front", "back"] and len(proposer.prompts) == 1, (
        "asked once: the division a pass works on does not change under it")


def _two(tmp_path, compose: bool):
    """A parent with two sub-loops in folders, each its own check over one shared stage (D801)."""
    home = tmp_path / "two"
    for d, n, words in (("short", 3, ("abcd", "ab")), ("long", 6, ("abcdefgh", "abcde"))):
        (home / "ops" / d).mkdir(parents=True)
        (home / "ops" / d / "check.py").write_text(
            "import sys\nprint(int(len(open(sys.argv[1]).read().strip()) > int(sys.argv[2])), 'failing')\n")
        for w in words:
            (home / "ops" / d / f"{w}.txt").write_text(w + "\n")
        (home / "ops" / d / "problem.yaml").write_text(
            f"statement: a word of at most {n} letters\nflow:\n"
            f"  generate: {{catalog: [{words[0]}.txt, {words[1]}.txt]}}\n"
            f"  test: \"{{python}} {{home}}/check.py {{artifact}} {n}\"\n")
    (home / "compose.py").write_text(
        "import json, sys\nparts = json.load(open(sys.argv[1]))\n"
        "open(sys.argv[2], 'w').write('+'.join(open(p).read().strip() for p in parts.values()) + '\\n')\n")
    (home / "problem.yaml").write_text(
        "statement: two words, each judged by its own check\nlanguage: text\n"
        "objectives: [{metric: chars, direction: maximize}]\nsubtasks: [ops/short, ops/long]\nflow:\n"
        + ("  generate: {command: \"{python} {home}/compose.py {parts} {artifact}\"}\n" if compose else "")
        + "  measure:\n    len: {command: \"{python} -c \\\"import sys; print('chars=' + str(len(open(sys.argv[1]).read().strip())))\\\" {artifact}\","
        " metrics: [chars]}\nbudget: {prototype: false, steps: 4}\n")
    return home


def test_sub_loops_in_folders_override_their_parent_box_by_box(tmp_path):
    """D801: a child in a folder says only what differs -- its own `test` -- and keeps the
    parent's stage; its id is its folder, its home the folder; the parent judges nothing itself."""
    from flux_loop import load_task

    task = load_task(_two(tmp_path, compose=False))
    short, long_ = task.subtasks
    assert (short.id, long_.id) == ("short", "long") and short.home.endswith("ops/short")
    assert [s.name for s in short.stages] == ["len"], "the parent's stage, inherited"
    assert "3" in " ".join(list(short.gate)[0].run) and "6" in " ".join(list(long_.gate)[0].run)
    assert short.record == "two/short" and not list(task.gate)
    assert task.to_dict()["subtasks"] == ["ops/short", "ops/long"], "written back as the folders"
    # D805: one workbench for the parent and its sub-loops, as one out/ -- also run alone
    wb = str((tmp_path / "two" / "workbench").resolve())
    assert task.workbench == short.workbench == long_.workbench == wb
    alone = load_task(tmp_path / "two" / "ops" / "short")
    assert alone.workbench == wb and alone.out_dir() == tmp_path / "two" / "out"


def test_the_parents_generate_composes_the_sub_loops(tmp_path):
    """D801: the parent's `generate: {command}` gets `{parts}` -- each sub-loop's answer -- and
    writes the whole, which the parent measures; no child inherits it."""
    from flux_loop import PromptProblem, load_task, request_for, run_loop

    task = load_task(_two(tmp_path, compose=True))
    assert all("generate" not in (c.flow or {}) or c.generator.get("catalog") for c in task.subtasks)
    out = run_loop(PromptProblem(task), request_for(task, db=str(tmp_path / "t.db")), log=lambda _m: None)
    assert out.decision is not None and out.decision.candidate.artifact == "ab+abcde\n"
    assert out.decision.metrics["chars"] == 8
