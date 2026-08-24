"""ChampSim/Pythia L2 prefetcher evaluator (docs/decisions.md D349)."""

from .adapter import (
    EVALUATOR_ID, ChampSimBingoEvaluator, NotExpressibleError, SimulationFailedError,
    config_of, run_champsim,
)
from .binary import (
    BINARY_NAME, ON_PATH, ChampSimUnavailableError, resolve_binary, resolve_source_tree,
    resolve_trace,
)

__all__ = [
    "BINARY_NAME", "EVALUATOR_ID", "ON_PATH", "ChampSimBingoEvaluator", "ChampSimUnavailableError",
    "NotExpressibleError", "SimulationFailedError", "config_of", "resolve_binary",
    "resolve_source_tree", "resolve_trace", "run_champsim",
]
