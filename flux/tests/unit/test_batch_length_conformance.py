"""`evaluate_batch` returns one Result per candidate, in order — checked against every registered
backend (docs/evaluator-abi.md "Batch mode", docs/decisions.md D165).

D165 found `search/architecture/dse.py` pairing batch results to candidates with `zip`, which
truncates silently when the lists disagree: a batch that dropped one candidate produced a sweep
reporting no errors and the wrong winner. The caller was fixed there, but the invariant it had
been assuming was written down nowhere and checked nowhere, so the next adapter to filter its
failures out of a batch would reintroduce the same bug one caller further along.

This runs against real adapter instances from the CLI registry — none of them need their external
tool present to be constructed — with `evaluate` stubbed. Stubbing is the point rather than a
compromise: every adapter's `evaluate_batch` is a one-line delegation to `self.evaluate`, so what
is under test is exactly the wrapper's own length- and order-preservation, and the check stays
runnable without Docker, Verilator, gem5 or a Rust build. Registry-driven so a backend added later
is covered without anyone remembering to add it here.
"""

from __future__ import annotations

import pytest
from flux_evaluator_abi import Budget, Candidate

_WORKLOAD = {"schema_version": "0.1.0", "id": "test/wl", "tensors": [], "ops": []}
_METRICS = frozenset({"latency_cycles"})


def _backends() -> list[str]:
    from flux_cli.registry import available_backends

    return available_backends()


def _candidates(n: int) -> list[Candidate]:
    return [
        Candidate(workload=_WORKLOAD, arch={"schema_version": "0.1.0", "id": f"arch{i}"}, mapping=None)
        for i in range(n)
    ]


def test_the_registry_is_non_empty():
    """Guards the guard: an empty backend list would turn every parametrised case below into a
    vacuous pass."""
    assert len(_backends()) >= 12


@pytest.mark.parametrize("name", _backends())
def test_evaluate_batch_returns_one_result_per_candidate_in_order(name):
    from flux_cli.registry import make_evaluator

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
    """The boundary a sequential loop gets right by construction and a hand-written batching path
    (the Phase 3 direction docs/roadmap.md names) is most likely to get wrong."""
    from flux_cli.registry import make_evaluator

    evaluator = make_evaluator(name)
    evaluator.evaluate = lambda c, b, m: c.arch["id"]

    assert evaluator.evaluate_batch(_candidates(1), Budget(), _METRICS) == ["arch0"]


@pytest.mark.parametrize("name", _backends())
def test_evaluate_batch_of_zero_candidates(name):
    """An empty batch is a real input — `run_architecture_dse` passes whatever the candidate
    generator produced, and a filter can leave nothing."""
    from flux_cli.registry import make_evaluator

    evaluator = make_evaluator(name)
    evaluator.evaluate = lambda c, b, m: c.arch["id"]

    assert evaluator.evaluate_batch([], Budget(), _METRICS) == []


def test_a_filtering_batch_implementation_would_fail_this_check():
    """Guards the guard, the other way: proves these assertions can actually fail. A batch that
    silently drops the candidates it can't handle is the realistic way an adapter breaks the
    invariant — it looks like reasonable error handling from inside the adapter.
    """

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
