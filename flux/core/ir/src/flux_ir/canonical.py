"""Canonicalisation and content-addressed hashing for Flux IR documents (docs/ir.md)."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonicalize(doc: dict[str, Any]) -> str:
    """Deterministic JSON: sorted keys, fixed separators, no insignificant whitespace, so key
    order and formatting do not change the result."""
    return json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def content_hash(doc: dict[str, Any]) -> str:
    """sha256 of the canonical form, hex-encoded: the cache key and the lineage key."""
    canonical = canonicalize(doc)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
