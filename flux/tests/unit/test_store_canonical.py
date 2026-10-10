"""Canonicalisation + content hashing of the documents a record stores (flux_store.canonical, D959)."""

from __future__ import annotations

from flux_store import canonical as flux_ir


def test_hash_is_independent_of_key_order():
    a = {"b": 1, "a": 2, "nested": {"y": 1, "x": 2}}
    b = {"a": 2, "nested": {"x": 2, "y": 1}, "b": 1}
    assert flux_ir.content_hash(a) == flux_ir.content_hash(b)


def test_hash_changes_when_content_changes():
    a = {"a": 1}
    b = {"a": 2}
    assert flux_ir.content_hash(a) != flux_ir.content_hash(b)


def test_hash_is_deterministic_across_calls():
    doc = {"id": "x", "ops": [{"id": "op0", "kind": "einsum"}]}
    assert flux_ir.content_hash(doc) == flux_ir.content_hash(doc)


def test_hash_is_a_64_char_hex_sha256():
    h = flux_ir.content_hash({"a": 1})
    assert len(h) == 64
    assert all(c in "0123456789abcdef" for c in h)
