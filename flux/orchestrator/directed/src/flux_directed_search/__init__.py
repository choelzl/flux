"""A search a model directs, one step at a time, from a menu the application registers.

WHAT THIS REPLACES. The first version of this loop hard-coded two things it could do — enumerate
a scope, ask for proposals — in three places at once: the menu inside the prompt, an `if/elif`
dispatch, and a fallback list. Three copies of one list, which is a drift bug waiting to happen,
and adding a third capability meant editing all three plus the stopping logic.

Here an ACTION is data: a name, the line the model reads, the parameters it may send, and the
callable that performs it. The prompt menu is GENERATED from the registry, so the menu and the
dispatch cannot disagree, and a new capability is a registration rather than an edit to the loop.

WHAT THE LOOP OWNS, because every long search needs it and none of it is domain knowledge:
validating the model's choice against the registry, falling back when the answer is unusable,
tracking what each step yielded, refusing to run forever, reporting coverage, and stopping.

WHAT IT DOES NOT OWN: what the actions mean. `enumerate` and `propose` are the interconnect
study's words. A memory study would register different ones and this file would not change.

The model DIRECTS but cannot narrow silently: every declared variant it never ran is reported.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

__all__ = ["Action", "DirectedSearch", "Outcome", "StepRecord", "SearchReport"]


@dataclass
class Outcome:
    """What one step produced. `gained` is the honest measure for a step whose job is to find
    candidates: ones never tried before, counted from the store rather than claimed by the
    action.

    `progressed` exists because not every useful step finds a candidate. Asking for real
    place-and-route on a design already in the store gains nothing and accomplishes something,
    and a barren check that only counted new candidates would read it as a wasted step and stop
    a search that was working. Left None it defaults to `gained > 0`, so an action that finds
    things needs to say nothing.
    """

    gained: int = 0
    detail: str = ""
    payload: Any = None
    progressed: bool | None = None

    @property
    def was_productive(self) -> bool:
        return self.gained > 0 if self.progressed is None else self.progressed


@dataclass
class Action:
    """One thing the orchestrator may choose to do."""

    name: str
    menu: str                       # the line the model reads, ending in what it is for
    run: Callable[[dict[str, Any]], Outcome]
    example: dict[str, Any] = field(default_factory=dict)
    variants: tuple[dict[str, Any], ...] = ()   # the finite ways this action can be taken
    variant_key: Callable[[dict[str, Any]], str] | None = None
    validate: Callable[[dict[str, Any]], dict[str, Any] | None] | None = None

    def allowed_values(self) -> dict[str, list[str]]:
        """The values each parameter may take, read off the declared variants.

        Derived rather than written down twice: a menu that lists values a validator does not
        accept, or omits ones it does, is the drift this design exists to avoid.
        """
        out: dict[str, set[str]] = {}
        for variant in self.variants:
            for field, value in variant.items():
                out.setdefault(field, set()).add(str(value))
        return {f: sorted(v) for f, v in out.items() if len(v) > 1}

    def key_of(self, params: dict[str, Any]) -> str | None:
        """A stable name for what these params select, or None when the action can always be
        taken again (asking for proposals is never a repeat).

        `variant_key` WITHOUT `variants` is the useful middle case: an action whose legal set is
        not fixed in advance but which must still not be repeated. Measuring one named design is
        that — the set is whatever the store holds, and placing the same fabric twice costs
        minutes of real place-and-route to learn nothing. Observed in a live run: a model chose
        `measure` on the same fabric twice, and without a key the loop had no reason to stop it.
        """
        if self.variant_key is None:
            return None
        return self.variant_key(params)


@dataclass
class StepRecord:
    step: int
    action: str
    params: dict[str, Any]
    reason: str
    gained: int
    detail: str = ""


@dataclass
class SearchReport:
    steps: list[StepRecord]
    unexplored: list[str]
    stopped_because: str

    def yields_digest(self) -> str:
        if not self.steps:
            return "(no step taken yet)"
        lines = []
        for s in self.steps:
            head = (f"  step {s.step} ({s.action}"
                    f"{'' if not s.params else ' ' + _short(s.params)}): ")
            # A step's own words when it has them: a measurement's result is the thing the next
            # decision turns on, and reporting only "0 candidates" would hide it entirely.
            lines.append(head + (s.detail or f"{s.gained} candidate(s) never tried before"))
        return "\n".join(lines)


def _short(params: dict[str, Any]) -> str:
    return ",".join(f"{k}={v}" for k, v in sorted(params.items()) if k != "action")


_PROMPT = """You are directing a design-space exploration. Choose the NEXT step only, then stop.

THE PROBLEM: {problem}

WHAT EARLIER WORK ALREADY SETTLED (conclusions are INFERRED, refusals are measured):
{lessons}

WHAT EACH STEP SO FAR YIELDED (candidates never tried before):
{yields}

ALREADY DONE: {done}
NOT YET DONE: {remaining}
STEPS TAKEN: {taken} of at most {cap}
MEASURED SO FAR:
{frontier}

CHOOSE ONE, as a single JSON object and nothing else:
{menu}

WHEN TO STOP. You decide, and there is no fixed number of steps: keep going while a step can
still plausibly improve the frontier, and stop when you are confident it cannot. Read the yield
column before deciding, since a step that found nothing predicts a successor that finds nothing.
Stopping early leaves value on the table; spending steps after the frontier stops moving wastes
them. Both are real costs and the choice is yours.

JSON only:"""


class DirectedSearch:
    """The loop. Construct with the actions an application supports, then `run()`."""

    def __init__(self, actions: list[Action], *, ask: Callable[[str], str] | None,
                 problem: str, cap: int = 16, barren_limit: int = 3,
                 frontier: Callable[[], str] = lambda: "(nothing yet)",
                 lessons: Callable[[], str] = lambda: "(none)",
                 log: Callable[[str], None] = print) -> None:
        self._actions = {a.name: a for a in actions}
        self._ask = ask
        self._problem = problem
        self._cap = cap
        self._barren_limit = barren_limit
        self._frontier = frontier
        self._lessons = lessons
        self._log = log
        self._done: list[str] = []

    def _menu(self) -> str:
        lines = []
        for action in self._actions.values():
            example = dict(action.example)
            example.setdefault("action", action.name)
            lines.append(f"  {json.dumps(example)}   {action.menu}")
            # The legal VALUES, not just one example of the shape. Observed: a model answered
            # `breadth: "broad"` — a reasonable synonym for "wide" that is not in the vocabulary —
            # and the whole step fell back to a default. The validator caught it, so nothing was
            # wrong with the result, but the step's direction was spent on a guess the prompt
            # could have prevented. An action that declares its variants can say what they are.
            for field, allowed in sorted(action.allowed_values().items()):
                lines.append(f"       {field}: one of {', '.join(allowed)}")
        lines.append('  {"action": "stop"}   nothing left worth spending on')
        return "\n".join(lines)

    # How many unexplored options to name before summarising. Bounded on purpose: the set is
    # only listable for actions with DECLARED variants, and even those can outgrow a prompt that
    # is sent on every decision. An action whose legal set is dynamic — measuring any design the
    # store holds — cannot be enumerated here at all, and pretending otherwise would put a
    # thousand labels in front of the model.
    _REMAINING_SHOWN = 8

    def _remaining_note(self) -> str:
        """What has NOT been done, so stopping has a visible cost.

        The prompt listed what was already done and never what was left, and a model reading it
        had no reminder that five of six scopes were untouched. Observed: a run that stopped after
        two steps and reported one scope of six explored (docs/decisions.md D291). Telling it what
        remains does not force it to continue — it still decides — but it decides against the
        actual state rather than against a blank.
        """
        keys = [k for a, v in self._unrun_variants() if (k := a.key_of(v))]
        if not keys:
            return "(every listed option has been taken; only repeatable steps remain)"
        shown = ", ".join(keys[: self._REMAINING_SHOWN])
        if len(keys) > self._REMAINING_SHOWN:
            shown += f", and {len(keys) - self._REMAINING_SHOWN} more"
        return f"{shown}. Stopping now leaves these unexplored, and the run will say so."

    def _unrun_variants(self) -> list[tuple[Action, dict[str, Any]]]:
        out = []
        for action in self._actions.values():
            for variant in action.variants:
                if action.key_of(variant) not in self._done:
                    out.append((action, dict(variant)))
        return out

    def _plan(self, report_so_far: SearchReport, taken: int) -> tuple[dict[str, Any], str]:
        """Ask the model; fall back to the next unrun variant when its answer is unusable.

        A bad answer costs a step's DIRECTION, never the step itself — which is what lets the
        whole search run on a machine with no model at all.
        """
        reason = "no model: taking the next unrun option"
        if self._ask is not None:
            prompt = _PROMPT.format(
                problem=self._problem, lessons=self._lessons(),
                yields=report_so_far.yields_digest(), done=", ".join(self._done) or "(none yet)",
                remaining=self._remaining_note(), taken=taken, cap=self._cap,
                frontier=self._frontier(), menu=self._menu())
            try:
                from flux_llm import strip_markdown_fence

                choice = json.loads(strip_markdown_fence(self._ask(prompt)).strip())
                name = choice.get("action")
                if name == "stop":
                    return {"action": "stop"}, "the model judged there was nothing left"
                action = self._actions.get(name)
                if action is not None:
                    params = {k: v for k, v in choice.items() if k != "action"}
                    if action.validate is not None:
                        params = action.validate(params)
                    if params is not None:
                        return {"action": name, **params}, "the model chose it"
                reason = f"unusable choice {choice!r}"
            except Exception as exc:  # noqa: BLE001 — a bad plan is a fallback, not a crash
                reason = f"{type(exc).__name__}: {exc}"[:90]
        for action, variant in self._unrun_variants():
            return {"action": action.name, **variant}, f"fell back to the next option ({reason})"
        return {"action": "stop"}, f"fell back to stopping, every option has run ({reason})"

    def run(self) -> SearchReport:
        report = SearchReport(steps=[], unexplored=[], stopped_because="reached the step cap")
        barren = 0
        step = 0
        while step < self._cap:
            plan, reason = self._plan(report, step)
            name = plan.pop("action")
            if name == "stop":
                report.stopped_because = reason
                self._log(f"\n--- orchestrator: stop ({reason})")
                break
            action = self._actions[name]
            # The parameters, not just the verb. A step logged as "enumerate (the model chose
            # it)" tells a reader nothing about what was chosen, and this output IS the demo:
            # the point is watching the model direct a search, which needs its actual choice.
            shown = _short(plan)
            self._log(f"\n--- orchestrator: {name}{' ' + shown if shown else ''} ({reason})")
            key = action.key_of(plan)
            if key is not None and key in self._done:
                self._log(f"    {key} already done, moving on")
                step += 1
                continue
            step += 1
            outcome = action.run(dict(plan))
            if key is not None:
                self._done.append(key)
            report.steps.append(StepRecord(step, name, dict(plan), reason, outcome.gained,
                                           outcome.detail))
            # The one place the model's judgement is overruled, and it is overruled by a fact from
            # the store rather than by a policy about how many rounds are reasonable.
            if not outcome.was_productive and key is None:
                barren += 1
                self._log(f"    that step found nothing new ({barren} in a row)")
                if barren >= self._barren_limit:
                    report.stopped_because = (
                        f"{self._barren_limit} consecutive steps found nothing new")
                    self._log(f"    stopping: {report.stopped_because}")
                    break
            else:
                barren = 0
        report.unexplored = [k for a, v in self._unrun_variants() if (k := a.key_of(v))]
        return report
