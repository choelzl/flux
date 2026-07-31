"""Simulated annealing (docs/04.md §6) over the same flat-mapping search space
search/exhaustive/'s candidates.py defines — a second, independent implementation of the same
`Strategy` Protocol, over the same representation, so it can be validated against exhaustive
search's *proven* true optimum instead of trusted on faith (see
tests/integration/test_search_annealing_live.py).

Classical serial-chain SA: one neighbor proposed and one Metropolis accept/reject decision per
`propose`/`observe` round-trip, geometric cooling. Deliberately not a batched/parallel variant —
docs/04.md §9's "explicit seeds everywhere" determinism principle is easier to keep honest with a
single chain than with several run in parallel and merged.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Any

from flux_evaluator_abi import Budget, Candidate, Result
from flux_search_exhaustive.candidates import (
    FlatMappingScope,
    MappingCandidate,
    build_flat_mapping_candidate,
    parse_flat_mapping_scope,
)


@dataclass(frozen=True, slots=True)
class SearchState:
    workload: dict[str, Any]
    arch: dict[str, Any]
    for_op: str


@dataclass(frozen=True, slots=True)
class EvaluatedCandidate:
    candidate: MappingCandidate
    result: Result | None
    error: str | None


class SimulatedAnnealingMappingStrategy:
    """docs/04.md §6's `Strategy` Protocol via simulated annealing. `propose(state, k)` requires
    `k == 1` (see module docstring); `observe([outcome])` applies the Metropolis criterion —
    always accept an improving move, accept a worsening move with probability
    `exp(-delta/temperature)` — then cools by `cooling_rate`. `done()` once `max_iterations` is
    reached or `temperature` drops below `min_temperature`.

    A candidate the evaluator refuses (an `Exception` in `observe`'s input) is treated as a
    rejected move: recorded in `evaluated`, temperature still cools, current state doesn't move.
    Not a special case — the same outcome a genuinely-worse candidate gets on a low-probability
    draw, just guaranteed rather than probabilistic.
    """

    def __init__(
        self,
        workload: dict[str, Any],
        arch: dict[str, Any],
        *,
        for_op: str,
        metric: str,
        minimize: bool = True,
        initial_temperature: float = 1.0,
        cooling_rate: float = 0.9,
        min_temperature: float = 1e-3,
        max_iterations: int = 200,
        seed: int = 0,
    ) -> None:
        self._scope: FlatMappingScope = parse_flat_mapping_scope(workload, arch, for_op=for_op)
        self._metric = metric
        self._minimize = minimize
        self._temperature = initial_temperature
        self._cooling_rate = cooling_rate
        self._min_temperature = min_temperature
        self._max_iterations = max_iterations
        self._iteration = 0
        self._rng = random.Random(seed)

        self._proposed: MappingCandidate | None = None
        self._current: MappingCandidate = self._random_candidate()
        self._current_value: float | None = None

        self.best: MappingCandidate | None = None
        self.best_value: float | None = None
        self.best_result: Result | None = None
        self.evaluated: list[EvaluatedCandidate] = []

    def _random_candidate(self) -> MappingCandidate:
        spatial_dim = self._rng.choice(self._scope.loop_dims)
        order = tuple(self._rng.sample(self._scope.loop_dims, len(self._scope.loop_dims)))
        return build_flat_mapping_candidate(self._scope, spatial_dim=spatial_dim, temporal_order=order)

    def _neighbor(self, candidate: MappingCandidate) -> MappingCandidate:
        """One random move: either swap two positions in the temporal order, or change which
        dim is spatially split (keeping the temporal order otherwise the same). Only one loop
        dim exists in a degenerate single-dim workload, so the swap move is skipped there
        (nothing to swap) — falls through to a spatial-dim change, which is itself a no-op when
        only one loop dim exists at all (spatial and temporal candidates coincide)."""
        can_swap = len(self._scope.loop_dims) >= 2
        can_change_spatial = len(self._scope.loop_dims) >= 2
        moves = [m for m, allowed in (("swap", can_swap), ("spatial", can_change_spatial)) if allowed]
        move = self._rng.choice(moves) if moves else "swap"

        if move == "spatial":
            other_dims = [d for d in self._scope.loop_dims if d != candidate.spatial_dim]
            new_spatial_dim = self._rng.choice(other_dims)
            return build_flat_mapping_candidate(
                self._scope, spatial_dim=new_spatial_dim, temporal_order=candidate.temporal_order
            )

        order = list(candidate.temporal_order)
        if len(order) >= 2:
            i, j = self._rng.sample(range(len(order)), 2)
            order[i], order[j] = order[j], order[i]
        return build_flat_mapping_candidate(
            self._scope, spatial_dim=candidate.spatial_dim, temporal_order=tuple(order)
        )

    def propose(self, state: SearchState, k: int) -> list[Candidate]:
        if k != 1:
            raise ValueError(
                "SimulatedAnnealingMappingStrategy proposes exactly one neighbor per call "
                "(classical serial-chain SA) — see module docstring"
            )
        if self._proposed is not None:
            raise RuntimeError("propose() called again before observe() for the pending proposal")
        proposed = self._neighbor(self._current)
        self._proposed = proposed
        return [Candidate(workload=state.workload, arch=state.arch, mapping=proposed.mapping)]

    def observe(self, results: list[Result | Exception]) -> None:
        if len(results) != 1:
            raise ValueError("observe() expects exactly one result, matching propose()'s k=1")
        if self._proposed is None:
            raise RuntimeError("observe() called without a pending propose()")
        proposed = self._proposed
        self._proposed = None
        outcome = results[0]
        self._iteration += 1

        if isinstance(outcome, Exception):
            self.evaluated.append(EvaluatedCandidate(candidate=proposed, result=None, error=str(outcome)))
            self._temperature *= self._cooling_rate
            return

        self.evaluated.append(EvaluatedCandidate(candidate=proposed, result=outcome, error=None))
        value = outcome.metrics[self._metric].value
        self._maybe_accept(proposed, value)
        self._maybe_record_best(proposed, value, outcome)
        self._temperature *= self._cooling_rate

    def _maybe_accept(self, proposed: MappingCandidate, value: float) -> None:
        if self._current_value is None:
            self._current, self._current_value = proposed, value
            return
        sign = 1 if self._minimize else -1
        delta = sign * (value - self._current_value)
        accept = delta <= 0 or self._rng.random() < math.exp(-delta / max(self._temperature, 1e-12))
        if accept:
            self._current, self._current_value = proposed, value

    def _maybe_record_best(self, proposed: MappingCandidate, value: float, result: Result) -> None:
        is_better = self.best_value is None or (
            value < self.best_value if self._minimize else value > self.best_value
        )
        if is_better:
            self.best, self.best_value, self.best_result = proposed, value, result

    def done(self) -> bool:
        return self._iteration >= self._max_iterations or self._temperature < self._min_temperature


class _EvaluatorProtocol:
    def evaluate(self, candidate: Candidate, budget: Any, metrics: frozenset[str]) -> Result: ...


@dataclass(frozen=True, slots=True)
class AnnealingSearchReport:
    evaluated: list[EvaluatedCandidate]
    best: MappingCandidate | None
    best_result: Result | None
    metric: str
    skipped_not_expressible: int
    iterations: int


def run_simulated_annealing(
    workload: dict[str, Any],
    arch: dict[str, Any],
    evaluator: _EvaluatorProtocol,
    *,
    for_op: str,
    metric: str,
    minimize: bool = True,
    initial_temperature: float = 1.0,
    cooling_rate: float = 0.9,
    min_temperature: float = 1e-3,
    max_iterations: int = 200,
    seed: int = 0,
) -> AnnealingSearchReport:
    """Drive `SimulatedAnnealingMappingStrategy` end to end against a real `Evaluator`: propose
    one neighbor, evaluate it (catching per-candidate failures as rejected moves — see the
    strategy's own docstring), observe, repeat until `done()`.
    """
    strategy = SimulatedAnnealingMappingStrategy(
        workload,
        arch,
        for_op=for_op,
        metric=metric,
        minimize=minimize,
        initial_temperature=initial_temperature,
        cooling_rate=cooling_rate,
        min_temperature=min_temperature,
        max_iterations=max_iterations,
        seed=seed,
    )
    state = SearchState(workload=workload, arch=arch, for_op=for_op)
    budget = Budget()

    while not strategy.done():
        (candidate,) = strategy.propose(state, k=1)
        try:
            outcome: Result | Exception = evaluator.evaluate(candidate, budget, frozenset({metric}))
        except Exception as exc:  # noqa: BLE001 - a candidate the evaluator refuses is expected, not fatal
            outcome = exc
        strategy.observe([outcome])

    return AnnealingSearchReport(
        evaluated=strategy.evaluated,
        best=strategy.best,
        best_result=strategy.best_result,
        metric=metric,
        skipped_not_expressible=sum(1 for e in strategy.evaluated if e.error is not None),
        iterations=len(strategy.evaluated),
    )
