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
from flux_llm import Reply


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

    def propose(self, prompt, *, schema=None, tools=None, budget=None):
        raise AssertionError("no model should be asked here")


def _request(tmp_path, **kw):
    kw.setdefault("steps", 4)
    return LoopRequest(db=str(tmp_path / "r.db"), prototype=False,
                       critique_rounds=0, **kw)


# ------------------------------------------------------------------- the registry
def test_the_registry_says_what_each_role_can_be_switched_to():
    assert available_roles("orchestrator") == ["agent", "anneal", "genetic", "given", "gradient", "llm", "model",
                                               "montecarlo", "rules", "sweep"], (
        "the agent joined this role (D505); the DSE policies left it (D507) and came back over `space:` (D553)")
    assert "model" in available_roles("generator") and "catalog" in available_roles("generator")
    with pytest.raises(ValueError, match="not one of the four roles"):
        available_roles("evaluation")
    with pytest.raises(ValueError, match="available: agent, anneal, genetic, given, gradient, llm, model, montecarlo, rules, sweep"):
        make_role("orchestrator", "vibes")


def test_a_role_that_needs_code_says_so_instead_of_guessing():
    """A template generator is a callable; a name cannot conjure one. And a name that is
    no evaluator (the learned screen left with D507) says where the no-AI half lives."""
    with pytest.raises(ValueError, match="CALLABLE the problem supplies"):
        make_role("generator", "template")
    with pytest.raises(ValueError, match="the no-AI evaluation half is a problem's `stages`"):
        make_role("evaluator", "intuition")


def test_a_spec_can_be_a_name_a_pair_a_dict_a_component_or_nothing():
    assert make_role("orchestrator", "rules").name == "rules"
    assert make_role("orchestrator", {"given": {"parts": ["a"]}}).parts == ("a",)
    assert make_role("orchestrator", "given").parts == ()                    # D566: the document's parts, as declared
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


def test_an_empty_given_division_is_the_problems_own_parts():
    """D566: `given` names no parts of its own; empty, it takes the problem's as declared."""
    assert Given([]).parts == () and Given([]).divide(Pair(), None) == ["front", "back"]


# ---------------------------------------------------------- orchestration, with a model
def test_the_model_orchestrator_is_the_default_under_a_name(tmp_path):
    asked: list[str] = []

    class Planner:
        def propose(self, prompt, *, schema=None, tools=None, budget=None):
            asked.append(prompt)
            return Reply.of('{"next": "back", "method": "try the back first"}')

    problem = Pair(rig(orchestrator="llm"))
    out = run_loop(problem, _request(tmp_path), proposer=Planner(), log=lambda _m: None)
    assert asked, "the model was asked which part to work on"
    assert problem.written[0] == "back", "and its answer was used"
    assert sorted(out.admitted) == ["back", "front"]


def test_the_model_orchestrator_chooses_what_a_step_is_for(tmp_path):
    """`next_work` (D457) is an orchestration decision too, so the model may answer it."""
    seen: list[str] = []

    class Chooser:
        def propose(self, prompt, *, schema=None, tools=None, budget=None):
            seen.append(prompt)
            if "what the next step should do" in prompt:
                return Reply.of('{"next": "batch"}')
            return Reply.of('{"next": "front"}')

    class Both(Pair):
        def search(self, state):
            yield [Candidate(name="found", artifact="ff")]

    problem = Both(rig(orchestrator="llm"))
    run_loop(problem, _request(tmp_path, steps=3), proposer=Chooser(), log=lambda _m: None)
    assert any("what the next step should do" in p for p in seen), (
        "it was asked which kind of work the step is for")


def test_an_answer_the_loop_cannot_use_falls_back_to_the_declared_work(tmp_path):
    class Confused:
        def propose(self, prompt, *, schema=None, tools=None, budget=None):
            return Reply.of("sure, whatever you think")

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
    with pytest.raises(TaskError, match="available: agent, anneal, genetic, given, gradient, llm, model, montecarlo, rules, sweep"):
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


# ------------------------------------------------------------- the AGENT orchestrator (D505)
class Ladder(Pair):
    """A problem with an improve ladder: the first stage sends `front` back once, and the
    ladder offers two steps -- the rules would take `polish` (due), the agent may prefer
    `rewrite` if the numbers say so."""

    def __init__(self, roles=None):
        super().__init__(roles)
        self.taken: list[str] = []

    def route(self, stage, scored, state):
        from flux_loop import Improve

        if any(i.subgoal == "front" for i in state.improve) or getattr(self, "_sent", False):
            return []
        self._sent = True
        return [Improve(state.admitted["front"], why="front is 3 bytes; the goal is 1", stage=stage, subgoal="front")]

    def improve_options(self, item, state):
        from flux_loop import Option

        def polish():
            self.taken.append("polish")
            return Candidate(name="front-polished", artifact="fr", subgoal="front"), "fr", ""

        def rewrite():
            self.taken.append("rewrite")
            return Candidate(name="front-rewritten", artifact="f", subgoal="front"), "f", ""

        return [Option("polish", "shave a byte; cheap", due=True, run=polish),
                Option("rewrite", "start over for the 1-byte goal; a whole pass", due=False, run=rewrite),
                Option("stand", "keep it", due=False, run=lambda: (None, None, "stands"))]

    def standing(self, state):
        return {"goal": "two parts, the smallest", "now": "improving front", "parts": {"front": "3 bytes"}}


def test_the_rules_take_the_first_due_step_of_a_ladder(tmp_path):
    """D505: a problem's improve ladder as a menu; with no agent the rules take the first due
    step, and the loop runs it as `improve` always did."""
    problem = Ladder(rig(orchestrator="rules"))
    out = run_loop(problem, _request(tmp_path, steps=6), proposer=Angry(), log=lambda _m: None)
    assert problem.taken == ["polish"] and out.admitted["front"].name == "front-polished"


def test_the_agent_orchestrator_picks_a_step_with_its_tools_and_records_why(tmp_path):
    """D505 (Cedric: "agentic capabilities ... orchestrator/orchestration"): the agent reads
    the standings and the decisions through tools, picks a step off the same menu -- here the
    one the rules would not take -- and its reason is on the record and in the log."""
    said: list[str] = []
    asked: list[tuple[str, list[str]]] = []

    class Agent:
        def propose(self, prompt, *, schema=None, tools=None, budget=None):
            names = [t.name for t in (tools or [])]
            asked.append((prompt, names))
            if "Pick ONE option" in prompt:
                by = {t.name: t for t in tools}
                assert "GOAL: two parts, the smallest" in by["standings"].run({}) and "admitted front: front-art" in by["standings"].run({})
                assert "[next part] back -- back first" in by["decisions"].run({})   # its own earlier pick
                return Reply.of('{"pick": "rewrite", "why": "3 bytes against a goal of 1: polishing cannot get there"}')
            if "Choose the next part" in prompt:
                return Reply.of('{"pick": "back", "why": "back first", "method": "as given"}')
            return Reply.of('{"pick": "improve", "why": "in hand first"}')

    problem = Ladder(rig(orchestrator="agent"))
    out = run_loop(problem, _request(tmp_path, steps=6, agent=("orchestrate",)), proposer=Agent(), log=said.append)
    assert problem.taken == ["rewrite"] and out.admitted["front"].name == "front-rewritten"
    assert problem.written[0] == "back"                                  # its part order was used
    assert any("orchestrate [improve front]: rewrite -- 3 bytes against a goal of 1" in m for m in said)
    step_turn = next(p for p, _n in asked if "Pick ONE option" in p)
    assert "  - polish (due): shave a byte" in step_turn and "  - rewrite: start over" in step_turn
    assert any(n == ["standings", "history", "decisions", "knowledge"] for _p, n in asked)
    from flux_records import Records

    rec = Records(str(tmp_path / "r.db"), objective={"study": "pair"})
    picks = [e["detail"] for e in rec.store.events(rec.campaign_id) if e["kind"] == "decision"]
    assert any(d["what"] == "improve front" and d["pick"] == "rewrite" for d in picks)
    assert any(d["what"] == "next part" and d["pick"] == "back" for d in picks)


def test_the_agent_falls_back_to_the_rules_when_it_answers_off_the_menu(tmp_path):
    said: list[str] = []

    class Vague:
        def propose(self, prompt, *, schema=None, tools=None, budget=None):
            return Reply.of('{"pick": "something else", "why": "?"}')

    problem = Ladder(rig(orchestrator="agent"))
    out = run_loop(problem, _request(tmp_path, steps=6, agent=("orchestrate",)), proposer=Vague(), log=said.append)
    assert problem.taken == ["polish"] and out.admitted["front"].name == "front-polished"
    assert any("answered off the menu; the rules decide" in m for m in said)


def test_the_request_rigs_the_agent_when_the_problem_left_the_role_open(tmp_path):
    """`--agent orchestrate` fills the orchestrator with the agent for a problem that never
    filled the role; a problem that did keeps its own."""
    class Agent:
        def propose(self, prompt, *, schema=None, tools=None, budget=None):
            return Reply.of('{"pick": "rewrite", "why": "the goal"}' if "Pick ONE option" in prompt else '{"pick": "front", "why": "-"}')

    problem = Ladder()
    run_loop(problem, _request(tmp_path, steps=6, agent=("orchestrate",)), proposer=Agent(), log=lambda _m: None)
    assert problem.taken == ["rewrite"] and problem.roles().orchestrator.name == "agent"
    problem2 = Ladder(rig(orchestrator="rules"))
    run_loop(problem2, _request(tmp_path, steps=6, agent=("orchestrate",)), proposer=Agent(), log=lambda _m: None)
    assert problem2.taken == ["polish"] and problem2.roles().orchestrator.name == "rules"


def test_a_pass_where_every_design_stood_is_at_rest(tmp_path):
    """D506: 73 passes in four hours, each deciding "stand" for every part. A pass that admits
    nothing and whose every improve step chose to stand says it is AT REST, and the result
    carries it so a looping caller (the TUI's r loop) stops instead of spinning."""
    class Resting(Ladder):
        def improve_options(self, item, state):
            from flux_loop import Option

            return [Option("depth", "a pass", due=False, run=lambda: (None, None, "no")),
                    Option("stand", "keep it", due=False, run=lambda: (None, None, "stands"))]

    problem = Resting(rig(orchestrator="rules"))
    out = run_loop(problem, _request(tmp_path, steps=6), proposer=Angry(), log=lambda _m: None)
    assert sorted(out.admitted) == ["back", "front"]           # the first pass: parts made, nothing at rest
    assert not out.at_rest
    again = Resting(rig(orchestrator="rules"))
    said: list[str] = []
    out2 = run_loop(again, _request(tmp_path, steps=6), proposer=Angry(), log=said.append)
    assert out2.at_rest and out2.stopped.startswith("at rest: every ladder is spent (front)")
    assert any("at rest" in m for m in said)


def test_a_pass_where_nothing_was_due_on_any_part_is_at_rest(tmp_path):
    """D518, live: every part met the goal alone, the whole missed it by 1.5%, the route sent
    nothing back -- and nine passes in twenty-five minutes each decided the same 788 MHz.
    A pass that admits nothing, sends nothing back and has nothing left to make is AT REST
    even when no ladder was walked."""
    problem = Pair(rig(orchestrator="rules"))
    out = run_loop(problem, _request(tmp_path, steps=6), proposer=Angry(), log=lambda _m: None)
    assert sorted(out.admitted) == ["back", "front"] and not out.at_rest
    said: list[str] = []
    out2 = run_loop(Pair(rig(orchestrator="rules")), _request(tmp_path, steps=6), proposer=Angry(), log=said.append)
    assert out2.at_rest and out2.stopped.startswith("at rest: nothing was due on any part")
    assert any("at rest: nothing was due" in m for m in said)


def test_a_composition_of_a_superseded_part_leaves_the_pool(tmp_path):
    """D506, live: after gelu was pipelined, the confirm stage kept measuring the OLD
    composition (unpipelined, 91 MHz) beside the new part at 636 and routed the part back
    with the stale number. When a part changes, every composition measured so far leaves the
    pass's pool (the record keeps it); the new composition is measured on its own."""
    class Twice(Ladder):
        def improve_options(self, item, state):
            from flux_loop import Option

            return [Option("polish", "shave", due=True,
                           run=lambda: (Candidate("front-polished", "fr", subgoal="front"), "fr", ""))]

    problem = Twice(rig(orchestrator="rules"))
    out = run_loop(problem, _request(tmp_path, steps=6), proposer=Angry(), log=lambda _m: None)
    composed = [s for s in out.scored if s.candidate.subgoal is None]
    assert [s.candidate.artifact for s in composed] == ["frback"]      # only the composition of what stands


def test_given_orders_the_documents_parts_and_never_names_its_own():
    """D566 (review 2, R10): parts are named once, in the document; `given` is an order over
    them -- a bare `given` takes them as declared, a list puts some first, a name the document
    does not have is refused."""
    from flux_loop import PromptProblem, TaskSpec

    doc = {"id": "g", "statement": "g", "parts": ["a", "b", "c"], "gate": {"test": ["true"]}}
    bare = PromptProblem(TaskSpec.from_dict({**doc, "roles": {"orchestrator": "given"}}))
    assert bare.roles().orchestrator.divide(bare, None) == ["a", "b", "c"]
    first = PromptProblem(TaskSpec.from_dict({**doc, "roles": {"orchestrator": {"given": {"parts": ["c"]}}}}))
    assert first.roles().orchestrator.divide(first, None) == ["c", "a", "b"]
    import pytest

    stray = PromptProblem(TaskSpec.from_dict({**doc, "roles": {"orchestrator": {"given": {"parts": ["z"]}}}}))
    with pytest.raises(ValueError, match="`given` names \\['z'\\], which the problem does not have"):
        stray.roles().orchestrator.divide(stray, None)
