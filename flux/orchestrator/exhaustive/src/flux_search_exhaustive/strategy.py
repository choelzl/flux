"""The exhaustive Strategy (docs/search.md): implements the `propose`/`observe`/`done` Protocol
over `candidates.py`'s generated mapping search space, plus `run_exhaustive_search` — a
convenience driver that runs the whole loop against a real `Evaluator`, with optional warm-start
(docs/search.md: "every strategy can query the Result Store for prior results") against a
`flux_store.ResultStore` so re-running the same sweep doesn't re-pay for candidates it already
has an answer for.

**Real wall-clock budget, docs/decisions.md D70 (following D69's own precedent for
`search/annealing`).** `run_exhaustive_search`'s own optional `wall_clock_budget_s` is checked
against real, measured elapsed time before every real evaluator call, same as D69. A real,
honest consequence unique to this strategy, spelled out explicitly rather than glossed over:
exhaustive search's entire point is a *proven* true optimum (every candidate evaluated, not a
sample) — stopping early on a budget genuinely breaks that guarantee, not just makes the search
"a bit less thorough." `ExhaustiveSearchReport.stopped_early` makes that loss of guarantee
visible to every caller, not silently absorbed into an unqualified `best`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from flux_evaluator_abi import Budget, Candidate, Result

from .candidates import MappingCandidate, generate_flat_mapping_candidates
from .engine import (EvaluatedCandidate, EvaluatorProtocol as _EvaluatorProtocol, SearchState,
                     classify_outcome, drive_propose_observe_loop)


class ExhaustiveMappingStrategy:
    """docs/search.md's `Strategy` Protocol (`propose`/`observe`/`done`), specialised to
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
    stopped_early: bool
    wall_clock_s: float


def run_exhaustive_search(
    workload: dict[str, Any],
    arch: dict[str, Any],
    evaluator: _EvaluatorProtocol,
    *,
    for_op: str,
    metric: str,
    minimize: bool = True,
    budget: Budget | None = None,
    wall_clock_budget_s: float | None = None,
    db_path: str | None = None,      # D459: the search's campaign record
) -> ExhaustiveSearchReport:
    """Drive `ExhaustiveMappingStrategy` end to end against a real `Evaluator`: propose every
    candidate at once, evaluate each (catching per-candidate failures — a mapping the evaluator
    can't express is expected, not a reason to abort the whole sweep, same "fail loudly, per
    candidate" posture the evaluator adapters themselves take), observe, and report the best
    result by `metric`.

    `wall_clock_budget_s` (docs/decisions.md D70) is a real, enforced stopping condition, checked
    against real, measured elapsed time before every real evaluator call — not merely reported.
    **A real, honest consequence, not a cosmetic one**: exhaustive search's whole point is a
    *proven* true optimum (every candidate evaluated); stopping early genuinely breaks that
    guarantee for this run. `ExhaustiveSearchReport.stopped_early` makes that loss visible —
    `best` becomes "best of what was actually evaluated," not "the proven true optimum," the
    moment `stopped_early` is `True`.
    """
    strategy = ExhaustiveMappingStrategy(workload, arch, for_op=for_op)
    state = SearchState(workload=workload, arch=arch, for_op=for_op)
    budget = budget if budget is not None else Budget()

    # One candidate per propose()/observe() round-trip, not one giant batch — the shared
    # engine's shape (D429), so the per-candidate budget check has a checkpoint to stop at.
    stopped_early, wall_clock_s = drive_propose_observe_loop(
        strategy, state, evaluator, metric, budget=budget,
        wall_clock_budget_s=wall_clock_budget_s, db_path=db_path, minimize=True)

    # A Result that lacks the searched metric is a refusal, not a crash (D112/D168): the one
    # rule in the engine, applied here after the sweep because this strategy does not know
    # the metric while it observes.
    evaluated = []
    for e in strategy.evaluated:
        result, error = classify_outcome(e.result, metric) if e.result is not None else (None, e.error)
        evaluated.append(e if result is e.result and error == e.error
                         else EvaluatedCandidate(candidate=e.candidate, result=result, error=error))

    skipped = sum(1 for e in evaluated if e.error is not None)
    scored = [e for e in evaluated if e.result is not None]

    def _metric_value(evaluated_candidate: EvaluatedCandidate) -> float:
        assert evaluated_candidate.result is not None  # guaranteed by the `scored` filter above
        return evaluated_candidate.result.value_of(metric)

    best = (min if minimize else max)(scored, key=_metric_value) if scored else None

    return ExhaustiveSearchReport(
        evaluated=evaluated, best=best, metric=metric, skipped_not_expressible=skipped,
        stopped_early=stopped_early, wall_clock_s=wall_clock_s,
    )
