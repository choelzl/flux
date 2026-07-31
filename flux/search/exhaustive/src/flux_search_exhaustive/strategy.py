"""The exhaustive Strategy (docs/04.md §6): implements the `propose`/`observe`/`done` Protocol
over `candidates.py`'s generated mapping search space, plus `run_exhaustive_search` — a
convenience driver that runs the whole loop against a real `Evaluator`, with optional warm-start
(docs/04.md §6: "every strategy can query the Result Store for prior results") against a
`flux_store.ResultStore` so re-running the same sweep doesn't re-pay for candidates it already
has an answer for.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from flux_evaluator_abi import Budget, Candidate, Result

from .candidates import MappingCandidate, generate_flat_mapping_candidates


@dataclass(frozen=True, slots=True)
class SearchState:
    """Everything fixed for the duration of one exhaustive-mapping search: the workload and
    architecture being searched, and which op within the workload. Mapping is what varies —
    that's the whole point of this strategy.
    """

    workload: dict[str, Any]
    arch: dict[str, Any]
    for_op: str


@dataclass(frozen=True, slots=True)
class EvaluatedCandidate:
    """One candidate's outcome: either a real `Result`, or the evaluator's own refusal
    (`error`, e.g. `NotExpressibleError`'s message) — never both, and never silently dropped.
    """

    candidate: MappingCandidate
    result: Result | None
    error: str | None


class _EvaluatorProtocol(Protocol):
    def evaluate(self, candidate: Candidate, budget: Budget, metrics: frozenset[str]) -> Result: ...


class ExhaustiveMappingStrategy:
    """docs/04.md §6's `Strategy` Protocol (`propose`/`observe`/`done`), specialised to
    exhaustive flat-mapping enumeration. The full candidate list is generated once at
    construction (`candidates.py`); `propose(state, k)` serves it in batches, `observe(results)`
    records outcomes for the most recently proposed batch, `done()` is true once every candidate
    has been observed.
    """

    def __init__(self, workload: dict[str, Any], arch: dict[str, Any], *, for_op: str) -> None:
        self._all_candidates = generate_flat_mapping_candidates(workload, arch, for_op=for_op)
        self._next_index = 0
        self._pending: list[MappingCandidate] = []
        self.evaluated: list[EvaluatedCandidate] = []

    def propose(self, state: SearchState, k: int) -> list[Candidate]:
        if self._pending:
            raise RuntimeError("propose() called again before observe() for the previous batch")
        batch = self._all_candidates[self._next_index : self._next_index + k]
        self._next_index += len(batch)
        self._pending = batch
        return [Candidate(workload=state.workload, arch=state.arch, mapping=c.mapping) for c in batch]

    def observe(self, results: list[Result | Exception]) -> None:
        if len(results) != len(self._pending):
            raise ValueError(
                f"observe() got {len(results)} results for {len(self._pending)} proposed candidates"
            )
        for mapping_candidate, outcome in zip(self._pending, results):
            if isinstance(outcome, Exception):
                self.evaluated.append(
                    EvaluatedCandidate(candidate=mapping_candidate, result=None, error=str(outcome))
                )
            else:
                self.evaluated.append(
                    EvaluatedCandidate(candidate=mapping_candidate, result=outcome, error=None)
                )
        self._pending = []

    def done(self) -> bool:
        return self._next_index >= len(self._all_candidates) and not self._pending

    @property
    def total_candidates(self) -> int:
        return len(self._all_candidates)


@dataclass(frozen=True, slots=True)
class ExhaustiveSearchReport:
    evaluated: list[EvaluatedCandidate]
    best: EvaluatedCandidate | None
    metric: str
    skipped_not_expressible: int


def run_exhaustive_search(
    workload: dict[str, Any],
    arch: dict[str, Any],
    evaluator: _EvaluatorProtocol,
    *,
    for_op: str,
    metric: str,
    minimize: bool = True,
    budget: Budget | None = None,
) -> ExhaustiveSearchReport:
    """Drive `ExhaustiveMappingStrategy` end to end against a real `Evaluator`: propose every
    candidate at once, evaluate each (catching per-candidate failures — a mapping the evaluator
    can't express is expected, not a reason to abort the whole sweep, same "fail loudly, per
    candidate" posture the evaluator adapters themselves take), observe, and report the best
    result by `metric`.
    """
    strategy = ExhaustiveMappingStrategy(workload, arch, for_op=for_op)
    state = SearchState(workload=workload, arch=arch, for_op=for_op)
    budget = budget if budget is not None else Budget()

    while not strategy.done():
        proposed = strategy.propose(state, k=strategy.total_candidates)
        outcomes: list[Result | Exception] = []
        for candidate in proposed:
            try:
                outcomes.append(evaluator.evaluate(candidate, budget, frozenset({metric})))
            except Exception as exc:  # noqa: BLE001 - a candidate the evaluator refuses is expected, not fatal
                outcomes.append(exc)
        strategy.observe(outcomes)

    skipped = sum(1 for e in strategy.evaluated if e.error is not None)
    scored = [e for e in strategy.evaluated if e.result is not None]

    def _metric_value(evaluated: EvaluatedCandidate) -> float:
        assert evaluated.result is not None  # guaranteed by the `scored` filter above
        return evaluated.result.metrics[metric].value

    best = (min if minimize else max)(scored, key=_metric_value) if scored else None

    return ExhaustiveSearchReport(
        evaluated=strategy.evaluated, best=best, metric=metric, skipped_not_expressible=skipped
    )
