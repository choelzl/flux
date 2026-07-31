"""Shared document/chunk types for the knowledge layer (docs/04.md §7.2, docs/00-decisions.md
D3). A `Chunk` is the retrieval unit: one paragraph-sized piece of a source document, tagged with
enough provenance to cite where it came from — never returned or stored without that provenance,
matching this repo's evaluator `Result.provenance` convention.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Chunk:
    """One retrievable unit of ingested text.

    `id` is stable and content-derived (`{standard_id}/{source_stem}#{index}`), not an
    auto-increment counter, so re-ingesting the same corpus produces the same ids.
    """

    id: str
    standard_id: str
    source_path: str
    heading: str | None
    text: str
