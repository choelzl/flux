"""Canonicalisation and content-addressed hashing for Flux IR documents (docs/ir.md)."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonicalize(doc: dict[str, Any]) -> str:
    """Deterministic JSON serialisation: sorted keys, no insignificant whitespace, fixed
    separators. Two documents that are semantically identical but differ in key order or
    formatting canonicalise to the same string.
    """
    return json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def content_hash(doc: dict[str, Any]) -> str:
    """sha256 of the canonical form, hex-encoded. This is the cache key for everything
    downstream and the lineage key for everything upstream (docs/gap-analysis.md G9, G10).
    """
    canonical = canonicalize(doc)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
