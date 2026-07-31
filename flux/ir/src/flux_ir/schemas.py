"""Schema loading and validation for Flux IR documents (docs/04.md §3)."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

import jsonschema

_SCHEMAS_DIR = Path(__file__).resolve().parent / "schemas"

_SCHEMA_FILES = {
    "workload": "workload.schema.json",
    "architecture": "architecture.schema.json",
    "mapping": "mapping.schema.json",
}


class SchemaValidationError(ValueError):
    """An IR document failed schema validation."""


@lru_cache(maxsize=None)
def _load_schema(kind: str) -> dict[str, Any]:
    if kind not in _SCHEMA_FILES:
        raise KeyError(f"unknown IR kind {kind!r}; expected one of {sorted(_SCHEMA_FILES)}")
    path = _SCHEMAS_DIR / _SCHEMA_FILES[kind]
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def validate(kind: str, doc: dict[str, Any]) -> None:
    """Validate `doc` against the named IR schema ('workload' | 'architecture' | 'mapping').

    Raises SchemaValidationError on failure with the document id in the message; returns None
    on success.
    """
    schema = _load_schema(kind)
    try:
        jsonschema.validate(instance=doc, schema=schema)
    except jsonschema.ValidationError as exc:
        doc_id = doc.get("id", "<no id>")
        raise SchemaValidationError(
            f"{kind} document {doc_id!r} failed schema validation: {exc.message}"
        ) from exc
