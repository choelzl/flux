"""`flux_evaluate` — the first real CHIA library node (docs/agent-surface.md: "Flux ships CHIA library
nodes: `flux_evaluate`, `flux_search`, `flux_calibrate`, `flux_conformance_check`").

Wraps `flows/cli`'s evaluator registry (the same `make_evaluator(backend)` the `flux eval` CLI
command uses) as a real `@ChiaFunction()` — no separate CHIA-specific evaluator implementation,
matching docs/architecture.md's "adapters, not forks": one evaluator, dispatchable locally, from the
CLI, or now as a Ray task, all going through the exact same `Evaluator.evaluate()` call.

This is docs/agent-surface.md's illustrative `evaluate_design` example made real, using the actual
`@ChiaFunction()` decorator (github.com/ucb-bar/chia, not the placeholder `@flux_tool` name that
doc used before real CHIA's API was known). Two of the "three surfaces" live here — the typed
Python function, now also Ray-dispatchable via `.chia_remote()`/`.chia_remote_blocking()`. The
third, the MCP-tool surface, wraps this same function — see `flows/mcp/` (`FluxTool`).
"""

from __future__ import annotations

from typing import Any

from chia.base.ChiaFunction import ChiaFunction
from flux_cli.registry import DEFAULT_METRICS, make_evaluator
from flux_evaluator_abi import Budget, Candidate, Result
from flux_store import CachingEvaluator, ResultStore


@ChiaFunction()
def flux_evaluate(
    backend: str,
    workload: dict[str, Any],
    arch: dict[str, Any] | None = None,
    mapping: dict[str, Any] | None = None,
    metrics: list[str] | None = None,
    wall_clock_s: float | None = None,
    usd: float | None = None,
    result_db_path: str | None = None,
) -> Result:
    """Evaluate a candidate accelerator design through a named Flux evaluator backend.

    Returns the same `Result` docs/evaluator-abi.md defines: `Estimate`s with confidence intervals, an
    independently-computed `Validity`, a structured `Bottleneck`, and full `Provenance` — not a
    bare number. Call directly for a local (in-process) evaluation, or via
    `flux_evaluate.chia_remote(...)` / `.chia_remote_blocking(...)` to dispatch it as a real Ray
    task (see `tests/integration/test_chia_flux_evaluate_live.py` for both, against a real local
    Ray instance and the real ZigZag backend).

    `result_db_path` opts into warm-start (docs/decisions.md D19): pass the same path across
    calls and an identical `(workload, arch, mapping)` triple is served from the store instead of
    spending a real evaluator call. Omit it (the default) for the original always-real-evaluation
    behavior — this is additive, not a behavior change for existing callers.
    """
    evaluator = make_evaluator(backend)
    candidate = Candidate(workload=workload, arch=arch, mapping=mapping)
    budget = Budget(wall_clock_s=wall_clock_s, usd=usd)
    requested_metrics = frozenset(metrics) if metrics is not None else DEFAULT_METRICS

    if result_db_path is None:
        return evaluator.evaluate(candidate, budget, requested_metrics)

    with ResultStore(result_db_path) as store:
        cached = CachingEvaluator(evaluator, store, evaluator_prefix=backend)
        return cached.evaluate(candidate, budget, requested_metrics)
