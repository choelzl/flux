"""Flux IR: canonicalisation, content-addressed hashing, and schema validation for the
Workload / Architecture / Mapping IR documents (docs/ir.md, amended by docs/decisions.md D1).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .canonical import canonicalize, content_hash
from .schemas import SchemaValidationError, validate

__all__ = [
    "canonicalize",
    "content_hash",
    "validate",
    "SchemaValidationError",
    "load_document",
]


def load_document(path: str | Path) -> dict[str, Any]:
    """Load a YAML or JSON IR document from disk into a plain dict."""
    with Path(path).open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)
