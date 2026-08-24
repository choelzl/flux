"""Exhaustive flat-mapping search strategy (docs/search.md)."""

from __future__ import annotations

from .candidates import (
    FlatMappingScope,
    MappingCandidate,
    NotAFlatMappingCandidate,
    build_flat_mapping_candidate,
    generate_flat_mapping_candidates,
    parse_flat_mapping_scope,
)
from .annealing import (AnnealingSearchReport, SimulatedAnnealingMappingStrategy,
                        run_simulated_annealing)
from .engine import EvaluatorProtocol, classify_outcome, drive_propose_observe_loop
from .strategy import (
    EvaluatedCandidate,
    ExhaustiveMappingStrategy,
    ExhaustiveSearchReport,
    SearchState,
    run_exhaustive_search,
)

__all__ = [
    "AnnealingSearchReport",
    "SimulatedAnnealingMappingStrategy",
    "run_simulated_annealing",
    "classify_outcome",
    "EvaluatorProtocol",
    "drive_propose_observe_loop",
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
