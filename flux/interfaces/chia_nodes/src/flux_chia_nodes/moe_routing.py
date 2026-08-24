"""`flux_sweep_moe_routing` — the CHIA node surface for
`flux_workload_dynamism.sweep_moe_routing` (docs/decisions.md D68): a real, honest cost estimate
for a Workload IR op with `kind: data_dependent` MoE routing semantics, built by resolving several
caller-chosen concrete routing decisions (which `top_k` of the declared candidate experts actually
ran) to fully static workloads and evaluating each through an existing, unmodified evaluator
backend — not a new cost model, a real composition of the ones that already exist, and the same
real shape `flux_sweep_dynamic_shape` (D63) already established for dynamic bounds.

Same `backend` resolution as `flux_evaluate` (`flux_cli.registry.make_evaluator`) — no separate
evaluator-selection mechanism to keep in sync. `result_db_path` (docs/decisions.md D86) opts into
the same real warm-start `flux_sweep_dynamic_shape` gets — a real, likely *more* valuable case
here than for dynamic shapes: `routing_samples` is exactly the kind of thing a real Monte-Carlo-
style caller draws many times from a small discrete space of `top_k` expert combinations, so
repeats within one call (or across overlapping calls against the same store) are a genuine, common
cache hit, not an edge case.
"""

from __future__ import annotations

from typing import Any

from chia.base.ChiaFunction import ChiaFunction
from flux_cli.registry import make_evaluator
from flux_evaluator_abi import Budget, Result
from flux_store import CachingEvaluator, ResultStore
from flux_workload_dynamism import sweep_moe_routing


@ChiaFunction()
def flux_sweep_moe_routing(
    backend: str,
    workload: dict[str, Any],
    op_id: str,
    routing_samples: list[list[str]],
    arch: dict[str, Any] | None = None,
    mapping: dict[str, Any] | None = None,
    metric: str = "latency_cycles",
    wall_clock_s: float | None = None,
    usd: float | None = None,
    result_db_path: str | None = None,
) -> Result:
    """Evaluate `workload`'s `data_dependent` op `op_id` at every routing decision in
    `routing_samples` (each a list of `top_k` selected expert-op ids, resolved by dropping every
    unselected candidate expert's own op from the workload) through the named evaluator `backend`,
    and aggregate into one honest `Result`: every metric present in every sample gets
    `Estimate.value` = the uniform mean across samples, `ci_low`/`ci_high` = the real observed
    min/max — an honest report of the real spread across the exact routing decisions evaluated,
    not a fabricated confidence interval.

    `result_db_path` opts into warm-start (docs/decisions.md D19/D86): pass the same path across
    calls and a repeated per-sample `(workload, arch, mapping)` triple — whether from a duplicate
    routing decision in `routing_samples` or a call that overlaps a prior one — is served from the
    store instead of spending a real evaluator call per occurrence. Omit it (the default) for the
    original always-real-evaluation behavior — additive, not a behavior change for existing
    callers.

    Raises `flux_workload_dynamism.MoeRoutingError` if `op_id` doesn't name a real
    `data_dependent` op with `semantics.candidate_ops` on `workload`, if a sample isn't a valid
    selection from those candidates, or if `routing_samples` is empty. Raises whatever the
    underlying evaluator raises for the first sample that fails.
    """
    evaluator = make_evaluator(backend)
    budget = Budget(wall_clock_s=wall_clock_s, usd=usd)
    if result_db_path is None:
        return sweep_moe_routing(
            workload, op_id, routing_samples, evaluator,
            arch=arch, mapping=mapping, metric=metric, budget=budget,
        )
    with ResultStore(result_db_path) as store:
        cached = CachingEvaluator(evaluator, store, evaluator_prefix=backend)
        return sweep_moe_routing(
            workload, op_id, routing_samples, cached,
            arch=arch, mapping=mapping, metric=metric, budget=budget,
        )
