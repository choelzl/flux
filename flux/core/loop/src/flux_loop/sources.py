"""Where a draft comes from, and the generate/build/test sub-loop around it (D456).

Generation is itself a loop: draft, build, fast-check, and again with the failure in hand until
it passes or the budget runs out. What differs between applications is who drafts:

* a model (`Model`, the default): design or resume from the best, patch toward the failure,
  revert when an edit path stops converging;
* a template (`Template`): a renderer, script or generator that emits the artifact; no model;
* a catalog (`Catalog`): designs that already exist, tried in turn;
* a solver (`Solver`): a candidate computed from the constraints, told the counter-example
  the last one failed on (D356).

A source is asked for one draft at a time and sees why the previous one was refused
(`Attempt`), so it needs no state of its own. The three cheap sources share one loop body
(`iterate`); the model keeps its own because patching, reverting and prototypes only make sense
for model text. The phases, refusal reasons and fast-check budget are the same either way.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol, Sequence, runtime_checkable

from .observe import _phase
from .types import BuildError, Candidate, LoopState

__all__ = ["Attempt", "Catalog", "Model", "Solver", "Source", "Template", "from_file",
           "iterate"]


@dataclass(frozen=True)
class Attempt:
    """One draft request. `index` 0 is the first try of this pass; 1 and up carry `prior`
    (what was drafted last) and `failure` (what the build or the fast check said about it)."""

    subgoal: str | None
    state: LoopState
    index: int = 0
    prior: Candidate | None = None
    failure: str = ""
    brief: str = ""                     # D839: what this draft is asked beyond the problem (an exploring pass's)

    @property
    def params(self) -> dict[str, Any]:
        """The run's knobs, so a source can be configured by the request."""
        return self.state.request.params


@runtime_checkable
class Source(Protocol):
    """A generator role: hand it an `Attempt`, get a candidate or the reason there is none."""

    name: str

    def draft(self, attempt: Attempt) -> tuple[Candidate | None, str]:
        ...


@dataclass
class Model:
    """The model inner loop, named so a document, graph or report can say where the model
    generates."""

    name: str = "LLM-gen"


@dataclass
class Template:
    """Code the framework writes. `render(attempt) -> Candidate | None | (Candidate, why)`;
    a renderer that ignores `attempt.failure` repeats itself, which the loop stops on."""

    render: Callable[[Attempt], Any]
    name: str = "template-fill"

    def draft(self, attempt: Attempt) -> tuple[Candidate | None, str]:
        return _as_draft(self.render(attempt), self.name)


@dataclass
class Catalog:
    """Designs that already exist, tried in order: `items[attempt.index]`, turned into a
    candidate by `make`. Exhausting the list is a refusal with its own reason, not a crash."""

    items: Sequence[Any]
    make: Callable[[Any, Attempt], Any]
    name: str = "catalog"

    def draft(self, attempt: Attempt) -> tuple[Candidate | None, str]:
        if attempt.index >= len(self.items):
            return None, (f"the catalog holds {len(self.items)} design(s) and all of them "
                          f"were tried")
        return _as_draft(self.make(self.items[attempt.index], attempt), self.name)


@dataclass
class Solver:
    """A candidate computed from the constraints. `solve(attempt)` sees the last failure, so a
    counter-example becomes the next constraint."""

    solve: Callable[[Attempt], Any]
    name: str = "solver"

    def draft(self, attempt: Attempt) -> tuple[Candidate | None, str]:
        return _as_draft(self.solve(attempt), self.name)


def _as_draft(answer: Any, who: str) -> tuple[Candidate | None, str]:
    """A source may return a candidate, None, or `(candidate, why)`; normalise to the pair."""
    if isinstance(answer, tuple):
        cand, why = (list(answer) + [""])[:2]
        return cand, str(why or "")
    if answer is None:
        return None, f"{who} produced no candidate"
    return answer, ""


def _signature(cand: Candidate) -> str:
    """What makes this candidate the same design as another: its artifact and its knobs, since
    point-like candidates (a bank mapping, a prefetcher configuration) have empty artifacts."""
    return cand.artifact + "|" + repr(sorted((k, str(v)) for k, v in cand.knobs.items()))


def iterate(problem: Any, source: Source, subgoal: str | None, state: LoopState, *,
            prior: Candidate | None = None, failure: str = "", brief: str = ""
            ) -> tuple[Candidate | None, Any, str]:
    """The generation sub-loop for a source that is not the model (D456): draft, build,
    fast-check, and again with the failure in hand, `request.repair_attempts` times over.

    Returns what `Problem.generate` returns -- `(candidate, built, "")` or
    `(None, None, reason)`. `prior` and `failure` seed the first draft with an existing design
    and what was said about it (D463: an evaluator sending a candidate back to be improved).
    """
    key = subgoal or "*"
    tag = subgoal or getattr(problem, "name", "candidate")
    rounds = 1 + max(0, state.request.repair_attempts)
    say = state.say
    seen: set[str] = set()
    for index in range(rounds):
        state.attempts[key] = state.attempts.get(key, 0) + 1
        attempt = Attempt(subgoal=subgoal, state=state, index=index, prior=prior,
                          failure=failure, brief=brief)
        with _phase(f"generation: {source.name} draft {index + 1}", why=tag):
            try:
                cand, why = source.draft(attempt)
            except Exception as exc:  # noqa: BLE001 -- a source that raises is a refusal
                return None, None, f"{source.name} failed: {exc!s:.200}"
        if cand is None:
            return None, None, why or failure or f"{source.name} produced no candidate"
        sign = _signature(cand)
        if sign in seen:
            # The same text cannot pass a check it has just failed: stop and name the source.
            return None, None, (f"{source.name} produced the same design again after "
                                f"{index} failure(s): {failure!s:.200}")
        seen.add(sign)
        if source.name.startswith("agent:"):
            from .novelty import twin, twin_said

            again = twin(state, cand)
            if again is not None:                   # D839: a design measured already, refused before it is built
                failure, prior = twin_said(again), cand
                say(f"  {source.name}: {cand.name} is {again.candidate.name} again, already measured; asking for a different one")
                continue
        try:
            with _phase(f"generation: build {cand.name}", why=source.name):
                built = problem.build(cand, subgoal, state)
        except BuildError as exc:
            failure, prior = str(exc), cand
            say(f"  {source.name}: {cand.name} did not build ({failure!s:.80})")
            continue
        with _phase(f"test: fast check {cand.name}", why=source.name):
            fails, text = problem.fast_check(built, cand, subgoal, state)
        if not fails:
            if index:
                say(f"  {source.name}: {cand.name} passes after {index} repair(s)")
            return cand, built, ""
        failure, prior = text or f"{fails} failure(s)", cand
        say(f"  {source.name}: {cand.name} has {fails} failure(s) on the fast check")
    return None, None, (f"{source.name}: no candidate passed the fast check in {rounds} "
                        f"attempt(s); last: {failure!s:.200}")


def from_file(path: str | Path, attempt: Attempt, *,
              name: str = "") -> tuple[Candidate | None, str]:
    """Read a file as a candidate: the `Catalog` entry that is a design already on disk."""
    p = Path(path)
    if not p.is_file():
        return None, f"{p} is not a file"
    return Candidate(name or p.stem, p.read_text(),
                     knobs={"source": str(p)}, subgoal=attempt.subgoal), ""
