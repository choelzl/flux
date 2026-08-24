"""`flux_search` — the second real CHIA library node (docs/agent-surface.md). Wraps
`flux_search_architecture.run_architecture_dse` (screen → rank → escalate architecture-space DSE)
as a `@ChiaFunction()`, so the whole loop is itself dispatchable as one Ray task, not just a local
Python call — the actual "agentic flow" surface: an orchestrator (or a future LLM-agent search
strategy — `search/agentic/`, not built here, see its README for why) calls `flux_search` once
and gets back a full report, with the parallel screening dispatch happening underneath via
`ChiaParallelEvaluator`, transparently.

One CHIA node, four DSE axes (docs/decisions.md D6/D26): `search_kind="architecture_width"`
sweeps a compute array's spatial width (`candidates.generate_width_candidates`); `search_kind=
"noc_topology"` sweeps a NoC's topology/dimensionality (`noc_candidates.generate_noc_topology_
candidates`) — e.g. comparing a 2D mesh against a 3D mesh at equal node count; `search_kind=
"memory_size"` sweeps one named memory-class hierarchy level's capacity
(`memory_candidates.generate_memory_size_candidates`) — e.g. finding the smallest buffer that
still fits a workload's working set, the real minimum-energy point per D26; `search_kind="joint"`
sweeps the width x memory-size Cartesian product together
(`memory_candidates.generate_joint_candidates`) — genuine multi-parameter architecture DSE, not
two single-axis sweeps combined after the fact. All four go through the exact same
`run_architecture_dse` engine underneath (screen → rank → escalate), which is candidate-axis-
agnostic by design — see `flux_search_architecture.dse`'s module docstring. This is the "unified
flow" connecting compute DSE, memory DSE, and NoC DSE into one CHIA-orchestrated surface, rather
than disconnected islands.

Backends are named by string (`"zigzag"`, `"systemc"`, `"rtl"`, `"booksim"`, ...), not passed as
`Evaluator` instances — same reason `flux_evaluate` takes a `backend: str`: simple, picklable
arguments that survive being shipped to a Ray worker (and, later, an MCP tool schema) cleanly.
"""

from __future__ import annotations

from typing import Any

from chia.base.ChiaFunction import ChiaFunction
from flux_cli.registry import make_evaluator
from flux_search_architecture import (
    ArchitectureDSEReport,
    generate_fusion_tile_candidates,
    generate_joint_candidates,
    generate_memory_size_candidates,
    generate_noc_topology_candidates,
    generate_width_candidates,
    run_architecture_dse,
)
from flux_store import CachingEvaluator, ResultStore

from .parallel import ChiaParallelEvaluator

_SEARCH_KINDS = ("architecture_width", "noc_topology", "memory_size", "joint", "fusion_tile")


@ChiaFunction()
def flux_search(
    workload: dict[str, Any],
    base_arch: dict[str, Any],
    screening_backend: str,
    *,
    search_kind: str = "architecture_width",
    widths: list[int] | None = None,
    noc_topology_variants: list[tuple[str, list[int]]] | None = None,
    memory_level: str | None = None,
    memory_sizes_kb: list[float] | None = None,
    tile_sizes: list[int] | None = None,
    escalate_contenders: bool = False,
    metric: str = "latency_cycles",
    minimize: bool = True,
    escalation_backends: list[str] | None = None,
    parallel_screening: bool = True,
    result_db_path: str | None = None,
    wall_clock_budget_s: float | None = None,
) -> ArchitectureDSEReport:
    """Run architecture-space DSE (`search/architecture`'s screen→rank→escalate loop) as a
    single CHIA node call, over any of four DSE axes (see module docstring).

    `search_kind="architecture_width"` (default) requires `widths` (e.g. `[4, 8, 16]`), sweeping
    a compute array's spatial width. `search_kind="noc_topology"` requires
    `noc_topology_variants` (e.g. `[("mesh", [8, 8]), ("mesh", [4, 4, 4])]`), sweeping a NoC's
    topology/dimensionality — the real 2D-vs-3D comparison docs/decisions.md D6 exists for.
    `search_kind="memory_size"` requires `memory_level` (e.g. `"gbuf"`) and `memory_sizes_kb`
    (e.g. `[1.25, 2, 4, 64, 512]`), sweeping that named memory-class hierarchy level's capacity —
    finds the real minimum-energy point (the smallest size that still fits the workload, per
    docs/decisions.md D26), not the largest. `search_kind="joint"` requires `widths`,
    `memory_level`, and `memory_sizes_kb` together, sweeping their full Cartesian product — real
    multi-parameter architecture DSE, both axes varied together.

    `screening_backend` names the fast evaluator sweeping every candidate (e.g. `"zigzag"` for
    architecture_width/memory_size/joint, `"booksim"` for noc_topology). `escalation_backends`
    names, in order, the rungs the winner is confirmed against (e.g. `["systemc", "rtl"]`) — each
    run once, only on the winner. `parallel_screening` (default `True`) uses
    `ChiaParallelEvaluator` so the sweep dispatches as real concurrent Ray tasks (including when
    `flux_search` itself is called via `.chia_remote(...)` — Ray supports nested remote dispatch
    natively, verified in `tests/integration/test_chia_flux_search_live.py`, not assumed); pass
    `False` to screen sequentially in-process instead (no Ray overhead for a tiny sweep).

    `result_db_path` opts into warm-start (docs/decisions.md D19) for both screening and
    escalation: each backend's evaluator is wrapped in `flux_store.CachingEvaluator` against the
    same store, keyed by its own backend name, so a repeated sweep (or a repeated winner) skips
    candidates it's already scored. Composes with `parallel_screening=True` unchanged — cache
    misses still dispatch over Ray, only cache hits are skipped. Omit it (the default) for the
    original always-real-evaluation behavior.

    `wall_clock_budget_s` (docs/decisions.md D71) is a real, enforced stopping condition for the
    *escalation* cascade only — screening's own batched/parallel dispatch isn't interruptible the
    same way (see `search/architecture`'s own module docstring for why). Checked against real,
    measured elapsed time before each escalation rung's own evaluator call; `report.stopped_early`
    is `True` when the budget cut escalation short (the winner and its screening result are always
    complete either way — only later rungs may be missing).
    """
    if search_kind not in _SEARCH_KINDS:
        raise ValueError(f"search_kind={search_kind!r} must be one of {_SEARCH_KINDS}")

    if search_kind == "architecture_width":
        if widths is None:
            raise ValueError("search_kind='architecture_width' requires widths")
        candidates = generate_width_candidates(base_arch, widths)
    elif search_kind == "noc_topology":
        if noc_topology_variants is None:
            raise ValueError("search_kind='noc_topology' requires noc_topology_variants")
        candidates = generate_noc_topology_candidates(base_arch, noc_topology_variants)
    elif search_kind == "memory_size":
        if memory_level is None or memory_sizes_kb is None:
            raise ValueError("search_kind='memory_size' requires memory_level and memory_sizes_kb")
        candidates = generate_memory_size_candidates(base_arch, memory_level, memory_sizes_kb)
    elif search_kind == "joint":
        if widths is None or memory_level is None or memory_sizes_kb is None:
            raise ValueError("search_kind='joint' requires widths, memory_level, and memory_sizes_kb")
        candidates = generate_joint_candidates(base_arch, widths, memory_level, memory_sizes_kb)
    else:
        # The one *mapping*-space axis (docs/decisions.md D104): the architecture is held fixed
        # and a fusion-only Mapping IR document's tile size varies. `tile_sizes=None` sweeps the
        # complete feasible space (every divisor of the shared row dim's bound).
        candidates = generate_fusion_tile_candidates(workload, base_arch, tile_sizes=tile_sizes)

    def _make_screening_evaluator(store: ResultStore | None):
        evaluator = (
            ChiaParallelEvaluator(screening_backend) if parallel_screening
            else make_evaluator(screening_backend)
        )
        if store is None:
            return evaluator
        return CachingEvaluator(evaluator, store, evaluator_prefix=screening_backend)

    def _make_escalation_evaluators(store: ResultStore | None):
        pairs = []
        for name in escalation_backends or []:
            evaluator = make_evaluator(name)
            if store is not None:
                evaluator = CachingEvaluator(evaluator, store, evaluator_prefix=name)
            pairs.append((name, evaluator))
        return pairs

    if result_db_path is None:
        return run_architecture_dse(
            workload, candidates, _make_screening_evaluator(None),
            metric=metric, minimize=minimize, escalation_evaluators=_make_escalation_evaluators(None),
            wall_clock_budget_s=wall_clock_budget_s, escalate_contenders=escalate_contenders,
        )

    with ResultStore(result_db_path) as store:
        return run_architecture_dse(
            workload, candidates, _make_screening_evaluator(store),
            metric=metric, minimize=minimize,
            escalation_evaluators=_make_escalation_evaluators(store),
            wall_clock_budget_s=wall_clock_budget_s, escalate_contenders=escalate_contenders,
        )
