"""The Evaluator ABI call surface (docs/evaluator-abi.md)."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .types import Budget, Candidate, Result


@runtime_checkable
class Evaluator(Protocol):
    """Any cost model that implements this becomes swappable behind the ABI (docs/evaluator-abi.md).
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
        """Batch mode is mandatory (docs/evaluator-abi.md): agentic/evolutionary search submits
        10**3-10**5 candidates at a time, and a one-call-per-candidate interface makes
        serialisation overhead dominate. Implementations are free to evaluate sequentially
        internally at v0.1 — the *interface* being batched is what matters for now; batching
        performance is a Phase 3 concern (docs/roadmap.md).

        **If it returns, it returns exactly one `Result` per candidate, in the order given.** An
        implementation that cannot evaluate a candidate raises (for the whole batch — per-item
        error isolation is not required at v0.1); it must not drop the candidate from the returned
        list. Callers pair results to candidates positionally and have no other way to tell which
        is which, so a short list silently re-pairs every result after the gap with the wrong
        candidate — or, if the drop is at the end, silently deletes candidates from the caller's
        view of its own sweep (docs/decisions.md D165, where that produced a confidently wrong DSE
        winner). This was the unstated invariant `search/architecture/dse.py` was assuming; stating
        it here is what makes that a bug in the implementation rather than in the caller.
        `tests/unit/test_batch_length_conformance.py` checks it against every registered backend.
        """
        ...
