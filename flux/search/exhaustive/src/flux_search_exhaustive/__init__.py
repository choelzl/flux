"""Exhaustive flat-mapping search strategy (docs/04.md §6)."""

from __future__ import annotations

from .candidates import (
    FlatMappingScope,
    MappingCandidate,
    NotAFlatMappingCandidate,
    build_flat_mapping_candidate,
    generate_flat_mapping_candidates,
    parse_flat_mapping_scope,
)
from .strategy import (
    EvaluatedCandidate,
    ExhaustiveMappingStrategy,
    ExhaustiveSearchReport,
    SearchState,
    run_exhaustive_search,
)

__all__ = [
    "MappingCandidate",
    "FlatMappingScope",
    "NotAFlatMappingCandidate",
    "generate_flat_mapping_candidates",
    "parse_flat_mapping_scope",
    "build_flat_mapping_candidate",
    "SearchState",
    "EvaluatedCandidate",
    "ExhaustiveMappingStrategy",
    "ExhaustiveSearchReport",
    "run_exhaustive_search",
]
