"""`flux_search` — the second real CHIA library node (docs/04.md §7.1). Wraps
`flux_search_architecture.run_architecture_dse` (screen → rank → escalate architecture-space DSE)
as a `@ChiaFunction()`, so the whole loop is itself dispatchable as one Ray task, not just a local
Python call — the actual "agentic flow" surface: an orchestrator (or a future LLM-agent search
strategy — `search/agentic/`, not built here, see its README for why) calls `flux_search` once
and gets back a full report, with the parallel screening dispatch happening underneath via
`ChiaParallelEvaluator`, transparently.

Backends are named by string (`"zigzag"`, `"systemc"`, `"rtl"`, ...), not passed as `Evaluator`
instances — same reason `flux_evaluate` takes a `backend: str`: simple, picklable arguments that
survive being shipped to a Ray worker (and, later, an MCP tool schema) cleanly.
"""

from __future__ import annotations

from typing import Any

from chia.base.ChiaFunction import ChiaFunction
from flux_cli.registry import make_evaluator
from flux_search_architecture import ArchitectureDSEReport, run_architecture_dse

from .parallel import ChiaParallelEvaluator


@ChiaFunction()
def flux_search(
    workload: dict[str, Any],
    base_arch: dict[str, Any],
    screening_backend: str,
    widths: list[int],
    metric: str = "latency_cycles",
    minimize: bool = True,
    escalation_backends: list[str] | None = None,
    parallel_screening: bool = True,
) -> ArchitectureDSEReport:
    """Run architecture-space DSE (`search/architecture`'s screen→rank→escalate loop) as a
    single CHIA node call.

    `screening_backend` names the fast evaluator sweeping every width in `widths` (e.g.
    `"zigzag"`). `escalation_backends` names, in order, the rungs the winner is confirmed
    against (e.g. `["systemc", "rtl"]`) — each run once, only on the winner. `parallel_screening`
    (default `True`) uses `ChiaParallelEvaluator` so the width sweep dispatches as real
    concurrent Ray tasks (including when `flux_search` itself is called via `.chia_remote(...)`
    — Ray supports nested remote dispatch natively, verified in
    `tests/integration/test_chia_flux_search_live.py`, not assumed); pass `False` to screen
    sequentially in-process instead (no Ray overhead for a tiny sweep).
    """
    screening_evaluator = (
        ChiaParallelEvaluator(screening_backend) if parallel_screening else make_evaluator(screening_backend)
    )
    escalation_evaluators = [(name, make_evaluator(name)) for name in (escalation_backends or [])]
    return run_architecture_dse(
        workload, base_arch, screening_evaluator,
        widths=widths, metric=metric, minimize=minimize,
        escalation_evaluators=escalation_evaluators,
    )
