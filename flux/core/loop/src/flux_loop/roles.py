"""The four roles as SWAPPABLE components, each with an AI and a no-AI half (D460).

Cedric's requirement, stated at the end of the single-loop work: every one of the four boxes in
the drawing must work with a model and without one, and it must be easy to switch which.

    Orchestration   code, rules or a user-given division   <OR>   a model choosing what next
    Generation      a template, a catalog, a solver        <OR>   a model writing the artifact
    Evaluation      analytical models and real tools       <OR>   a learned model, plus the tools
    Knowledge       what a person fed in                   <OR>   what a model mined from data

Two of these were already components: generation's sources (D456) and knowledge's declared
sources (D449). Orchestration only had DEFAULTS -- the model menu was what a problem got unless
it overrode the hook, and the no-model policy was something each study wrote for itself. A
default is not a choice you can make from a document or a flag, which is what this module adds:
a `Roles` bundle, a registry from NAME to component, and one place a document, a command line or
an application says who fills which role.

`Roles()` with every slot `None` is exactly today's behaviour -- the problem's own hooks. A slot
that is filled is asked FIRST and may still decline (return None), so a component can have an
opinion about one decision and none about the others.

The evaluation slot is declared here and carried through, and the learned evaluator that makes
it a real choice is the next pass (the tools half is `stages`, as it has always been).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Callable, Iterable, Protocol, runtime_checkable

__all__ = ["AgentOrchestrator", "Given", "ModelOrchestrator", "Orchestrator", "ROLES", "Roles", "Rules",
           "available", "available_roles", "make", "make_role", "register",
           "register_role", "rig"]

#: The four boxes, in the drawing's order. `knowledge` is the mentor role's slot: the repository
#: calls the role "mentor" (`MentorRole`) and what it holds "knowledge".
ROLES = ("orchestrator", "generator", "evaluator", "knowledge")


@runtime_checkable
class Orchestrator(Protocol):
    """WHAT TO DO NEXT, as a component rather than as a default.

    A component implements what it has an opinion about and nothing else: a method it
    does not define, or that returns None, is the problem's own. So `Rules` decides the
    planning and leaves the division alone, and `sweep` supplies batches (D465) without
    knowing what a part is.
    """

    name: str

    def divide(self, problem: Any, state: Any, critique: str | None = None) -> Any | None:
        """The parts this pass works on: names, `SubLoop`s, or both (D455). None: the
        problem's own division."""
        ...

    def plan_next(self, problem: Any, menu: list[str], state: Any,
                  human: str | None) -> tuple[str, str] | None:
        """(part, method) for the next step, chosen from `menu`. None: the problem's own."""
        ...

    def next_work(self, problem: Any, state: Any, waiting: list[str]) -> str | None:
        """"part", "batch" or "improve" when more than one is available
        (D457/D463). None: the problem's own."""
        ...

    # An orchestrator MAY also be a search policy of its own, and then it supplies the
    # batches instead of the problem (D465):
    #
    #     def search(self, problem, state) -> Iterator[list[Candidate]]: ...


@dataclass(frozen=True)
class Roles:
    """Who fills each role this pass. A `None` slot is the problem's own hooks.

    `generator` is a `flux_loop.sources` source and `knowledge` a `flux_knowledge.Mentor`:
    the components those roles already had (D456/D449), reachable from the same bundle so an
    application swaps one line rather than four kinds of thing.
    """

    orchestrator: Any | None = None
    generator: Any | None = None
    evaluator: Any | None = None
    knowledge: Any | None = None

    def with_role(self, role: str, who: Any) -> "Roles":
        _check_role(role)
        return replace(self, **{role: who})

    def named(self) -> dict[str, str]:
        """What is filled, as names -- for a report, a log line or the mentor tab."""
        return {r: getattr(getattr(self, r), "name", type(getattr(self, r)).__name__)
                for r in ROLES if getattr(self, r) is not None}


# --------------------------------------------------------------- the no-AI orchestrator
@dataclass
class Rules:
    """Code and logic decide: the problem's declared division, worked in order, no model call.

    This is what a study with a fixed chain writes by hand -- "the next part that is not
    proven yet" -- named so it can be SELECTED instead of written again. The loop has already
    filtered the menu by its cooldown rule before asking, so the first entry is the next part
    that is worth trying.
    """

    name: str = "rules"

    def divide(self, problem: Any, state: Any, critique: str | None = None) -> Any | None:
        return None                      # the problem declares its own parts

    def plan_next(self, problem: Any, menu: list[str], state: Any,
                  human: str | None) -> tuple[str, str] | None:
        return (menu[0], "") if menu else None

    def next_work(self, problem: Any, state: Any, waiting: list[str]) -> str | None:
        return _in_hand_first(state, waiting)


class Given:
    """The USER provides the division: these parts, in this order, nothing asked of a model.

    `parts` are names the generator writes. A part that is itself a whole loop is a `SubLoop`
    and belongs in `parts` too -- the loop takes either (D455).
    """

    def __init__(self, parts: Iterable[Any] = (), name: str = "given") -> None:
        self.parts = tuple(parts)                    # D566: an ORDER over the problem's own parts; () = as declared
        self.name = name

    def divide(self, problem: Any, state: Any, critique: str | None = None) -> Any | None:
        """The problem's declared parts (the document's `parts:`), in the given order when one
        was given (D566: parts are named once, in the document; `given` only orders them)."""
        declared = list(problem.subgoals() or [])
        if not self.parts:
            return declared or None
        names = [getattr(p, "name", p) for p in self.parts]
        stray = [n for p, n in zip(self.parts, names) if isinstance(p, str) and declared and n not in declared]
        if stray:                                    # a SubLoop given as a part is a part of its own (D455)
            raise ValueError(f"`given` names {stray}, which the problem does not have (its parts: {declared})")
        return list(self.parts) + [p for p in declared if p not in names]

    def plan_next(self, problem: Any, menu: list[str], state: Any,
                  human: str | None) -> tuple[str, str] | None:
        """In the order they were given, not in the order the loop happens to hold them."""
        for part in self.parts:
            key = getattr(part, "name", part)
            if key in menu:
                return str(key), ""
        return (menu[0], "") if menu else None

    def next_work(self, problem: Any, state: Any, waiting: list[str]) -> str | None:
        return _in_hand_first(state, waiting)


# ------------------------------------------------------------------ the AI orchestrator
@dataclass
class ModelOrchestrator:
    """A model decides: which part next and what to try on it, from a schema-constrained menu
    (D413), and which kind of work a step is for when there is a choice (D457).

    This is the loop's own default behaviour, named -- so that "the model orchestrates" is
    something a document or a flag can SAY, and so that turning it off is a choice rather than
    an override someone has to write.
    """

    name: str = "llm"

    def divide(self, problem: Any, state: Any, critique: str | None = None) -> Any | None:
        """The division stays the problem's: dividing needs the task's own words, which the
        problem has and this component does not. A task document asks its model to divide
        when it says `"parts": "decompose"` (D431)."""
        return None

    def plan_next(self, problem: Any, menu: list[str], state: Any,
                  human: str | None) -> tuple[str, str] | None:
        from .problem import plan_with_model

        return plan_with_model(problem, menu, state, human)

    def next_work(self, problem: Any, state: Any, waiting: list[str]) -> str | None:
        """Asks the model which kind of work the next step is for, from the kinds that
        are actually available -- D463 added `improve`: a design an evaluator sent back with
        its numbers. Falls back to what is in hand when there is no model or no usable
        answer."""
        from .model import _ask, _json

        kinds = (["improve"] if state.improve else []) + (["part"] if waiting else [])
        kinds = kinds + ["batch"]
        if state.proposer is None:
            return _in_hand_first(state, waiting)
        lines = ["A design search is running. Choose what the next step should do."]
        if state.improve:
            lines.append(f"improve: {len(state.improve)} design(s) an evaluator sent back "
                         f"to be redrafted: {state.improve[0].why[:160]}")
        if waiting:
            lines.append(f"part: {len(waiting)} part(s) still need work: "
                         + ", ".join(waiting))
        lines.append("batch: the search has more candidates to propose")
        lines.append("Reply with ONLY {\"next\": \"<one of "
                     + ", ".join(kinds) + ">\"}.")
        schema = {"type": "object", "properties": {"next": {"enum": kinds}},
                  "required": ["next"]}
        try:
            doc = _json(_ask(state, "\n".join(lines), schema).text)
        except Exception:  # noqa: BLE001 -- an unanswered choice is not a failed run
            return None
        if isinstance(doc, dict) and doc.get("next") in kinds:
            return str(doc["next"])
        return None


# ------------------------------------------------------------- the AGENT orchestrator (D505)
@dataclass
class AgentOrchestrator:
    """A model decides WITH INSTRUMENTS and says why: which part next, which kind of work,
    which step of a design's improve ladder -- from the same menus the rules walk, after
    reading the standings, the part's history, the decisions already taken and the knowledge
    through tools inside its turn. Every pick is recorded on the campaign as a `decision`
    event with the model's reason, shown in the log, and falls back to the rules when the
    model does not answer or answers off the menu.

    What it may NOT decide is the same as for the `llm` orchestrator: the gate. A design is
    admitted by the exhaustive test, never by an opinion.
    """

    name: str = "agent"
    fallback: Any = field(default_factory=lambda: Rules())

    def divide(self, problem: Any, state: Any, critique: str | None = None) -> Any | None:
        return None                      # the division is the plan's (D505 planning) or the problem's

    def plan_next(self, problem: Any, menu: list[str], state: Any,
                  human: str | None) -> tuple[str, str] | None:
        if state.proposer is None or len(menu) <= 1:
            return None
        lines = [f"Parts still to make: {', '.join(menu)}.",
                 f"Made so far: {', '.join(sorted(state.admitted)) or 'none'}.",
                 "Choose the next part and, in one line, the method to try for it."]
        got = self._decide(problem, state, "next part", lines, menu, extra={"method": {"type": "string"}},
                           human=human)
        if got is None:
            return None
        return got["pick"], str(got.get("method") or "")[:120]

    def next_work(self, problem: Any, state: Any, waiting: list[str]) -> str | None:
        kinds = (["improve"] if state.improve else []) + (["part"] if waiting else []) + ["batch"]
        if state.proposer is None or len(kinds) <= 1:
            return None
        lines = ["What should the next step do?"]
        if state.improve:
            lines.append(f"improve: {len(state.improve)} design(s) an evaluator sent back to be improved "
                         f"-- first: {state.improve[0].why[:200]}")
        if waiting:
            lines.append(f"part: {len(waiting)} part(s) still to make: {', '.join(waiting)}")
        lines.append("batch: the search proposes more candidates")
        got = self._decide(problem, state, "what next", lines, kinds)
        return got["pick"] if got else None

    def choose_improve(self, problem: Any, item: Any, options: list, state: Any) -> Any | None:
        if state.proposer is None or len(options) <= 1:
            return None
        names = [o.name for o in options]
        lines = [f"The design {item.candidate.name}" + (f" (part {item.subgoal})" if item.subgoal else "")
                 + f" came back from the {item.stage or 'gate'} stage: {item.why[:400]}",
                 "Its ladder -- what could be done for it, with the code's opinion of what is due:"]
        lines += [f"  - {o.name}{' (due)' if o.due else ''}: {o.why}" for o in options]
        lines.append("Pick ONE option. Prefer what the numbers on record say over the code's order when "
                     "they disagree; an option that repeats a measured failure is a wasted pass.")
        got = self._decide(problem, state, f"improve {item.subgoal or item.candidate.name}", lines, names)
        if got is None:
            return None
        return next(o for o in options if o.name == got["pick"])

    # ---- the turn
    def _decide(self, problem: Any, state: Any, what: str, lines: list[str], choices: list[str],
                extra: dict | None = None, human: str | None = None) -> dict | None:
        """One decision: a turn with the orchestrator's tools, a schema-constrained answer
        {"pick": one of `choices`, "why": ...}, recorded on the campaign. None when the model
        did not answer or answered off the menu -- the caller falls back to the rules."""
        from .model import _ask, _json
        from .observe import _phase
        from .tools import orchestrator_tools

        objectives = {}
        try:
            objectives = problem.standing(state) or {}
        except Exception:  # noqa: BLE001
            pass
        head = ["You are the ORCHESTRATOR of a design campaign; you decide what the loop spends its next step on.",
                f"OBJECTIVE: {objectives.get('goal', problem.name)}",
                f"NOW: {objectives.get('now', '')}" if objectives.get("now") else "",
                (f"THE WHOLE: {objectives.get('composed')}" if objectives.get("composed") else ""),
                (human or ""),
                "You have tools: standings() -- every part's numbers; history(part) -- what was tried for a "
                "part and why it was refused; decisions() -- the picks already taken this campaign and their "
                "reasons; knowledge(query) -- the method sheet and the papers. Read what you need, then answer.",
                *lines,
                'Reply with ONLY JSON: {"pick": "<one of ' + ", ".join(choices) + '>", "why": "<one or two lines: the numbers that decide it>"'
                + (', "method": "<one line>"' if extra and "method" in extra else "") + "}"]
        props: dict = {"pick": {"type": "string", "enum": list(choices)}, "why": {"type": "string"}}
        props.update(extra or {})
        schema = {"type": "object", "properties": props, "required": ["pick", "why"]}
        prompt = "\n".join(l for l in head if l)
        with _phase(f"orchestrate: {what}", why=", ".join(choices)) as out:
            try:
                reply = _ask(state, prompt, schema,
                             tools=orchestrator_tools(problem, state) if state.request.tools or "orchestrate" in (state.request.agent or ()) else None).text
            except Exception as exc:  # noqa: BLE001
                out["error"] = f"the agent did not answer ({exc!s:.120}); the rules decide"
                state.say(f"  orchestrate: the agent did not answer ({exc!s:.80}); the rules decide")
                return None
            doc = _json(reply)
            if not isinstance(doc, dict) or doc.get("pick") not in choices:
                out["error"] = f"off the menu: {str(reply)[:200]}; the rules decide"
                state.say(f"  orchestrate: the agent answered off the menu; the rules decide")
                return None
            out["pick"] = str(doc["pick"])
            out["why"] = str(doc.get("why") or "")
        why = str(doc.get("why") or "").strip()
        state.say(f"  orchestrate [{what}]: {doc['pick']} -- {why[:200]}")
        _record_decision(state, what, str(doc["pick"]), why, choices)
        return doc


def _record_decision(state: Any, what: str, pick: str, why: str, choices: list[str]) -> None:
    """The pick on the campaign, as an event the `decisions()` tool and the report read back."""
    from .ledger import Kind

    state.ledger.note(Kind.DECISION, what, what=what, pick=pick, why=why[:600], choices=list(choices),
                      step=int(state.step or 0))


def _in_hand_first(state: Any, waiting: list[str]) -> str:
    """The no-model rule for what a step is for (D463): improve the design already
    in hand, then the declared parts, then propose something new. A design whose numbers
    say it is nearly right is the cheapest progress available."""
    if getattr(state, "improve", None):
        return "improve"
    return "part" if waiting else "batch"


# ------------------------------------------------------------------------- the registry
#: role -> name -> factory(config) -> component. A factory takes the spec's own keys as a dict
#: so a document can configure a component ("given" needs its parts).
_FACTORIES: dict[str, dict[str, Callable[[dict[str, Any]], Any]]] = {r: {} for r in ROLES}

#: Why a role's component cannot come from a name alone, said in the error rather than guessed.
_IN_CODE = {
    "generator": ("a `template` or `solver` generator is a CALLABLE the problem supplies "
                  "(flux_loop.sources.Template / Solver); name it in code, not in a document"),
    "evaluator": ("the no-AI evaluation half is a problem's `stages` (commands, ABI evaluators, "
                  "analytical models, its own `measure`) and needs no component; `surrogate` (D560) "
                  "is the model half: it predicts the costly stage from the record and orders, never decides"),
    "knowledge": ("the human half is a problem's declared sources (flux_knowledge.Mentor over "
                  "Corpus / Library / RecordReadback / Notes, D449) and is built in code; "
                  "`mined` is the half extracted from this project's own data (D462)"),
}


def _check_role(role: str) -> None:
    if role not in ROLES:
        raise ValueError(f"{role!r} is not one of the four roles: {', '.join(ROLES)}")


def register(role: str, name: str, factory: Callable[[dict[str, Any]], Any], *,
             replace: bool = False) -> None:
    """Add a component under `name` for `role`. `factory` is handed the spec's own keys."""
    _check_role(role)
    if not name or not isinstance(name, str):
        raise ValueError(f"a component name must be a non-empty string, not {name!r}")
    if name in _FACTORIES[role] and not replace:
        raise ValueError(f"{role} {name!r} is already registered; pass replace=True")
    _FACTORIES[role][name] = factory


def available(role: str) -> list[str]:
    """The names this role can be switched to, sorted."""
    _check_role(role)
    return sorted(_FACTORIES[role])


def make(role: str, spec: Any) -> Any | None:
    """One role's component from a name (`"rules"`), a configured spec
    (`{"given": {"parts": [...]}}` or `{"name": "given", "parts": [...]}`), an already-built
    component (returned as it is), or None (the problem's own hooks)."""
    _check_role(role)
    if spec is None:
        return None
    if not isinstance(spec, (str, dict)):
        return spec                      # already a component: the code path
    name, config = _spec(role, spec)
    factory = _FACTORIES[role].get(name)
    if factory is None:
        known = ", ".join(available(role)) or "nothing yet"
        extra = _IN_CODE.get(role)
        raise ValueError(f"{role} {name!r} is not registered; available: {known}"
                         + (f". {extra}" if extra else ""))
    return factory(config)


def _spec(role: str, spec: str | dict[str, Any]) -> tuple[str, dict[str, Any]]:
    if isinstance(spec, str):
        return spec, {}
    if "name" in spec:
        return str(spec["name"]), {k: v for k, v in spec.items() if k != "name"}
    if len(spec) == 1:
        (name, config), = spec.items()
        return str(name), dict(config) if isinstance(config, dict) else {"value": config}
    raise ValueError(f"a {role} spec is a name, {{\"name\": ..., ...}} or a single "
                     f"{{name: config}} pair, not {sorted(spec)}")


def rig(**specs: Any) -> Roles:
    """A `Roles` from names or specs: `rig(orchestrator="rules", generator="model")`."""
    for role in specs:
        _check_role(role)
    return Roles(**{role: make(role, spec) for role, spec in specs.items()})


register("orchestrator", "rules", lambda _c: Rules())
register("orchestrator", "given", lambda c: Given(c.get("parts") or c.get("value") or ()))
register("orchestrator", "llm", lambda _c: ModelOrchestrator())
register("orchestrator", "agent", lambda _c: AgentOrchestrator())
register("knowledge", "mined", lambda c: _mined(c))
register("knowledge", "digest", lambda c: _digest_mentor(c))
register("knowledge", "sources", lambda c: _sources_mentor(c))
register("generator", "model", lambda _c: _source("Model"))
register("generator", "catalog", lambda c: _catalog(c))


def _source(cls: str) -> Any:
    from . import sources

    return getattr(sources, cls)()


def _mined(config: dict[str, Any]) -> Any:
    """Knowledge extracted from the data this project produced (D462): mined facts and the
    conclusions drawn from them, as a `Mentor` with that one source. `db` defaults to the
    run's own campaign store, so switching it on needs no configuration."""
    from flux_knowledge import Mentor, Mined

    known = {"db", "calibration", "max_facts", "static"}
    unknown = sorted(set(config) - known - {"value"})
    if unknown:
        raise ValueError(f"a `mined` knowledge source takes {sorted(known)}, not {unknown}")
    kw = {k: v for k, v in config.items() if k in known}
    if isinstance(kw.get("calibration"), str):
        kw["calibration"] = (kw["calibration"],)
    elif "calibration" in kw:
        kw["calibration"] = tuple(kw["calibration"])
    if isinstance(config.get("value"), str):
        kw.setdefault("db", config["value"])
    return Mentor([Mined(**kw)])


def _digest_mentor(config: dict[str, Any]):
    """`digest` (D576): the library's key points, digested once into the run's store."""
    from flux_knowledge import Digest, Mentor

    return Mentor([Digest(db=str(config.get("db") or config.get("value") or ""))])


def _sources_mentor(config: dict[str, Any]):
    """`{sources: {names: [mined, digest]}}`: several of the model-side sources at once."""
    from flux_knowledge import Digest, Mentor, Mined

    names = list(config.get("names") or config.get("value") or [])
    made = []
    for n in names:
        if n == "mined":
            made.append(Mined())
        elif n == "digest":
            made.append(Digest())
        else:
            raise ValueError(f"knowledge source {n!r} is not one a role can add (mined, digest)")
    return Mentor(made)


def _catalog(config: dict[str, Any]) -> Any:
    """Designs that already exist, as paths on disk -- the one generator a document CAN name
    on its own, because a path needs no callable."""
    from .sources import Catalog, from_file

    items = config.get("items") or config.get("value") or ()
    items = [items] if isinstance(items, str) else list(items)
    if not items:
        raise ValueError("a `catalog` generator needs the paths of the designs to try")
    return Catalog(items, lambda item, attempt: from_file(item, attempt))


#: `flux_loop.make_role` / `available_roles` / `register_role`: the same three functions
#: under names that read unambiguously from outside this module.
make_role = make
available_roles = available
register_role = register

# the DSE policies register themselves as orchestrators (D553); imported last, they import Rules from here
from . import dse as _dse  # noqa: E402,F401
from . import surrogate as _surrogate  # noqa: E402,F401 -- the evaluator's model half registers itself (D560)
