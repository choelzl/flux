"""`ChiaParallelEvaluator` — wraps a backend name as an Evaluator ABI `Evaluator` whose
`evaluate_batch()` dispatches every candidate to Ray concurrently via `flux_evaluate.chia_remote`,
instead of the ABI's usual sequential default (every adapter's own `evaluate_batch` in this repo
just loops `evaluate()`).

Same `Evaluator` interface (docs/04.md §4.1) as every other adapter — any code written against
`evaluate`/`evaluate_batch` (e.g. `search/architecture`'s sweep) gets real CHIA/Ray parallelism
for free by being handed this instead of a plain evaluator, not by being rewritten to know about
CHIA. That's the point of docs/04.md's layering: L5 Search stays CHIA-agnostic; L6 Flows (this
module) is where the CHIA-specific adaptation lives.
"""

from __future__ import annotations

from typing import Any

from chia.base.ChiaFunction import get
from flux_evaluator_abi import Budget, Candidate, Result

from .evaluate import flux_evaluate


class ChiaParallelEvaluator:
    """`evaluate()` is a plain local call (no Ray); `evaluate_batch()` submits every candidate as
    a separate Ray task via `flux_evaluate.chia_remote(...)` and waits for all of them — real
    concurrent dispatch, not a sequential loop wearing a batch-shaped interface.
    """

    def __init__(self, backend: str) -> None:
        self.backend = backend

    def evaluate(self, candidate: Candidate, budget: Budget, metrics: frozenset[str]) -> Result:
        return flux_evaluate(
            self.backend, candidate.workload, candidate.arch, candidate.mapping,
            list(metrics), budget.wall_clock_s, budget.usd,
        )

    def evaluate_batch(
        self, candidates: list[Candidate], budget: Budget, metrics: frozenset[str]
    ) -> list[Result]:
        refs = [
            flux_evaluate.chia_remote(
                self.backend, c.workload, c.arch, c.mapping,
                list(metrics), budget.wall_clock_s, budget.usd,
            )
            for c in candidates
        ]
        return get(refs)
