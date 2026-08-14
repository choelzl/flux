"""Real, dependency-tracked re-evaluation for `flux_sweep_dynamic_shape` (docs/decisions.md D86,
generalizing D79's own pattern beyond `flux_characterize_memory_level`): a stub evaluator counting
real calls, so hit/miss behavior is checked directly, not inferred from timing — the same
discipline `tests/unit/test_memory_characterize_caching.py` established. See
tests/integration/test_dynamic_shape_sweep_live.py for the real-evaluator version.
"""

from __future__ import annotations

import pytest
from flux_chia_nodes import dynamic_shape
from flux_evaluator_abi import (
    Bottleneck,
    Budget,
    Candidate,
    Domain,
    Escalation,
    Estimate,
    Limiter,
    Method,
    Provenance,
    Result,
    Validity,
)
from flux_store import ResultStore

_WORKLOAD = {
    "id": "w",
    "ops": [
        {"id": "op0", "kind": "einsum", "expr": "S D, T D -> S T", "bounds": {"S": 1, "D": 64, "T": {"dyn": [1, 256]}}},
    ],
}
_ARCH_A = {"schema_version": "0.1.0", "id": "arch-a", "hierarchy": []}
_ARCH_B = {"schema_version": "0.1.0", "id": "arch-b", "hierarchy": []}


def _make_result(evaluator: str, value: float) -> Result:
    return Result(
        metrics={"latency_cycles": Estimate(value=value, ci_low=value, ci_high=value, unit="cycles", method=Method.ANALYTIC)},
        validity=Validity(ok=True, checker_version="test"),
        domain=Domain(in_domain=True),
        bottleneck=Bottleneck(limiter=Limiter.COMPUTE),
        provenance=Provenance(evaluator=evaluator, inputs={}),
        escalation=Escalation(recommended=False),
    )


class _CountingEvaluator:
    """Reports `evaluator="zigzag@stub"` (matching this test's own default `backend="zigzag"`) so
    `CachingEvaluator`'s own `evaluator_prefix` filter finds stored rows correctly.
    """

    def __init__(self) -> None:
        self.calls: list[Candidate] = []

    def evaluate(self, candidate: Candidate, budget: Budget, metrics: frozenset[str]) -> Result:
        self.calls.append(candidate)
        t = candidate.workload["ops"][0]["bounds"]["T"]
        return _make_result("zigzag@stub", float(t))

    def evaluate_batch(self, candidates, budget, metrics):  # pragma: no cover - unused here
        return [self.evaluate(c, budget, metrics) for c in candidates]


@pytest.fixture
def store(tmp_path):
    with ResultStore(tmp_path / "flux.db") as s:
        yield s


@pytest.fixture
def counting_evaluator(monkeypatch):
    stub = _CountingEvaluator()
    monkeypatch.setattr(dynamic_shape, "make_evaluator", lambda backend: stub)
    return stub


def test_without_result_db_path_every_call_is_real_unchanged_behavior(counting_evaluator):
    """No regression for every existing caller that never passes `result_db_path` — the default
    stays "always call the real evaluator", exactly as before this decision."""
    dynamic_shape.flux_sweep_dynamic_shape("zigzag", _WORKLOAD, "op0", "T", [1, 8])
    dynamic_shape.flux_sweep_dynamic_shape("zigzag", _WORKLOAD, "op0", "T", [1, 8])
    assert len(counting_evaluator.calls) == 4  # 2 sample points x 2 calls, nothing cached


def test_a_duplicate_sample_point_within_one_call_is_a_real_cache_hit(counting_evaluator, store):
    """The real, immediate opportunity this decision closes: `sample_points` has no dedup of its
    own — [1, 8, 1] previously meant three real evaluator calls for two distinct values."""
    dynamic_shape.flux_sweep_dynamic_shape(
        "zigzag", _WORKLOAD, "op0", "T", [1, 8, 1], result_db_path=str(store.db_path),
    )
    assert len(counting_evaluator.calls) == 2  # value=1 evaluated once, not twice


def test_repeating_the_exact_same_sweep_across_calls_is_a_real_cache_hit(counting_evaluator, store):
    dynamic_shape.flux_sweep_dynamic_shape(
        "zigzag", _WORKLOAD, "op0", "T", [1, 8, 32], result_db_path=str(store.db_path),
    )
    dynamic_shape.flux_sweep_dynamic_shape(
        "zigzag", _WORKLOAD, "op0", "T", [1, 8, 32], result_db_path=str(store.db_path),
    )
    assert len(counting_evaluator.calls) == 3  # only the first call's 3 distinct points were real


def test_a_different_arch_for_the_same_sample_points_is_a_real_cache_miss(counting_evaluator, store):
    """Not over-broad: `arch` is a real part of the Candidate this wraps — a genuinely different
    architecture must still force real re-evaluation, same discipline D79 already established."""
    dynamic_shape.flux_sweep_dynamic_shape(
        "zigzag", _WORKLOAD, "op0", "T", [1, 8], arch=_ARCH_A, result_db_path=str(store.db_path),
    )
    dynamic_shape.flux_sweep_dynamic_shape(
        "zigzag", _WORKLOAD, "op0", "T", [1, 8], arch=_ARCH_B, result_db_path=str(store.db_path),
    )
    assert len(counting_evaluator.calls) == 4  # 2 distinct points x 2 distinct archs


def test_result_db_path_is_a_string_not_a_store_object(counting_evaluator, store):
    """Deliberately mirrors flux_evaluate's own `result_db_path: str` convention, not
    flux_characterize_memory_level's `store: ResultStore` one (docs/decisions.md D86) — a caller
    only ever provides a path, the node owns opening/closing the store."""
    result = dynamic_shape.flux_sweep_dynamic_shape(
        "zigzag", _WORKLOAD, "op0", "T", [1], result_db_path=str(store.db_path),
    )
    assert result.metrics["latency_cycles"].value == pytest.approx(1.0)
