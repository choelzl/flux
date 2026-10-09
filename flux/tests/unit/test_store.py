"""flux_store.ResultStore (docs/stores.md) with a synthetic Result. The real-evaluator version is
tests/integration/test_store_live.py.
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
