"""Schema loading and validation for Flux IR documents (docs/ir.md)."""

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
    "objective": "objective.schema.json",
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


# Cap on how many schema errors one failure message carries. Enough to fix a document in one
# pass; short of pasting an entire malformed file back at a caller.
_MAX_REPORTED_ERRORS = 10


def validate(kind: str, doc: dict[str, Any]) -> None:
    """Validate `doc` against the named IR schema ('workload' | 'architecture' | 'mapping').

    Raises SchemaValidationError listing **every** violation found (up to `_MAX_REPORTED_ERRORS`),
    each with its path in the document; returns None on success.

    All errors, not just the first, because an LLM repair loop consumes this message and has a
    small retry budget (D187).
    """
    schema = _load_schema(kind)
    errors = sorted(
        jsonschema.Draft202012Validator(schema).iter_errors(doc),
        key=lambda e: (list(map(str, e.path)), e.message),
    )
    if not errors:
        return
    doc_id = doc.get("id", "<no id>")
    shown = errors[:_MAX_REPORTED_ERRORS]
    detail = "; ".join(
        f"{'/'.join(map(str, e.path)) or '<root>'}: {e.message}" for e in shown
    )
    if len(errors) > len(shown):
        detail += f"; ... and {len(errors) - len(shown)} more"
    raise SchemaValidationError(
        f"{kind} document {doc_id!r} failed schema validation "
        f"({len(errors)} error{'s' if len(errors) != 1 else ''}): {detail}"
    ) from errors[0]
