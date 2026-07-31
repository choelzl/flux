"""`flux_evaluate` — the first real CHIA library node (docs/04.md §7.1: "Flux ships CHIA library
nodes: `flux_evaluate`, `flux_search`, `flux_calibrate`, `flux_conformance_check`").

Wraps `flows/cli`'s evaluator registry (the same `make_evaluator(backend)` the `flux eval` CLI
command uses) as a real `@ChiaFunction()` — no separate CHIA-specific evaluator implementation,
matching docs/04.md §2's "adapters, not forks": one evaluator, dispatchable locally, from the
CLI, or now as a Ray task, all going through the exact same `Evaluator.evaluate()` call.

This is docs/04.md §7.2's illustrative `evaluate_design` example made real, using the actual
`@ChiaFunction()` decorator (github.com/ucb-bar/chia, not the placeholder `@flux_tool` name that
doc used before real CHIA's API was known). Only one of the "three surfaces" is wired here — the
typed Python function, now also Ray-dispatchable via `.chia_remote()`/`.chia_remote_blocking()`.
The MCP-tool surface is a separate, not-yet-built piece — see `flows/mcp/README.md`.
"""

from __future__ import annotations

from typing import Any

from chia.base.ChiaFunction import ChiaFunction
from flux_cli.registry import DEFAULT_METRICS, make_evaluator
from flux_evaluator_abi import Budget, Candidate, Result


@ChiaFunction()
def flux_evaluate(
    backend: str,
    workload: dict[str, Any],
    arch: dict[str, Any] | None = None,
    mapping: dict[str, Any] | None = None,
    metrics: list[str] | None = None,
    wall_clock_s: float | None = None,
    usd: float | None = None,
) -> Result:
    """Evaluate a candidate accelerator design through a named Flux evaluator backend.

    Returns the same `Result` docs/04.md §4.2 defines: `Estimate`s with confidence intervals, an
    independently-computed `Validity`, a structured `Bottleneck`, and full `Provenance` — not a
    bare number. Call directly for a local (in-process) evaluation, or via
    `flux_evaluate.chia_remote(...)` / `.chia_remote_blocking(...)` to dispatch it as a real Ray
    task (see `tests/integration/test_chia_flux_evaluate_live.py` for both, against a real local
    Ray instance and the real ZigZag backend).
    """
    evaluator = make_evaluator(backend)
    candidate = Candidate(workload=workload, arch=arch, mapping=mapping)
    budget = Budget(wall_clock_s=wall_clock_s, usd=usd)
    requested_metrics = frozenset(metrics) if metrics is not None else DEFAULT_METRICS
    return evaluator.evaluate(candidate, budget, requested_metrics)
