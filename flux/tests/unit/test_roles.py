"""The four roles as swappable components (docs/decisions.md D460).

Cedric's requirement: orchestration, generation, evaluation and knowledge must each work with a
model and without one, and switching which must be easy -- "have some application where we can
toggle what orchestrator is used". Generation (D456) and knowledge (D449) were already
components; orchestration only had DEFAULTS, and a default is not something a document or a flag
can choose.

What these pin: the registry answers what a role can be switched to and refuses what it cannot,
a rig is opt-in (an empty one is exactly the old behaviour), the no-AI orchestrators run a whole
loop with no model in it at all, the AI one is the loop's own default under a name, a task
document can say who fills a role, and a command line can switch one role of a document it did
not write.
"""

from __future__ import annotations

import pytest
from flux_loop import (Candidate, Given, LoopRequest, ModelOrchestrator, Problem, PromptProblem,
                       Roles, Rules, SubLoop, TaskError, TaskSpec, Template, Verdict,
                       available_roles, make_role, register_role, rig, run_loop)


class Pair(Problem):
    """Two parts, written by a template: nothing here needs a model."""

    name = "pair"

    def __init__(self, roles: Roles | None = None) -> None:
        self._roles = roles
        self.written: list[str] = []

    def objective(self, request):
        return {"study": "pair"}

    def subgoals(self):
        return ["front", "back"]

    def generator(self, subgoal, state):
        chosen = self.roles().generator
        if chosen is not None:
            return chosen
        return Template(lambda attempt: Candidate(name=f"{attempt.subgoal}-art",
                                                  artifact=str(attempt.subgoal)))

    def build(self, cand, subgoal, state):
        self.written.append(cand.artifact)
        return cand.artifact

    def judge(self, built, cand, subgoal, state):
        return Verdict(True, 0.0)

    def compose(self, admitted, state):
        if len(admitted) < 2:
            return None
        return Candidate(name="composed", artifact="".join(
            admitted[k].artifact for k in ("front", "back")))

    def stages(self):
        return ["stage"]

    def measure(self, cand, stage, state):
        return {"size": float(len(cand.artifact))}


class Angry:
    """A proposer that must not be called."""

    def propose(self, prompt: str) -> str:
        raise AssertionError("no model should be asked here")


def _request(tmp_path, **kw):
    kw.setdefault("steps", 4)
    return LoopRequest(db=str(tmp_path / "r.db"), prototype=False, compute=False,
                       critique_rounds=0, **kw)


# ------------------------------------------------------------------- the registry
def test_the_registry_says_what_each_role_can_be_switched_to():
    assert available_roles("orchestrator") == ["anneal", "given", "llm", "montecarlo",
                                              "rules", "sweep"], (
        "the DSE box is part of this role now (D465)")
    assert "model" in available_roles("generator") and "catalog" in available_roles("generator")
    with pytest.raises(ValueError, match="not one of the four roles"):
        available_roles("evaluation")
    with pytest.raises(ValueError, match="available: anneal, given, llm"):
        make_role("orchestrator", "vibes")


def test_a_role_that_needs_code_says_so_instead_of_guessing():
    """A template generator is a callable; a name cannot conjure one. And a component that
    cannot work without configuration names what it needs (D461's learned stage needs a
    metric), rather than being built half-formed."""
    with pytest.raises(ValueError, match="CALLABLE the problem supplies"):
        make_role("generator", "template")
    with pytest.raises(ValueError, match="needs the `metric` it predicts"):
        make_role("evaluator", "learned")
    with pytest.raises(ValueError, match="the no-AI evaluation half is a problem's `stages`"):
        make_role("evaluator", "intuition")


def test_a_spec_can_be_a_name_a_pair_a_dict_a_component_or_nothing():
    assert make_role("orchestrator", "rules").name == "rules"
    assert make_role("orchestrator", {"given": {"parts": ["a"]}}).parts == ("a",)
    assert make_role("orchestrator", {"name": "given", "parts": ["b"]}).parts == ("b",)
    mine = Rules(name="mine")
    assert make_role("orchestrator", mine) is mine
    assert make_role("orchestrator", None) is None


def test_the_rig_is_opt_in():
    """Every slot None is exactly what a problem written before the rig existed does."""
    empty = Pair().roles()
    assert (empty.orchestrator, empty.generator, empty.evaluator, empty.knowledge) == \
        (None, None, None, None)
    assert empty.named() == {}
    assert rig(orchestrator="rules").named() == {"orchestrator": "rules"}


# ------------------------------------------------------- orchestration, without a model
def test_the_rules_orchestrator_runs_the_whole_loop_with_no_model(tmp_path):
    problem = Pair(rig(orchestrator="rules"))
    out = run_loop(problem, _request(tmp_path), proposer=Angry(), log=lambda _m: None)
    assert sorted(out.admitted) == ["back", "front"], "both parts were written"
    assert problem.written == ["front", "back"], "in the declared order, decided in code"
    assert out.decision is not None and out.decision.metrics["size"] == 9.0


def test_the_user_can_give_the_division_and_its_order(tmp_path):
    """`given` is the other no-AI half: the parts are the user's, not the problem's."""
    problem = Pair(rig(orchestrator={"given": {"parts": ["back", "front"]}}))
    out = run_loop(problem, _request(tmp_path), proposer=Angry(), log=lambda _m: None)
    assert problem.written == ["back", "front"], "the order the user gave, not the declared one"
    assert sorted(out.admitted) == ["back", "front"]


def test_a_given_division_may_carry_a_sub_task(tmp_path):
    """A part that is itself a loop is a work item like any other (D455), so it can be given."""
    child = Pair(rig(orchestrator="rules"))

    class Composing(Pair):
        def compose(self, admitted, state):
            return Candidate(name="composed",
                             artifact="".join(c.artifact for _k, c in sorted(admitted.items())))

    problem = Composing(Roles(orchestrator=Given(
        ["front", SubLoop(name="deep", problem=child, statement="its own study")])))
    out = run_loop(problem, _request(tmp_path, max_depth=2), proposer=Angry(),
                   log=lambda _m: None)
    assert "deep" in out.admitted, "the child's decision became the parent's part"
    assert child.written == ["front", "back"], "and the child ran its own loop"


def test_an_empty_given_division_is_refused_at_build_time():
    with pytest.raises(ValueError, match="needs the parts it was given"):
        Given([])


# ---------------------------------------------------------- orchestration, with a model
def test_the_model_orchestrator_is_the_default_under_a_name(tmp_path):
    asked: list[str] = []

    class Planner:
        def propose(self, prompt: str) -> str:
            asked.append(prompt)
            return '{"next": "back", "method": "try the back first"}'

    problem = Pair(rig(orchestrator="llm"))
    out = run_loop(problem, _request(tmp_path), proposer=Planner(), log=lambda _m: None)
    assert asked, "the model was asked which part to work on"
    assert problem.written[0] == "back", "and its answer was used"
    assert sorted(out.admitted) == ["back", "front"]


def test_the_model_orchestrator_chooses_what_a_step_is_for(tmp_path):
    """`next_work` (D457) is an orchestration decision too, so the model may answer it."""
    seen: list[str] = []

    class Chooser:
        def propose(self, prompt: str) -> str:
            seen.append(prompt)
            if "what the next step should do" in prompt:
                return '{"next": "batch"}'
            return '{"next": "front"}'

    class Both(Pair):
        def search(self, state):
            yield [Candidate(name="found", artifact="ff")]

    problem = Both(rig(orchestrator="llm"))
    run_loop(problem, _request(tmp_path, steps=3), proposer=Chooser(), log=lambda _m: None)
    assert any("what the next step should do" in p for p in seen), (
        "it was asked which kind of work the step is for")


def test_an_answer_the_loop_cannot_use_falls_back_to_the_declared_work(tmp_path):
    class Confused:
        def propose(self, prompt: str) -> str:
            return "sure, whatever you think"

    class Both(Pair):
        def search(self, state):
            yield [Candidate(name="found", artifact="ff")]

    problem = Both(rig(orchestrator="llm"))
    out = run_loop(problem, _request(tmp_path, steps=3), proposer=Confused(),
                   log=lambda _m: None)
    assert problem.written, "the parts were worked anyway"
    assert out.decision is not None


# ------------------------------------------------------------------- the document
def _doc(**kw):
    doc = {"id": "rigged", "statement": "write the word good", "extension": ".txt",
           "parts": [{"name": "one", "statement": "the word"},
                     {"name": "two", "statement": "the word again"}],
           "gate": {"test": ["true"]},
           "stages": [{"name": "size", "command": ["wc", "-c", "{artifact}"],
                      "metrics_re": {"bytes": r"(\d+)"}}],
           "objectives": [{"metric": "bytes", "direction": "minimize"}]}
    doc.update(kw)
    return doc


def test_a_document_says_who_fills_a_role():
    task = TaskSpec.from_dict(_doc(roles={"orchestrator": "rules"}))
    assert task.roles == {"orchestrator": "rules"}
    problem = PromptProblem(task)
    assert problem.roles().orchestrator.name == "rules"
    assert task.to_dict()["roles"] == {"orchestrator": "rules"}


def test_a_role_a_document_cannot_mean_is_a_load_error():
    with pytest.raises(TaskError, match="available: anneal, given, llm"):
        TaskSpec.from_dict(_doc(roles={"orchestrator": "telepathy"}))
    with pytest.raises(TaskError, match="keys are the four roles"):
        TaskSpec.from_dict(_doc(roles={"evaluation": "tools"}))
    with pytest.raises(TaskError, match="said twice"):
        TaskSpec.from_dict(_doc(roles={"generator": "model"},
                                generator={"command": ["true"]}))


def test_a_caller_switches_one_role_of_a_document_it_did_not_write():
    task = TaskSpec.from_dict(_doc(roles={"orchestrator": "llm", "generator": "model"}))
    problem = PromptProblem(task, roles=Roles(orchestrator=Rules()))
    assert problem.roles().orchestrator.name == "rules", "the caller's choice won"
    assert problem.roles().generator is not None, "and the document's other choice stood"


def test_the_command_line_builds_a_rig():
    from flux_cli.commands import _roles_from

    assert _roles_from(None) is None
    got = _roles_from(["orchestrator=rules"])
    assert got.orchestrator.name == "rules" and got.generator is None
    with pytest.raises(ValueError, match="ROLE=NAME"):
        _roles_from(["orchestrator"])


def test_a_role_component_can_be_registered_from_outside():
    class Alternating:
        name = "alternating"

        def divide(self, problem, state, critique=None):
            return None

        def plan_next(self, problem, menu, state, human):
            return (menu[-1], "from the back") if menu else None

        def next_work(self, problem, state, waiting):
            return None

    register_role("orchestrator", "alternating", lambda _c: Alternating(), replace=True)
    try:
        assert "alternating" in available_roles("orchestrator")
        assert make_role("orchestrator", "alternating").name == "alternating"
    finally:
        from flux_loop import roles as roles_module
        roles_module._FACTORIES["orchestrator"].pop("alternating", None)
