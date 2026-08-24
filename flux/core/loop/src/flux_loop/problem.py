"""The contract a problem implements (D421), as the four roles of the drawing (D427): mentor, orchestrator, generator, evaluator -- each a class of hooks with defaults, `Problem` their combination.

WHAT A WORLD FILLS (D561): the contract is `flux_loop.task.CONTRACT`, by box of the drawing, with
`CORE` the fourteen hooks a world usually fills; `flux task check` prints it with what the world
filled. The other public methods here are the loop's own and a world cannot take them.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Callable, Iterator

from .model import _ask, _json
from .patch import focus_window, patch_prompt, patch_schema
from .capabilities import Prototype  # noqa: F401 -- the hook's type
from .ladder import Ladder  # noqa: F401 -- the hook's type
from .objective import Objectives
from .types import (Candidate, Improve, LoopRequest, LoopState, Option, Scored, SubLoop,
                    Verdict)

if TYPE_CHECKING:  # pragma: no cover
    from .roles import Roles
    from .sources import Source

__all__ = ["EvaluatorRole", "GeneratorRole", "MentorRole", "OrchestratorRole", "Problem"]

class _Role:
    """A role's hooks, each with a default; `Problem` is the four roles combined.
    A problem can also be assembled from parts -- a shared evaluator role, a
    problem-specific generator role -- which is what the split is for (D427).

    Every role also knows about `Roles` (D460): WHO fills it, if anyone. `None` in a slot --
    the default for all four -- is the problem's own hooks, so a problem written before the rig
    existed behaves exactly as it did."""

    name: str = "problem"

    #: A problem sets this (in its constructor, from a flag, from a document) to swap a role.
    _roles: "Roles | None" = None

    def roles(self) -> "Roles":
        """Who fills each of the four roles. Overridable for a problem that builds its rig per
        run; the default is whatever was set on the problem."""
        from .roles import Roles

        return self._roles or Roles()


class MentorRole(_Role):
    """input / knowledge / records nodes: what the loop should know and record."""

    def objective(self, request: LoopRequest) -> dict[str, Any]:
        """The campaign record's objective document (what makes two runs the same
        campaign)."""
        return {"study": self.name, **request.params}

    def tools_missing(self) -> list[str]:
        """Names of tools the problem needs that are absent; the loop refuses loudly."""
        return []

    def validate(self, request: LoopRequest) -> list[str]:
        """What is wrong with the PROBLEM, before anything is spent on it (D463): a target no
        stage measures, a constraint nothing can check, an objective naming a metric this
        problem never produces. The loop refuses loudly with these words rather than running
        and deciding on nothing. Empty = the problem is well posed.

        This is the drawing's "input/problem valid?" node. It judges the REQUEST and the
        problem's own declarations, never a candidate -- a candidate is the gate's business."""
        return []

    def open_records(self, request: LoopRequest, say: Callable[[str], None]) -> Any:
        """The campaign record for `request.db` (D446): `flux_records.Records` over the
        problem's `objective`, or a problem's own subclass with a typed read-back."""
        from flux_records import Records

        return Records(request.db, objective=self.objective(request), log=say, name=self.campaign_name(request))

    def campaign_name(self, request: LoopRequest) -> str | None:
        """The campaign's own id in the record (D524), keyed by the PROBLEM: a document's
        `campaign: {name: nlu}`. None keys it by the objective's hash, as before."""
        return None

    def versions(self) -> dict[str, str]:
        """The versions of what makes and judges a design (D510): `{"transpiler": ..., "judge":
        ...}`, written on every admitted row. At a reload a row made by today's transpiler and
        judge is TRUSTED as it stands; a transpiler change re-spells the design from its
        prototype once and records the result; a judge change re-judges once. Empty (the
        default) means every reload re-verifies, as before."""
        return {}

    def from_record(self, doc: dict[str, Any]) -> tuple[Candidate, float | None, str] | None:
        """Map a recorded trial's candidate document back to (candidate, score-or-None,
        why). The default reads the loop's own shape; a problem whose campaign predates
        the loop overrides this so its history is not lost. None = not a candidate."""
        if "artifact" not in doc:
            return None
        sc = doc.get("score")
        return (Candidate.from_record(doc),
                float(sc) if isinstance(sc, (int, float)) else None,
                str(doc.get("why") or ""))

    def knowledge(self) -> Any | None:
        """The mentor's DECLARED sources (D449): a `flux_knowledge.Mentor` over `Corpus`,
        `Library`, `RecordReadback` and `Notes`. Given one, `mentor_sections` and the
        generator's static `prompt_prefix` are assembled from it -- each source read once
        per run when it cannot change, so a library lookup does not re-run every turn.
        None: the problem writes both by hand. The default is the rig's knowledge slot
        (D460)."""
        return self.roles().knowledge

    def mentor_sections(self, state: LoopState) -> list[tuple[str, str]]:
        """What the mentor role holds for this run, as (title, text) sections the
        observer can browse (D418m): the knowledge the prompts carry, the library,
        the record's read-back. The default is the declared `knowledge()`; the loop adds
        the generic record facts itself."""
        mentor = self.knowledge()
        return mentor.sections(state) if mentor is not None else []

    def prepare(self, state: LoopState) -> None:
        """Once per pass, after the record is open and proven/best parts are reloaded,
        before any planning: author or reload the test suite, open inputs, warm a
        cache. The place for the model's TEST-AUTHOR role when the problem has one.

        The default lets the EVALUATION component prepare itself (D461): a learned stage fits
        itself here, from what this campaign has already measured. A problem that overrides
        this and wants a learned stage calls `self.prepare_roles(state)`."""
        self.prepare_roles(state)

    def prepare_roles(self, state: LoopState) -> None:
        """Let each filled role prepare itself for this pass (D461). Only the evaluation
        component has anything to do so far; a role that cannot prepare is not asked."""
        for who in (self.roles().evaluator,):
            ready = getattr(who, "prepare", None)
            if callable(ready):
                try:
                    ready(self, state)
                except Exception as exc:  # noqa: BLE001 -- a role that cannot get ready is
                    state.say(f"  {getattr(who, 'name', who)} could not prepare "
                              f"({exc!s:.100}); the pass runs without it")


def plan_with_model(problem: Any, menu: list[str], state: LoopState,
                    human: str | None) -> tuple[str, str]:
    """The model choosing the next part from a schema-constrained menu (D413), falling
    back to the menu's first entry whenever there is no model, no choice to make, no
    prompt or no usable answer. Shared, because it is both the loop's default and what
    the `llm` orchestrator component IS (D460) -- one body, not two that drift."""
    default = menu[0]
    if state.proposer is None or len(menu) == 1:
        return default, ""
    prompt, schema = problem.plan_prompt(menu, state, human)
    if not prompt:
        return default, ""
    try:
        reply = _ask(state, prompt, schema).text
    except Exception:  # noqa: BLE001
        return default, ""
    doc = _json(reply)
    if isinstance(doc, dict) and doc.get("next") in menu:
        return str(doc["next"]), str(doc.get("method", ""))[:120]
    return default, ""


class OrchestratorRole(_Role):
    """gate / propose / DSE / frontier / decide nodes: what to try next, and the
    decision at the end.

    THREE kinds of work, one vocabulary (D457), and a step of the loop is one of them:

    * a PART to write -- `subgoals`/`decompose`, `plan_next`, then the generator's inner
      loop writes it against the fast test (the default);
    * a SUB-TASK to run as its own loop -- a `SubLoop` from `subproblems` or `decompose`,
      with its own gate, stages and record (D455);
    * a BATCH of candidates to gate and measure together -- `search` yields them, which is
      what an enumeration, a solver chain, a climb or a proposer round is (D446).

    A problem may have more than one of them, and then `next_work` is the orchestration
    decision: what to spend the next step on. Code, a DSE rule or a model may answer it."""

    def search(self, state: LoopState) -> Iterator[list[Candidate]] | None:
        """propose, for a problem that ENUMERATES or PROPOSES candidates in batches instead
        of writing one part at a time (D446): a generator. Every batch it yields is gated
        (`build`, `judge`) and the admitted candidates are measured on the first stage at
        once (`measure_batch`); the batch's `Scored` list comes back as the value of the
        `yield`, so a policy that reads results before choosing the next batch -- a climb,
        a solver-then-model chain, an invention round told this run's numbers -- is one
        generator with local state, and `request.steps` bounds the batches. An empty
        batch is a step that measures nothing; returning ends the search. None (the
        default) means this problem has no batches to search, only parts to write -- unless the
        ORCHESTRATOR component is a search policy of its own (D465: `sweep`, `montecarlo`,
        `anneal` over `space()`), which is then what this returns."""
        who = self.roles().orchestrator
        policy = getattr(who, "search", None)
        return policy(self, state) if callable(policy) else None

    def objections(self, state: LoopState) -> list[str]:
        """The MODEL'S reading of the problem before a step is spent (the drawing's "input /
        problem valid?" box, its model half, D556): objections said in the log and kept as
        lessons, never a gate -- the rules half (`validate`) refuses, this one advises. Empty
        by default; a document that says `flow: {validate: llm}` asks for it."""
        return []

    def space(self, state: LoopState) -> dict[str, list]:
        """The DESIGN SPACE a DSE policy searches (D465, D553): knob -> its choices, each in a
        meaningful order so a neighbour is a little different. The document's `space:` block
        by default; a world whose space is computed (the macarray: the multipliers on the
        menu times the reducers, depths and mappings) returns it here. Empty: no space."""
        return {}

    def instantiate(self, points: list[dict[str, Any]], state: LoopState) -> list[Candidate]:
        """The candidates for these points of the space (D553): what `build` and `judge` will
        take. The default keeps a point as knobs, named by its values; a world that generates
        text from a point (RTL from a configuration) generates -- and may verify -- it here,
        the whole batch at once."""
        return [Candidate(name="-".join(str(v) for v in p.values()), knobs=dict(p)) for p in points]

    def subgoals(self) -> list[str]:
        """The parts to divide into (operators, fabrics, ...) in default order,
        easiest first. Empty = one indivisible goal."""
        return []

    def subproblems(self, state: LoopState) -> list[SubLoop]:
        """Parts that are THEMSELVES loops (D455), declared by the problem: a composition
        study that knows its children names them here every pass, and the orchestrator only
        chooses which to run next and when to stop. A child has its own gate, stages and
        record; what the parent sees is what the child DECIDED, as an admitted part.

        The other half of the same capability is dynamic: `decompose` may return `SubLoop`
        items a model asked for. Both arrive at the parent as the same work item."""
        return []

    def decompose(self, state: LoopState,
                  critique: str | None = None) -> list[str | SubLoop]:
        """propose: decompose (D431) -- the parts this pass works on, decided once the
        record is open and before anything is reloaded, so a problem that asks a model
        to divide the task can remember the division in the record and reuse it on
        resume. `critique` is a critic's objection to the previous division (D433): a
        problem that re-divides reads it, the default ignores it.

        A part is either a NAME the generator writes, or a `SubLoop` the parent runs as its
        own loop (D455). The default is both, as declared: `subgoals()` then
        `subproblems(state)` -- unless the ORCHESTRATOR component has its own division
        (D460: a user-given list of sub-tasks, a rule, a model)."""
        planned = (getattr(state, "plan", None) or {}).get("parts")
        if isinstance(planned, list) and planned:
            # D505: the LOOP PLAN names the parts and their order; a declared sub-loop keeps
            # its `SubLoop` under its name
            subs = {sl.name: sl for sl in self.subproblems(state)}
            return [subs.get(p, p) for p in planned]
        divide = getattr(self.roles().orchestrator, "divide", None)
        if callable(divide):
            given = divide(self, state, critique)
            if given is not None:
                return list(given)
        return [*self.subgoals(), *self.subproblems(state)]

    def next_work(self, state: LoopState, waiting: list[str]) -> str:
        """WHAT TO DO NEXT, when there is more than one kind of work available (D457/D463):
        `"part"` (write the next part, or run the next sub-task as its own loop), `"batch"`
        (take the next batch of candidates from `search` through the gate and the first stage)
        or `"improve"` (redraft a design an evaluator sent back, `state.improve`). The loop
        asks only when there is actually a choice.

        The default improves what is in hand first -- a design whose numbers say it is nearly
        right is the cheapest progress available -- then finishes the declared parts, because
        a composition needs its pieces before there is anything to search over. A problem may
        answer from code, from a DSE rule, or by asking a model; the ORCHESTRATOR component
        answers when there is one (D460).
        """
        choose = getattr(self.roles().orchestrator, "next_work", None)
        if callable(choose):
            answer = choose(self, state, waiting)
            if answer is not None:
                return answer          # the component decides; it sees the queue too
        if state.improve:
            return "improve"
        return "part" if waiting else "batch"

    def plan_next(self, menu: list[str], state: LoopState, human: str | None
                  ) -> tuple[str, str]:
        """(subgoal, method) for the next step. The default asks the model with a
        schema whose `next` is an enum of the menu, falling back to the menu's first
        entry; a problem with its own policy (exhaustive, annealing, a fixed chain)
        overrides this and never calls a model -- and so does the ORCHESTRATOR component
        when one fills the role (D460: `rules` decides in code, `given` walks the user's own
        order, `llm` is this default, named)."""
        plan = getattr(self.roles().orchestrator, "plan_next", None)
        if callable(plan):
            answer = plan(self, menu, state, human)
            if answer is not None:
                return answer
        return plan_with_model(self, menu, state, human)

    def plan_part(self, subgoal: str | None, state: LoopState) -> dict[str, Any]:
        """propose: brief (D432) -- what the orchestrator decides about ONE part before
        its generation loop runs, once per part: `brief` (text the generation prompt's
        static prefix carries: what to make, what to watch, how it is judged) and
        inner-loop overrides (`repair_attempts`). Empty by default: the part statement
        is the brief. A problem that asks a model for it remembers the answer in the
        record so a resume reuses it."""
        return {}

    def library_index(self, state: LoopState) -> list[str]:
        """One line per digested library document (D576), for a planning prompt; [] when the
        mentor has no `digest` source or the store holds none."""
        mentor = self.knowledge()
        if mentor is None or getattr(mentor, "source", None) is None or mentor.source("digest") is None:
            return []
        try:
            from flux_knowledge import index_lines

            return index_lines(str(getattr(state.request, "db", "") or ""))
        except Exception:  # noqa: BLE001
            return []

    def plan_prompt(self, menu: list[str], state: LoopState, human: str | None
                    ) -> tuple[str, dict | None]:
        """The planner's prompt and schema; ("", None) means "do not ask"."""
        partial = {sg: why[:80] for sg, (_s, _c, why) in state.best.items() if sg in menu}
        lines = [human or "",
                 f"Proven so far: {', '.join(sorted(state.admitted)) or 'none'}.",
                 f"Still to prove: {', '.join(menu)}."]
        index = self.library_index(state)                                    # D576
        if index:
            lines.append("THE LIBRARY, digested (name a method from it when it fits):\n" + "\n".join(index[:40]))
        if partial:
            lines.append("Best refused attempt per part: "
                         + "; ".join(f"{k}: {v}" for k, v in partial.items()))
        lines.append('Choose the next part to work on and the method to try. Reply '
                     'with JSON {"next": "<one of the parts>", "method": "<one line>"}.')
        schema = {"type": "object",
                  "properties": {"next": {"type": "string", "enum": list(menu)},
                                 "method": {"type": "string"}},
                  "required": ["next"]}
        return "\n".join(l for l in lines if l), schema

    # ---- the decision
    def frontier_axes(self) -> tuple[Callable[[Scored], float], Callable[[Scored], float]] | None:
        """(better, cost) for the frontier over Scored: the first two objectives (D511);
        None = no frontier (a boolean-with-cost problem decides over all scored)."""
        return self.objectives().frontier_axes()

    def frontier(self, scored: list[Scored], state: LoopState) -> list[Scored]:
        """frontier: everything measured on the first stage that nothing else beats on
        every axis. Two axes from `frontier_axes` by default (D446); a problem with more
        objectives overrides this (Pareto over four costs). No axes = all of it."""
        axes = self.frontier_axes()
        if axes is None:
            return list(scored)
        from flux_frontier import frontier as _frontier

        better, cost = axes
        return _frontier(scored, better=better, cost=cost)

    def finalists(self, front: list[Scored], state: LoopState, stage: str = "") -> list[Scored]:
        """Which frontier points climb to `stage`, the next one up: `request.finalists` of them
        spread along the cost axis by default (D446). A problem adds what its report must
        compare on ONE stage -- the incumbent, a stack's shipped-default reference -- because a
        confirmed answer beside a screened incumbent compares stages, not designs.

        `stage` is which stage they are about to pay for (D454), so a three-stage chain can send
        more candidates to a cheap middle stage than to the expensive last one."""
        ordered = self.role_order(list(front), state, stage)
        if ordered is not None:                          # D560: the surrogate's order, its best first
            return ordered[:max(0, int(state.request.finalists))] if state.request.finalists else ordered
        axes = self.frontier_axes()
        if axes is None:
            return list(front)
        from flux_frontier import spread as _spread

        return _spread(front, state.request.finalists, cost=axes[1])

    def role_order(self, front: list[Scored], state: LoopState, stage: str) -> list[Scored] | None:
        """The evaluation component's ORDER of the frontier for `stage` (D560: a surrogate's
        prediction, best first), or None when it has none -- the problem then spreads."""
        who = self.roles().evaluator
        fn = getattr(who, "order", None)
        return fn(self, list(front), state, stage) if callable(fn) else None

    def review(self, stage: str, batch: list[Scored], state: LoopState) -> None:
        """What the orchestrator learns from a stage's results, once per measured batch
        (D446): the lessons a report carries -- the screen's leader against the incumbent,
        where the last stage disagreed with the screen -- appended to `state.lessons`. The
        default learns nothing."""
        return None

    def route(self, stage: str, scored: list[Scored], state: LoopState) -> list[Improve]:
        """Where a stage's results GO (D463). An evaluator has two edges out: the orchestrator,
        which picks what to try next, and the generator, which improves the design already in
        hand. Everything measured goes to the orchestrator by default (it is what `search`
        receives and what the chain climbs); what this returns ALSO goes back to the generator,
        as `Improve` items carrying the numbers that sent them back.

        Called after every stage, so any evaluator in the chain can send a design back -- a
        correctness test, a fast model, a slow simulation, a placement. The default is the
        ladder's route (D517) when the problem declares a ladder, else nothing: a problem
        whose designs are not improvable in place (a point in a discrete space) simply does
        not implement either."""
        ladder = self.ladder()
        if ladder is not None:
            from .ladder import route

            return route(self, stage, scored, state)
        return []

    def prefer_admitted(self, subgoal: str | None, rows: list[tuple[Candidate, dict[str, float] | None]],
                        state: LoopState, stage: str | None = None) -> Candidate | None:
        """WHICH of a part's admitted designs on record stands at a reload (D504): `rows` are
        (design, its numbers on the stage the reload compares on, or None), oldest first. By
        the objectives (D511): the tournament of `Objectives.better`, a design never measured
        cannot win over one that was, a dead heat keeps the later one. No objectives: None,
        and the last admitted stands -- a design admitted later is not better for being later."""
        return self.objectives().best_of(rows, stage, self.stages()) if self.objectives() else None

    def good_enough(self, state: LoopState) -> str | None:
        """OPTIONAL early stop (D463), off unless a problem implements it: whether the
        pass is done before its budget runs out, and why in words:
        "the target is met: 612 MHz at 0.51 mm2". The loop checks this before each step and
        stops with that reason in the report. None = keep working.

        A target is a property of the request, not of the loop, which is why this is a hook
        and why the default never stops: "good enough" for one study is a constraint met, for
        another a frontier that has not moved in three steps, and for a sweep that wants every
        point it is nothing at all. Same for `LoopRequest.budget_s`: no clock unless a caller
        sets one. The default (D511): the first objective's GOAL, met by the WHOLE design (the
        last composed candidate measured)."""
        objs = self.objectives()
        if objs.goal is None:
            return None
        stages = self.stages()
        whole = next((sc for sc in reversed(state.scored or []) if (sc.candidate.meta or {}).get("composed")), None)   # a list of parts, or True
        return objs.good_enough(whole.metrics, whole.stage, stages) if whole is not None else None

    def decide(self, pool: list[Scored], state: LoopState) -> tuple[Scored | None, str]:
        """(pick, decided_by), by the objectives (D511): with a goal, the least on the next
        objective among those at the goal (the best on the first when nothing reaches it);
        without one, the knee over every objective; with none, the first thing measured."""
        return self.objectives().decide(pool, self.stages())

    def conclusion(self, pick: Scored, decided_by: str) -> dict[str, Any]:
        return {"decision": pick.name, "decided_by": decided_by, **pick.metrics}


class GeneratorRole(_Role):
    """generate / template-fill / repair nodes: making a candidate."""

    def generator(self, subgoal: str | None, state: LoopState) -> "Source | None":
        """WHO drafts (D456): a `flux_loop.sources` source -- `Model` (the default),
        `Template` (a renderer, no model), `Catalog` (designs that already exist) or
        `Solver` (computed from the constraints). The generate/build/fast-check sub-loop
        around it is the same either way; `None` means the model inner loop.

        The default is the rig's generation slot (D460), so this role is switched the same way
        as the other three; a problem that chooses per part overrides this."""
        return self.roles().generator

    def improve_options(self, item: Improve, state: LoopState) -> list[Option]:
        """THE LADDER of a design sent back, as a menu (D505): what could be done for it, each
        with its line and the code's opinion of whether it is due. Empty (the default) means
        `improve` does what it always did; a problem with steps (the NLU's sweep, take, depth
        pass, redesign, stand) lists them here, and `improve_choice` -- the rules, or the agent
        orchestrator -- picks one."""
        ladder = self.ladder()
        if ladder is not None:
            from .ladder import options

            return options(self, ladder, item, state)
        return []

    def improve_choice(self, item: Improve, options: list[Option], state: LoopState) -> Option:
        """WHICH step of the ladder (D505): the ORCHESTRATOR component's pick when it has one
        (`choose_improve`), else the rules' -- the first due option, in the problem's order,
        and the last option (a problem lists `stand` last) when none is due."""
        if not options:
            raise ValueError("no options to choose from")
        choose = getattr(self.roles().orchestrator, "choose_improve", None)
        if callable(choose):
            pick = choose(self, item, options, state)
            if pick is not None:
                return pick
        return next((o for o in options if o.due), options[-1])

    def improve(self, item: Improve, state: LoopState) -> tuple[Candidate | None, Any, str]:
        """Draft a better version of a candidate an evaluator sent back (D463), told what its
        numbers were. Returns what `generate` returns.

        A problem with an improve ladder (`improve_options`, D505) has its step chosen here --
        by the rules or by the agent -- and run. Otherwise the default is the same generation
        sub-loop, seeded with the design in hand: the model path reworks it (D414's patch
        loop, which is what `state.best` is for), and a template, catalog or solver source
        sees it as `Attempt.prior` with `Attempt.failure` carrying the numbers."""
        options = self.improve_options(item, state)
        if options:
            pick = self.improve_choice(item, options, state)
            state.say(f"  improve {item.subgoal or item.candidate.name}: {pick.name} -- {pick.why[:140]}")
            got = pick.run() if callable(pick.run) else (None, None, f"{pick.name}: nothing to run")
            if pick.name == "stand" or (got[0] is None and not pick.due):
                state.rested.append(item.subgoal or item.candidate.name)     # D506: nothing was due
            elif got[0] is not None:
                state.improved += 1
            return got
        from .sources import Model, iterate

        source = self.generator(item.subgoal, state)
        if source is None or isinstance(source, Model):
            key = item.subgoal or "*"
            keep = state.best.get(key)
            state.best[key] = (0.0, item.candidate, item.why)
            try:
                return self.generate(item.subgoal, "improve", state, item.why)
            finally:
                if keep is None:
                    state.best.pop(key, None)
                else:
                    state.best[key] = keep
        return iterate(self, source, item.subgoal, state, prior=item.candidate,
                       failure=item.why)

    def generate(self, subgoal: str | None, method: str, state: LoopState,
                 human: str | None) -> tuple[Candidate | None, Any, str]:
        """Produce a BUILT candidate: (candidate, built, "") or (None, None, reason).

        The default asks `generator()` who drafts and runs the generation sub-loop around
        it (D456): the model inner loop (design -> build -> fast test -> patch ...) for
        `Model` or `None`, the shared draft/build/check loop for a template, a catalog or a
        solver. A problem whose generation is neither overrides this."""
        from .generation import _generate_with_model
        from .sources import Model, iterate

        source = self.generator(subgoal, state)
        if source is None or isinstance(source, Model):
            return _generate_with_model(self, subgoal, method, state, human)
        return iterate(self, source, subgoal, state)

    #: Which declared sources belong in the STATIC prompt prefix (D449): the ones that cannot
    #: change during a run. The record's read-back is deliberately not among them -- it grows
    #: as the run measures things, and would break the server's prefix cache every turn.
    static_knowledge: tuple[str, ...] = ("sheet", "library", "mined", "digest")

    def tools(self, subgoal: str | None, state: LoopState, stage: str = "prototype",
              checked: Any = None) -> list:
        """The tools a model turn on `subgoal` may call (D505), when the request turns them on:
        the loop's generic set (`compute`, the problem's own `check` in the prototype stage,
        `history`, `knowledge`) by default; a problem adds its own or narrows the set."""
        from .tools import loop_tools

        return loop_tools(self, subgoal, state, checked=checked, stage=stage)

    def prompt_prefix(self, subgoal: str | None, state: LoopState) -> str:
        """The STATIC part of every prompt for this part -- contract, knowledge,
        reply shape -- which the loop places FIRST so the model server's prefix
        cache is reused turn after turn (D422); the changing part (source, failure,
        notes, results) goes last. The default is the declared `knowledge()`'s static
        sources; return "" to keep prompts as they are."""
        mentor = self.knowledge()
        if mentor is None:
            return ""
        return mentor.prefix(state, keys=self.static_knowledge, focus=subgoal or None)   # D550: the part in hand

    def objectives(self) -> "Objectives":
        """THE OBJECTIVE AS A VECTOR (D511): `flux_loop.Objectives`, ordered, the first the goal
        when it names one. The frontier's axes, the decision, the early stop and which of a
        part's admitted designs stands at a reload all derive from it below; a problem with
        no objectives (the default) has no frontier and decides on the first thing measured."""
        return Objectives()

    def standing(self, state: LoopState) -> dict[str, Any]:
        """WHERE THE CAMPAIGN STANDS, for the results table (D497, Cedric: "it is unsure what
        is the target / what result we want / what is the problem we work on now"):
        {"goal": one line, "now": what this pass is doing, "composed": the whole design's
        last numbers, "parts": {part: its constraint and numbers, one line}}. Any key may be
        missing; the default says nothing and the table shows the parts alone."""
        return {}

    def locate(self, failure: str, artifact: str) -> list[int]:
        """Line numbers the failure points at (1-based), so a patch prompt can show
        a window around them instead of the whole artifact (D422). The default reads
        `file:LINE:` / `line LINE` from the failure text; a numeric failure with no
        location returns [] and the whole artifact is shown."""
        found = {int(m) for m in re.findall(r":(\d+)(?::\d+)?[:\s]", failure)}
        found |= {int(m) for m in re.findall(r"\bline (\d+)", failure, re.I)}
        n = artifact.count("\n") + 1
        return sorted(x for x in found if 1 <= x <= n)

    def design_prompt(self, subgoal: str | None, method: str, state: LoopState,
                      human: str | None, prior: Candidate | None, prior_why: str
                      ) -> tuple[str, dict | None]:
        """A prompt (and schema) asking for a whole candidate; `prior` present means
        "rework this one" and `prior_why` is why it was refused."""
        raise NotImplementedError(f"{self.name}: design_prompt or generate")

    def parse_design(self, reply: str, subgoal: str | None) -> tuple[Candidate | None, str]:
        """(candidate, "") or (None, reason)."""
        raise NotImplementedError(f"{self.name}: parse_design or generate")

    def patch_prompt(self, subgoal: str | None, cand: Candidate, failure: str,
                     state: LoopState) -> tuple[str, dict | None]:
        """The edit request; the loop's default is find/replace edits (D414), shown
        as a window around the failing lines when the failure locates them (D422)."""
        view = focus_window(cand.artifact, self.locate(failure, cand.artifact),
                            state.request.patch_context_lines)
        return patch_prompt(cand.name, cand.artifact, failure, view=view), patch_schema()

    def rewrite_prompt(self, subgoal: str | None, cand: Candidate, failure: str,
                       state: LoopState) -> tuple[str, dict | None]:
        """The full-rewrite fallback when patching is unusable."""
        return self.design_prompt(subgoal, "", state, None, cand, failure)

    def apply_tools(self, subgoal: str | None, cand: Candidate, reply: str,
                    state: LoopState) -> tuple[Candidate, str]:
        """Problem-supplied tools a reply may invoke (a table oracle, a solver):
        return the candidate with their results applied and any refusal text."""
        return cand, ""

    def ladder(self) -> "Ladder | None":
        """THE IMPROVE LADDER, declared (D517): a `flux_loop.Ladder` -- which steps, the sweep's
        register counts, the passes' fractions -- and the loop runs it (`flux_loop.ladder`):
        `improve_options` and `route` derive from it below. None (the default) = a part sent
        back is reworked by the generation sub-loop, as `improve` says."""
        return None

    def siblings(self, part: str, proto: str, state: LoopState) -> dict | None:
        """A verified design of `part` in a SIBLING campaign of this problem (the ladder's
        `import` step, D506): `{campaign, artifact, digest, value, ...}` or None. The loop's
        own (D540): another campaign of this document in the same store, read from its rows."""
        from .records import sibling_design

        return sibling_design(self, part, proto, state)

    def redesign_note(self, part: str, state: LoopState, depth: dict, nth: int) -> str:
        """What a DIFFERENT-algorithm pass is told (the ladder's `redesign` step, D499) beyond
        the stage's own prompt: why the design on record is not good enough, what to try."""
        return (f"ALTERNATIVE ALGORITHM (the {nth}{'st' if nth == 1 else 'nd'} asked for): the design on record "
                f"for {part} passes every input but does not reach the goal. Do NOT refine it -- write a "
                "DIFFERENT algorithm for the same function, verified the same way; the tool then measures "
                "it and keeps whichever is better.")

    def prototype(self) -> "Prototype | None":
        """PROTOTYPE FIRST (D424), declared (D515): a `flux_loop.Prototype` -- what `design(x)`
        computes for a part, what an input is, what the gate demands, the toolkit and its
        documentation, the check that judges a prototype -- and the loop runs the stage: the
        language's rules, the history, the example, the repair turns, the family search.
        None (the default) = no such stage; the model writes the target directly."""
        return None

    def describe_failure(self, subgoal: str | None, verdict: Verdict) -> str:
        """The failure text a repair prompt carries; override to add structure
        (regions, decoded values) the model cannot misread."""
        return verdict.why

    def transpile(self, prototype: str, subgoal: str | None, state: LoopState,
                  pipeline: int | None = None) -> Candidate | None:
        """A verified prototype as a TARGET candidate, mechanically (D478): the problem's
        transpiler turns the prototype language into the artifact language with no model
        turn, or returns None when it has no transpiler (the model transcribes, D424).
        A prototype the transpiler cannot spell should be refused by the prototype check,
        naming the construct, so the prototype stage fixes it rather than this failing.
        `pipeline` is the register count the ladder's sweep asks for (D496); None = the
        admitted design's, or none."""
        return None


class EvaluatorRole(_Role):
    """test / analytical / simulation nodes: the fast check, the gate, the chain."""

    def build(self, cand: Candidate, subgoal: str | None, state: LoopState) -> Any:
        """Compile / elaborate / sanity-check; raise BuildError(text) to refuse."""
        raise NotImplementedError(f"{self.name}: build")

    def fast_check(self, built: Any, cand: Candidate, subgoal: str | None,
                   state: LoopState) -> tuple[int, str]:
        """test/validate: (failures, failure text). Milliseconds; the generator
        iterates against it. Default: nothing to check, (0, "")."""
        return 0, ""

    def judge(self, built: Any, cand: Candidate, subgoal: str | None,
              state: LoopState) -> Verdict:
        """The gate: exhaustive or proof-grade where possible."""
        raise NotImplementedError(f"{self.name}: judge")

    def critique(self, kind: str, subject: Any, state: LoopState) -> Verdict:
        """critique (D433): the adversary. `kind` is "decomposition" (subject: the part
        names), "candidate" (subject: an admitted `Candidate`, the gate already passed)
        or "decision" (subject: the `Scored` pick). `ok=False` with `why` sends a division
        back to be redone, a candidate back to the writer with `why` as the failure text
        (at most `request.critique_rounds` times: the gate is the ground truth, the critic
        can delay, never veto), or a decision into the report's caveats. The default has
        no critic: everything is ok."""
        return Verdict(True, 0.0, "")

    def compose(self, admitted: dict[str, Candidate], state: LoopState
                ) -> Candidate | list[Candidate] | None:
        """Combine admitted sub-goals into what the outer chain measures. Default: none
        (single-goal problems measure the admitted candidate).

        MANY is allowed (D455), because what a parent evaluates after its sub-loops finish is
        the parent's to declare: one composed artifact, the children's own decisions side by
        side, or every combination of their frontiers. `state.children` holds each child's
        whole `LoopResult`, so a parent that wants pairs can build them here."""
        if len(admitted) == 1 and not self.subgoals():
            return next(iter(admitted.values()))
        return None

    def stages(self) -> list[str]:
        """Costed stages in rising order; the last is the one the report quotes.

        The EVALUATION component may add a stage of its own below the problem's (D461: a
        learned screen), which is why this asks it. A problem that overrides `stages` and wants
        that possibility returns `self.chained([...])`."""
        return self.chained(["screen"])

    #: D505: the loop plan applied to this problem this pass (`flux_loop.plan`); `chained` keeps
    #: the stages it names
    _plan: dict[str, Any] | None = None

    def chained(self, own: list[str]) -> list[str]:
        """The problem's own stages, plus whatever the evaluation component adds below them
        (D461). Unchanged when no component is filled or it has nothing to add."""
        keep = (self._plan or {}).get("stages")
        if isinstance(keep, list) and keep:
            own = [s for s in own if s in keep]      # D505: the plan's subset, in the problem's order
        who = self.roles().evaluator
        add = getattr(who, "stages", None)
        return list(add(self, list(own))) if callable(add) else list(own)

    def cutoff(self, stage: str, scored: list[Scored], state: LoopState
               ) -> list[Scored] | tuple[list[Scored], str]:
        """Which of `stage`'s results are worth the NEXT stage, and why not the others (D454).

        The chain's own stopping rule, applied between stages: a candidate that cannot be the
        answer must not cost a placement to confirm it. `flux_loop.cutoff` has the two shapes
        this repository needs -- `above`/`below` for a requirement the answer must clear, and
        `within_best` for a band around this run's own leader, whose threshold is not knowable
        before the run. Return the survivors, or `(survivors, why)` so the report can say what
        was cut and by what rule; the default cuts nothing and the frontier decides alone.

        This is NOT the frontier: the frontier keeps what nothing else beats on every axis,
        which is about trade-offs. A cutoff is about spending -- it drops candidates that are
        still on the frontier when they cannot clear a requirement.

        The EVALUATION component's own stage gets the component's rule (D461)."""
        answer = self.role_cutoff(stage, scored, state)
        if answer is not None:
            return answer
        return list(scored)

    def role_cutoff(self, stage: str, scored: list[Scored], state: LoopState) -> Any | None:
        """The evaluation component's cutoff for its own stage, if it has one (D461). None
        means it has nothing to say about this stage."""
        who = self.roles().evaluator
        rule = getattr(who, "cutoff", None)
        return rule(stage, list(scored), state) if callable(rule) else None

    def measure(self, cand: Candidate, stage: str, state: LoopState
                ) -> dict[str, Any] | None:
        """Run one costed stage; the loop caches by (stage, artifact) when a cache is
        open. None = could not measure. Numbers in the dict become `Scored.metrics`,
        anything else `Scored.payload` (so keep it JSON-able: the cache stores it).

        The EVALUATION component measures its own stage (D461: a learned screen predicts);
        everything else is the problem's. A problem that overrides `measure` and wants a
        learned stage asks `self.role_measure(...)` first."""
        return self.role_measure(cand, stage, state)

    def role_measure(self, cand: Candidate, stage: str, state: LoopState
                     ) -> dict[str, Any] | None:
        """The evaluation component's answer for `stage`, or None when the stage is not its
        own (D461) -- the shape a problem's own `measure` can defer to."""
        who = self.roles().evaluator
        run = getattr(who, "measure", None)
        return run(self, cand, stage, state) if callable(run) else None

    def cache_key(self, cand: Candidate, stage: str, state: LoopState) -> str:
        """What makes a measurement of `cand` on `stage` the same one in the loop's cache
        (D567): the candidate's key by default -- its text or its knobs. A world whose
        numbers also depend on something the candidate does not carry (the clock the tools
        are constrained to, the mapping) adds it here, and keeps no cache of its own."""
        return cand.key()

    def measure_batch(self, cands: list[Candidate], stage: str, state: LoopState
                      ) -> list[dict[str, Any] | None]:
        """One costed stage over many candidates at once (D446), one result per candidate
        in the order given -- the ABI's batch invariant: a metrics dict, `{"error": why}`
        or None for one the stage could not measure. The default is `measure` per
        candidate through the loop's cache, the misses `request.workers` at a time (D525);
        a problem with its own batch machinery (a Ray backend, a cache keyed on its tool
        fingerprints) overrides this and owns its caching (`cache_suffix` None)."""
        from .measure import measure_pool

        return measure_pool(self, state, cands, stage)

    def analytic_stages(self) -> frozenset[str]:
        """Stages whose numbers are modelled, not simulated or placed -- what the record's
        method tag says about them (D446). Empty: every stage is measured.

        A learned stage is modelled by construction, so the EVALUATION component's own stages
        join whatever the problem declares (D461) -- the record must never report a prediction
        as a measurement."""
        return self.role_analytic()

    def role_analytic(self, own: frozenset[str] = frozenset()) -> frozenset[str]:
        """`own` plus the evaluation component's modelled stages (D461)."""
        who = self.roles().evaluator
        tell = getattr(who, "analytic", None)
        return frozenset(own) | (frozenset(tell()) if callable(tell) else frozenset())

    def analytic_metrics(self) -> frozenset[str]:
        """Metrics that are modelled on EVERY stage (a storage model beside a simulated
        speedup), for the record's per-metric method tag (D446)."""
        return frozenset()

    def calibrated(self, biases: list[Any], state: LoopState) -> None:
        """What a costly stage just said about a cheap one (D464), once per pair of
        stages that measured the same designs: `flux_loop.calibrate.Bias` per metric,
        with the ratio, its spread and how many designs it was computed over.

        The loop has already reported them and left them in `state.bias`, keyed by
        `(stage, metric)`; this hook is for a problem that wants to DO something with
        them -- correct its own fast model, tighten a cutoff, write them where the next
        run will read them. Doing nothing is a fine answer: the numbers are in the
        report and the record either way."""
        return None

    def evaluator_name(self, stage: str) -> str:
        """The record's evaluator provenance for a stage (D426): the registry name of the
        backend that measured it, `@stage`. The default names the problem -- or the EVALUATION
        component, for a stage that is its own (D461)."""
        return self.role_evaluator_name(stage) or f"{self.name}@{stage}"

    def role_evaluator_name(self, stage: str) -> str | None:
        """What the evaluation component calls itself for its own stage (D461), or None."""
        who = self.roles().evaluator
        named = getattr(who, "evaluator_name", None)
        return named(stage) if callable(named) else None

    def cache_suffix(self) -> str | None:
        """The sidecar the loop's measurement cache lives in; None = the problem caches
        for itself (its `measure_batch` owns it) and the loop opens none."""
        return f"{self.name}.json"


class Problem(MentorRole, OrchestratorRole, GeneratorRole, EvaluatorRole):
    """What a problem supplies. Every method has a default so a problem implements
    only what makes it different; the docstrings say which node each one serves.

    Required in practice: `objective`, `build`, `judge`, and then either the parts
    path -- `subgoals` (or one), with the model pair (`design_prompt` + `parse_design`)
    or `generate` -- or, for batches: `search`, `stages`, `measure` or `measure_batch`
    and the frontier/decision hooks (D446)."""

    name: str = "problem"
