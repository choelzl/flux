"""The Evaluator ABI call surface (docs/evaluator-abi.md)."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .types import Budget, Candidate, Result


class SequentialBatch:
    """The ABI's `evaluate_batch` default (D441): one `evaluate` per candidate, in order. Every
    adapter uses it as a base; the `Evaluator` protocol below stays the structural check.
    Concurrency comes from wrapping an evaluator in a concurrent one, not from adapters."""

    def evaluate(self, candidate: Candidate, budget: Budget, metrics: frozenset[str]) -> Result:
        raise NotImplementedError(f"{type(self).__name__}.evaluate")

    def evaluate_batch(self, candidates: list[Candidate], budget: Budget,
                       metrics: frozenset[str]) -> list[Result]:
        return [self.evaluate(c, budget, metrics) for c in candidates]


@runtime_checkable
class Evaluator(Protocol):
    """Any cost model that implements this becomes swappable behind the ABI (docs/evaluator-abi.md).
    The conformance suite (tests/conformance/) proves an adapter interprets the IR like the
    reference, or fails loudly (`not_expressible_in`) where it cannot express it, never
    silently approximating.
    """

    name: str
    """The registry's name for this backend (`"rtl"`, `"zigzag"`, ...): what a task document
    writes to ask for it, and the prefix of every `Result.provenance.evaluator` it produces
    (D426). `flux_evaluator_abi.make_evaluator(name)` resolves it back."""

    def evaluate(self, candidate: Candidate, budget: Budget, metrics: frozenset[str]) -> Result:
        """Evaluate a single candidate."""
        ...

    def evaluate_batch(
        self, candidates: list[Candidate], budget: Budget, metrics: frozenset[str]
    ) -> list[Result]:
        """Evaluate many candidates in one call (search submits 10**3-10**5 at a time).
        Implementations may evaluate sequentially internally.

        If it returns, it returns exactly one `Result` per candidate, in the order given. A
        candidate that cannot be evaluated raises (for the whole batch); it must never be
        dropped, since callers pair results positionally and a short list silently mis-pairs or
        loses candidates (D165). `tests/unit/test_batch_length_conformance.py` checks every
        registered backend.
        """
        ...
