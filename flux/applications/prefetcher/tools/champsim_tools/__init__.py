"""ChampSim, owned by flux: run an `.ini` or build a prefetcher header, measure it on traces."""

from flux_evaluator_abi import NotExpressibleError  # noqa: F401  -- the ABI's one refusal (D426)

from .adapter import EVALUATOR_ID, ChampSimEvaluator
from .baseline import baseline_ipc
from .binary import ChampSimUnavailableError, fingerprint, resolve_binary, resolve_source_tree
from .build import BuildError, BuildResult, build_header, install, stage_tree
from .run import SimulationFailedError, parse, simulate
from .study import BuildFailedError, measure, prepare, split_ini
from .traces import resolve as resolve_traces, stage as stage_traces

__all__ = [
    "EVALUATOR_ID", "BuildError", "BuildFailedError", "BuildResult", "ChampSimEvaluator", "ChampSimUnavailableError", "NotExpressibleError",
    "SimulationFailedError",
    "baseline_ipc", "build_header", "fingerprint", "install", "measure", "parse", "prepare", "resolve_binary",
    "resolve_source_tree", "resolve_traces", "simulate", "split_ini", "stage_traces", "stage_tree",
]
