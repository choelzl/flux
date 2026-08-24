"""Where a draft comes from, and the generate/build/test sub-loop around it (D456).

Generation is itself a loop (Cedric's drawing): draft, build, fast-check, and again with the
failure in hand until it passes or the budget runs out. What differs between applications is
not that loop -- it is WHO drafts:

* a model (`Model`, the default): the rich inner loop of D412/D414/D417 -- design or resume
  from the best, patch toward the failure, revert when an edit path stops converging;
* a template (`Template`): code the framework writes -- a renderer, a script, a generator
  that takes a spec and emits the artifact. No model in the loop at all;
* a catalog (`Catalog`): designs that already exist -- a shipped default, a vendor IP, last
  night's winner. The "generator" chooses among them and the next one is tried when one fails;
* a solver (`Solver`): a candidate computed from the constraints, told the counter-example
  the last one failed on (the bankmap chain's shape, D356).

A source is asked for one draft at a time and sees why the previous one was refused
(`Attempt`), so an iterative source needs no state of its own beyond what it is handed. The
three cheap sources share ONE loop body here (`iterate`); the model keeps its own because
patching, reverting and prototypes only make sense for text a model wrote. Either way the
phases a watcher sees, the refusal reasons and the fast-check budget are the same.
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
    (what was drafted last) and `failure` (what the build or the fast check said about it),
    which is everything an iterating source needs to do better than it just did."""

    subgoal: str | None
    state: LoopState
    index: int = 0
    prior: Candidate | None = None
    failure: str = ""

    @property
    def params(self) -> dict[str, Any]:
        """The run's knobs, so a source can be configured by the request rather than by
        whoever constructed it."""
        return self.state.request.params


@runtime_checkable
class Source(Protocol):
    """A generator role: hand it an `Attempt`, get a candidate or the reason there is none."""

    name: str

    def draft(self, attempt: Attempt) -> tuple[Candidate | None, str]:
        ...


@dataclass
class Model:
    """The model inner loop -- named so a document, a graph or a report can SAY that this is
    where the model generates, rather than it being the unnamed default."""

    name: str = "LLM-gen"


@dataclass
class Template:
    """Code the framework writes. `render(attempt) -> Candidate | None | (Candidate, why)`;
    a renderer that ignores `attempt.failure` simply produces the same thing again, which the
    loop notices as a repeat rather than spending the whole budget on it."""

    render: Callable[[Attempt], Any]
    name: str = "template-fill"

    def draft(self, attempt: Attempt) -> tuple[Candidate | None, str]:
        return _as_draft(self.render(attempt), self.name)


@dataclass
class Catalog:
    """Designs that already exist, tried in order: `items[attempt.index]`, turned into a
    candidate by `make`. Exhausting the list is a refusal with its own reason, not a crash --
    "the three shipped fabrics all failed the fast check" is a result."""

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
    counter-example becomes the next constraint -- which is what a proof chain does."""

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
    """What makes this candidate the same design as another: its artifact AND its knobs, since
    a problem whose candidates are points rather than text has an empty artifact for all of
    them (a bank mapping, a prefetcher configuration)."""
    return cand.artifact + "|" + repr(sorted((k, str(v)) for k, v in cand.knobs.items()))


def iterate(problem: Any, source: Source, subgoal: str | None, state: LoopState, *,
            prior: Candidate | None = None, failure: str = ""
            ) -> tuple[Candidate | None, Any, str]:
    """The generation sub-loop for a source that is not the model (D456): draft, build,
    fast-check, and again with the failure in hand, `request.repair_attempts` times over.

    Returns what `Problem.generate` returns -- `(candidate, built, "")` or
    `(None, None, reason)` -- so a problem swaps the source and changes nothing else.

    `prior` and `failure` seed the first draft with a design that already exists and what was
    said about it (D463): an evaluator sending a candidate back to be improved is this same
    iteration, starting from something instead of from nothing.
    """
    key = subgoal or "*"
    tag = subgoal or getattr(problem, "name", "candidate")
    rounds = 1 + max(0, state.request.repair_attempts)
    say = state.say
    seen: set[str] = set()
    for index in range(rounds):
        state.attempts[key] = state.attempts.get(key, 0) + 1
        attempt = Attempt(subgoal=subgoal, state=state, index=index, prior=prior,
                          failure=failure)
        with _phase(f"generation: {source.name} draft {index + 1}", why=tag):
            try:
                cand, why = source.draft(attempt)
            except Exception as exc:  # noqa: BLE001 -- a source that raises is a refusal
                return None, None, f"{source.name} failed: {exc!s:.200}"
        if cand is None:
            return None, None, why or failure or f"{source.name} produced no candidate"
        sign = _signature(cand)
        if sign in seen:
            # The same text cannot pass a check it has just failed, so the remaining
            # attempts would buy nothing: stop and say which source repeated itself.
            return None, None, (f"{source.name} produced the same design again after "
                                f"{index} failure(s): {failure!s:.200}")
        seen.add(sign)
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
