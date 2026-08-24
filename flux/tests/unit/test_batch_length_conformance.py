"""`evaluate_batch` returns one Result per candidate, in order, for every registered backend (D165).

A caller pairing results with `zip` truncates silently when the lengths disagree, giving the wrong
winner with no error. This runs real adapter instances from the CLI registry with `evaluate`
stubbed: every adapter's `evaluate_batch` delegates to `self.evaluate`, so the wrapper's length and
order preservation is exactly what is under test, with no external tool needed.
"""

from __future__ import annotations

import pytest
from flux_evaluator_abi import Budget, Candidate

_WORKLOAD = {"schema_version": "0.1.0", "id": "test/wl", "tensors": [], "ops": []}
_METRICS = frozenset({"latency_cycles"})


def _backends() -> list[str]:
    from flux_evaluator_abi import available_evaluators

    return available_evaluators()


def _candidates(n: int) -> list[Candidate]:
    return [
        Candidate(workload=_WORKLOAD, arch={"schema_version": "0.1.0", "id": f"arch{i}"}, mapping=None)
        for i in range(n)
    ]


def test_the_registry_is_non_empty():
    """Guards the guard: an empty backend list would make every case below vacuous."""
    assert len(_backends()) >= 5


@pytest.mark.parametrize("name", _backends())
def test_evaluate_batch_returns_one_result_per_candidate_in_order(name):
    from flux_evaluator_abi import make_evaluator

    evaluator = make_evaluator(name)
    candidates = _candidates(4)
    seen: list[Candidate] = []

    def _stub(candidate, budget, metrics):
        seen.append(candidate)
        return candidate.arch["id"]  # a sentinel, not a Result: only pairing is under test here

    evaluator.evaluate = _stub
    returned = evaluator.evaluate_batch(candidates, Budget(), _METRICS)

    assert len(returned) == len(candidates), (
        f"{name}.evaluate_batch returned {len(returned)} results for {len(candidates)} candidates; "
        "docs/evaluator-abi.md requires one per candidate — callers pair them positionally"
    )
    assert returned == [c.arch["id"] for c in candidates], (
        f"{name}.evaluate_batch reordered its results; positional pairing makes that "
        "indistinguishable from returning the wrong numbers"
    )
    assert seen == candidates, f"{name}.evaluate_batch did not evaluate every candidate exactly once"


@pytest.mark.parametrize("name", _backends())
def test_evaluate_batch_of_one_candidate(name):
    """Order is preserved across the batch."""
    from flux_evaluator_abi import make_evaluator

    evaluator = make_evaluator(name)
    evaluator.evaluate = lambda c, b, m: c.arch["id"]

    assert evaluator.evaluate_batch(_candidates(1), Budget(), _METRICS) == ["arch0"]


@pytest.mark.parametrize("name", _backends())
def test_evaluate_batch_of_zero_candidates(name):
    """An empty batch is valid input and returns an empty list."""
    from flux_evaluator_abi import make_evaluator

    evaluator = make_evaluator(name)
    evaluator.evaluate = lambda c, b, m: c.arch["id"]

    assert evaluator.evaluate_batch([], Budget(), _METRICS) == []


def test_a_filtering_batch_implementation_would_fail_this_check():
    """Guards the guard: a batch that silently drops candidates fails these assertions."""

    class _FilteringEvaluator:
        def evaluate(self, candidate, budget, metrics):
            if candidate.arch["id"] == "arch1":
                raise ValueError("not_expressible_in: [fake]")
            return candidate.arch["id"]

        def evaluate_batch(self, candidates, budget, metrics):
            out = []
            for c in candidates:
                try:
                    out.append(self.evaluate(c, budget, metrics))
                except ValueError:
                    continue  # the bug: the candidate vanishes instead of the batch raising
            return out

    candidates = _candidates(4)
    returned = _FilteringEvaluator().evaluate_batch(candidates, Budget(), _METRICS)

    assert len(returned) != len(candidates)
