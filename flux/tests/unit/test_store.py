"""flux_store.ResultStore (docs/records.md): content-addressed documents (an objective, a digest)."""

from __future__ import annotations

from flux_store.canonical import content_hash as _content_hash
import pytest
from flux_store import ResultStore


@pytest.fixture
def store(tmp_path):
    with ResultStore(tmp_path / "flux.db") as s:
        yield s


def test_put_and_get_document_round_trips(store):
    doc = {"schema_version": "0.1.0", "id": "x", "ops": [{"id": "op0", "kind": "einsum"}]}
    content_hash = store.put_document("objective", doc)

    assert content_hash == _content_hash(doc)
    assert store.get_document(content_hash) == doc


def test_get_document_returns_none_for_unknown_hash(store):
    assert store.get_document("deadbeef" * 8) is None


def test_put_document_is_idempotent(store):
    doc = {"id": "x"}
    h1 = store.put_document("objective", doc)
    h2 = store.put_document("objective", doc)
    assert h1 == h2
    count = store._conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    assert count == 1


def test_put_document_rejects_unknown_kind(store):
    with pytest.raises(ValueError, match="unknown document kind"):
        store.put_document("bogus", {"id": "x"})
