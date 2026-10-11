"""ChampSim, owned by flux: run an `.ini` or build a prefetcher header, measure it on traces.
`python -m flux_helpers.champsim build|check|run` is its command line (commands.py)."""

from .baseline import baseline_ipc
from .binary import ChampSimUnavailableError, fingerprint, resolve_binary, resolve_source_tree
from .build import BuildError, BuildResult, build_header, install, stage_tree
from .run import SimulationFailedError, parse, simulate
from .study import BuildFailedError, measure, prepare, split_ini
from .traces import resolve as resolve_traces, stage as stage_traces

__all__ = [
    "BuildError", "BuildFailedError", "BuildResult", "ChampSimUnavailableError",
    "SimulationFailedError",
    "baseline_ipc", "build_header", "fingerprint", "install", "measure", "parse", "prepare", "resolve_binary",
    "resolve_source_tree", "resolve_traces", "simulate", "split_ini", "stage_traces", "stage_tree",
]


def catalog() -> list[dict]:
    """This helper's catalog entries (D968): build, smoke-run and measure a prefetcher."""
    from .._entries import N_FAILING, metrics, param

    traces = param("Trace folder", "{home}/traces")
    return [{"id": "champsim-build", "role": "check", "title": "Build the prefetcher into ChampSim",
             "what": "Compiles a prefetcher header into ChampSim; reports the first compiler error.",
             "run": "{python} -m flux_helpers.champsim build {artifact}", "params": {}, "needs": ["pythia"],
             "pass": "passes when it builds; exit 3 = did not build", "languages": ["cpp"], "kinds": ["champsim"], "short": "build"},
            {"id": "champsim-check", "role": "check", "title": "Smoke-run the prefetcher",
             "what": "Builds the prefetcher and runs it on one trace; refused when it issues no prefetches.",
             "run": "{python} -m flux_helpers.champsim check {artifact} --traces {traces}", "params": {"traces": traces},
             "needs": ["pythia"], "pass": N_FAILING, "languages": ["cpp"], "kinds": ["champsim"], "short": "smoke"},
            {"id": "champsim-run", "role": "stage", "title": "Simulate on your traces (ChampSim)",
             "what": "Runs an .ini or a prefetcher header on every trace; the geometric-mean IPC speed-up over no prefetcher.",
             "run": "{python} -m flux_helpers.champsim run {artifact} --traces {traces} --warmup {warmup} --sim {sim}",
             "params": {"traces": traces, "warmup": param("Warm-up", 10_000_000, "instructions"),
                        "sim": param("Simulated", 15_000_000, "instructions")},
             "metrics": metrics("geomean_speedup"), "needs": ["pythia"], "languages": ["ini", "cpp"], "kinds": ["champsim"],
             "short": "sim"}]
