"""Unit tests for flux_store.ResultStore (docs/04.md §8). Uses a synthetic Result rather than
invoking a real evaluator — see tests/integration/test_store_live.py for the real-evaluator
version.
"""

from __future__ import annotations

import flux_ir
import pytest
from flux_evaluator_abi import (
    Bottleneck,
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


def _sample_result(evaluator: str = "test-evaluator@0.0.0") -> Result:
    return Result(
        metrics={
            "latency_cycles": Estimate(
                value=100, ci_low=100, ci_high=100, unit="cycles", method=Method.ANALYTIC
            )
        },
        validity=Validity(ok=True, checker_version="test"),
        domain=Domain(in_domain=False),
        bottleneck=Bottleneck(limiter=Limiter.COMPUTE),
        provenance=Provenance(evaluator=evaluator, inputs={}),
        escalation=Escalation(recommended=False),
    )


@pytest.fixture
def store(tmp_path):
    with ResultStore(tmp_path / "flux.db") as s:
        yield s


def test_put_and_get_document_round_trips(store):
    doc = {"schema_version": "0.1.0", "id": "x", "ops": [{"id": "op0", "kind": "einsum"}]}
    content_hash = store.put_document("workload", doc)

    assert content_hash == flux_ir.content_hash(doc)
    assert store.get_document(content_hash) == doc


def test_get_document_returns_none_for_unknown_hash(store):
    assert store.get_document("deadbeef" * 8) is None


def test_put_document_is_idempotent(store):
    doc = {"id": "x"}
    h1 = store.put_document("workload", doc)
    h2 = store.put_document("workload", doc)
    assert h1 == h2
    count = store._conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    assert count == 1


def test_put_document_rejects_unknown_kind(store):
    with pytest.raises(ValueError, match="unknown IR kind"):
        store.put_document("bogus", {"id": "x"})


def test_put_and_get_result_round_trips(store):
    result = _sample_result()
    row_id = store.put_result(result, workload_hash="wh123", arch_hash="ah456")

    fetched = store.get_result(row_id)
    assert fetched["workload_hash"] == "wh123"
    assert fetched["arch_hash"] == "ah456"
    assert fetched["mapping_hash"] is None
    assert fetched["evaluator"] == "test-evaluator@0.0.0"
    assert fetched["result"] == result.to_dict()
    assert fetched["created_at"]  # non-empty timestamp


def test_get_result_returns_none_for_unknown_id(store):
    assert store.get_result(999) is None


def test_find_results_filters_by_workload_hash(store):
    id_a = store.put_result(_sample_result(), workload_hash="wh-a")
    id_b = store.put_result(_sample_result(), workload_hash="wh-b")

    matches = store.find_results(workload_hash="wh-a")
    assert [m["id"] for m in matches] == [id_a]
    assert id_b not in [m["id"] for m in matches]


def test_find_results_filters_by_multiple_fields(store):
    store.put_result(_sample_result("zigzag@1"), workload_hash="wh", arch_hash="ah-1")
    id_match = store.put_result(_sample_result("zigzag@1"), workload_hash="wh", arch_hash="ah-2")
    store.put_result(_sample_result("timeloop@1"), workload_hash="wh", arch_hash="ah-2")

    matches = store.find_results(workload_hash="wh", arch_hash="ah-2", evaluator="zigzag@1")
    assert [m["id"] for m in matches] == [id_match]


def test_find_results_with_no_filters_returns_everything_in_insertion_order(store):
    ids = [store.put_result(_sample_result(), workload_hash=f"wh-{i}") for i in range(3)]
    matches = store.find_results()
    assert [m["id"] for m in matches] == ids


def test_two_evaluators_on_the_same_workload_and_architecture_are_both_findable(store):
    """Mirrors the real cross-evaluator report scenario (docs/phase1-exit-criterion-report.md):
    same workload_hash and arch_hash, two different evaluators, both results retrievable."""
    zigzag_id = store.put_result(
        _sample_result("zigzag@3.8.5"), workload_hash="wh", arch_hash="ah"
    )
    timeloop_id = store.put_result(
        _sample_result("timeloop-docker@image"), workload_hash="wh", arch_hash="ah"
    )

    both = store.find_results(workload_hash="wh", arch_hash="ah")
    assert {m["id"] for m in both} == {zigzag_id, timeloop_id}
    assert {m["evaluator"] for m in both} == {"zigzag@3.8.5", "timeloop-docker@image"}
