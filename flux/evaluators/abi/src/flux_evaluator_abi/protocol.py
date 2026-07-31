"""The Evaluator ABI call surface (docs/04.md §4.1, §4.3)."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .types import Budget, Candidate, Result


@runtime_checkable
class Evaluator(Protocol):
    """Any cost model that implements this becomes swappable behind the ABI (docs/04.md §4).
    Adapters (ZigZag, Timeloop+Accelergy, ...) each provide one of these; the conformance suite
    (tests/conformance/) proves a given adapter interprets the IR the same way as the reference,
    or fails loudly (`not_expressible_in`) on the parts it cannot express, rather than silently
    approximating.
    """

    def evaluate(self, candidate: Candidate, budget: Budget, metrics: frozenset[str]) -> Result:
        """Evaluate a single candidate."""
        ...

    def evaluate_batch(
        self, candidates: list[Candidate], budget: Budget, metrics: frozenset[str]
    ) -> list[Result]:
        """Batch mode is mandatory (docs/04.md §4.3): agentic/evolutionary search submits
        10**3-10**5 candidates at a time, and a one-call-per-candidate interface makes
        serialisation overhead dominate. Implementations are free to evaluate sequentially
        internally at v0.1 — the *interface* being batched is what matters for now; batching
        performance is a Phase 3 concern (docs/05.md).
        """
        ...
