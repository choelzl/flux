"""The propose/observe/done engine every search strategy shares (D429), driven by the one loop (D459).

`SearchState`, `EvaluatedCandidate` and the driver loop had been written three times --
exhaustive, annealing, agentic -- field for field and line for line. They live here,
in the package the other two already depend on; each keeps its public names by importing
them from here. The agentic package's `_EvaluatedEntry` adds its fallback fields on top.

**The driver is `flux_loop` now** (D459). `while not strategy.done(): propose -> evaluate ->
observe` is `flux_loop`'s batch path with a batch of one: a strategy's `propose` is the
generator, running the evaluator is the measurement, and what the strategy `observe`s is what
the stage returned. The strategies are unchanged -- their prompts, parsers, visited sets and
`done()` conditions are what makes them different, and none of that was ever the loop. What
they gain by being on it: a campaign record (every evaluated candidate a trial, every
evaluator refusal a refused trial with its reason -- these searches kept no record at all),
the timing tree, and one implementation of the wall-clock budget.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Protocol

from flux_evaluator_abi import Budget, Candidate, Result
from flux_loop import Candidate as LoopCandidate
from flux_loop import Problem, Verdict

__all__ = ["EvaluatedCandidate", "EvaluatorProtocol", "SearchState", "StrategyProblem",
           "classify_outcome", "drive_propose_observe_loop"]


@dataclass(frozen=True, slots=True)
class SearchState:
    """Everything fixed for the duration of one mapping search: the workload and
    architecture being searched, and which op within the workload. The mapping is what
    varies -- that is the whole point of these strategies."""

    workload: dict[str, Any]
    arch: dict[str, Any]
    for_op: str


@dataclass(frozen=True, slots=True)
class EvaluatedCandidate:
    """One candidate's outcome: either a real `Result`, or the evaluator's own refusal
    (`error`, e.g. `NotExpressibleError`'s message) -- never both, never silently dropped."""

    candidate: Any
    result: Result | None
    error: str | None


class EvaluatorProtocol(Protocol):
    def evaluate(self, candidate: Candidate, budget: Budget, metrics: frozenset[str]) -> Result: ...


def classify_outcome(outcome: Result | Exception | None, metric: str
                     ) -> tuple[Result | None, str | None]:
    """What one evaluation came back as: `(result, None)` when a Result carries `metric`,
    `(None, reason)` when the evaluator raised or its Result lacks the metric -- a
    per-candidate refusal, never a crash (the D112 hole, closed once here rather than in
    every strategy: `evaluator/rtl` legally returns an empty metrics dict for anything but
    `latency_cycles`, and indexing it used to kill whole searches, D168)."""
    if outcome is None:
        return None, None
    if isinstance(outcome, Exception):
        return None, str(outcome)
    refusal = outcome.refusal_for(metric)
    return (None, refusal) if refusal is not None else (outcome, None)


class _Strategy(Protocol):
    def done(self) -> bool: ...
    def propose(self, state: Any, k: int) -> list[Any]: ...
    def observe(self, outcomes: list[Any]) -> None: ...


#: The loop bounds its own steps; a search is bounded by its strategy's `done()` instead, so
#: the step budget here is only a backstop against a strategy that never finishes.
_STEP_CEILING = 100_000


class StrategyProblem(Problem):
    """One search strategy as a `flux_loop.Problem` (D459).

    `search` is the strategy's own propose/done control flow, one candidate per batch -- one
    per round trip deliberately, because a batched propose leaves no checkpoint between
    candidates to stop at. `measure` runs the evaluator and hands the outcome to `observe`:
    the strategy sees exactly what it saw before, including the exception when the evaluator
    refuses a candidate, which is expected rather than fatal.
    """

    def __init__(self, strategy: _Strategy, state: Any, evaluator: EvaluatorProtocol,
                 metric: str, *, budget: Budget, wall_clock_budget_s: float | None,
                 minimize: bool | None) -> None:
        self.name = getattr(strategy, "name", type(strategy).__name__)
        self.strategy = strategy
        self.state = state
        self.evaluator = evaluator
        self.metric = metric
        self.budget = budget
        self.wall_clock_budget_s = wall_clock_budget_s
        self.minimize = minimize
        self.started = time.perf_counter()
        self.stopped_early = False
        self.proposed = 0
        #: the candidate objects themselves, by the name the loop knows them under: a
        #: candidate is a strategy's own type (an ABI `Candidate`, a width, a topology) and
        #: belongs to the strategy, not in a record document (D458)
        self._own: dict[str, Any] = {}

    # ---- mentor
    def objective(self, request: Any) -> dict[str, Any]:
        # `minimize` verbatim, None included: a run that was never told which way is better
        # is not the same search as one that was, and two campaigns should not share an id.
        return {"study": self.name, "metric": self.metric, "minimize": self.minimize}

    # ---- orchestration
    def search(self, loop_state: Any) -> Any:
        while not self.strategy.done():
            if (self.wall_clock_budget_s is not None
                    and time.perf_counter() - self.started >= self.wall_clock_budget_s):
                self.stopped_early = True
                loop_state.say(f"  the wall-clock budget ({self.wall_clock_budget_s:g}s) is "
                               f"spent after {self.proposed} candidate(s)")
                return
            (candidate,) = self.strategy.propose(self.state, k=1)
            name = f"{self.name}#{self.proposed}"
            self._own[name] = candidate
            self.proposed += 1
            yield [LoopCandidate(name=name, knobs=_knobs(candidate),
                                 meta={"strategy": "llm" if _agentic(self.strategy)
                                       else "enumerate"})]

    # ---- the gate: there is none. A strategy proposes only candidates it considers legal,
    # and what the evaluator thinks of one is a measurement, not a gate.
    def build(self, cand: Any, subgoal: Any, loop_state: Any) -> Any:
        return self._own[cand.name]

    def judge(self, built: Any, cand: Any, subgoal: Any, loop_state: Any) -> Verdict:
        return Verdict(True, 0.0)

    # ---- the stage
    def stages(self) -> list[str]:
        return [self.metric]

    def measure(self, cand: Any, stage: str, loop_state: Any) -> dict[str, Any]:
        """Evaluate one candidate and let the strategy observe the outcome -- the whole point
        of the round trip. The evaluator's refusal is returned as this stage's error, so the
        record carries WHY a candidate went nowhere instead of dropping it."""
        own = self._own[cand.name]
        try:
            outcome: Result | Exception = self.evaluator.evaluate(
                own, self.budget, frozenset({self.metric}))
        except Exception as exc:  # noqa: BLE001 - a refused candidate is expected, not fatal
            outcome = exc
        self.strategy.observe([outcome])
        if isinstance(outcome, Exception):
            return {"error": str(outcome)}
        if not hasattr(outcome, "refusal_for"):
            return {}          # an outcome only the strategy understands; nothing to record
        result, error = classify_outcome(outcome, self.metric)
        if result is None:
            return {"error": error or f"no {self.metric} in the result"}
        return {self.metric: float(result.value_of(self.metric))}

    def cache_suffix(self) -> str | None:
        """No measurement cache, on purpose: the strategy must SEE every outcome, and a cached
        hit is a candidate it never observed -- it would leave the search waiting for a result
        that never comes."""
        return None

    # ---- the decision
    def frontier(self, scored: list[Any], loop_state: Any) -> list[Any]:
        return []

    def frontier_axes(self) -> None:
        return None

    def decide(self, pool: list[Any], loop_state: Any) -> tuple[Any, str]:
        """The best measured candidate on this one metric, when the caller said which way is
        better; otherwise no decision -- the strategy's own `best` is the answer either way,
        and this is what the RECORD's conclusion is drawn from."""
        if not pool or self.minimize is None:
            return None, "the strategy's own best is the answer"
        best = (min if self.minimize else max)(
            pool, key=lambda s: s.metrics.get(self.metric, float("inf") if self.minimize
                                              else float("-inf")))
        return best, f"the best {self.metric} of {len(pool)} measured candidate(s)"


def _knobs(candidate: Any) -> dict[str, Any]:
    """What the record should say this candidate WAS. Every candidate type in these packages
    has a `to_dict`; anything else is recorded as its text rather than not at all."""
    to_dict = getattr(candidate, "to_dict", None)
    if callable(to_dict):
        try:
            got = to_dict()
            if isinstance(got, dict):
                return got
        except Exception:  # noqa: BLE001 -- a record document is never worth a failed search
            pass
    return {"candidate": str(candidate)}


def _agentic(strategy: Any) -> bool:
    """Whether a model proposed the candidates, for the record's strategy column (D446)."""
    return hasattr(strategy, "_llm")


def drive_propose_observe_loop(strategy: _Strategy, state: Any, evaluator: EvaluatorProtocol,
                               metric: str, *, budget: Budget | None = None,
                               wall_clock_budget_s: float | None = None,
                               db_path: str | None = None,
                               minimize: bool | None = None) -> tuple[bool, float]:
    """`while not strategy.done(): propose one -> evaluate -> observe` ON THE LOOP (D459),
    with the wall-clock budget checked against real elapsed time before every evaluator call
    (D70/D73). A candidate the evaluator refuses reaches `observe` as the exception --
    expected, not fatal. Returns `(stopped_early, wall_clock_s)`; the strategy's own
    `evaluated` / `best` are the main side effect, exactly as before.

    `db_path` opens a campaign record for the search (D459): every evaluated candidate lands
    as a trial on the metric's stage and every refusal as a refused trial with its reason.
    `minimize` says which way is better, so that record can also carry a conclusion; without
    it the run records the measurements and no decision.
    """
    from flux_loop import LoopRequest, run_loop

    problem = StrategyProblem(strategy, state, evaluator, metric,
                              budget=budget if budget is not None else Budget(),
                              wall_clock_budget_s=wall_clock_budget_s, minimize=minimize)
    run_loop(problem, LoopRequest(
        db=db_path or "", steps=_STEP_CEILING, prototype=False, compute=False,
        critique_rounds=0, patching=False, params={"metric": metric}),
        log=lambda _m: None)
    return problem.stopped_early, time.perf_counter() - problem.started
