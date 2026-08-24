"""Simulated annealing search strategy over the flat-mapping space (docs/search.md)."""

from __future__ import annotations

from .strategy import (
    AnnealingSearchReport,
    EvaluatedCandidate,
    SearchState,
    SimulatedAnnealingMappingStrategy,
    run_simulated_annealing,
)

__all__ = [
    "SearchState",
    "EvaluatedCandidate",
    "SimulatedAnnealingMappingStrategy",
    "AnnealingSearchReport",
    "run_simulated_annealing",
]
