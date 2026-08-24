"""`flux_sweep_dynamic_shape` — the CHIA node surface for
`flux_workload_dynamism.sweep_dynamic_shape` (docs/decisions.md D63): a real, honest cost estimate
for a Workload IR op with a declared dynamic bound (KV-cache growth, MoE-adjacent dynamic shapes,
...), built by evaluating several caller-chosen concrete sample points through an existing,
unmodified evaluator backend and aggregating the real per-sample results — not a new cost model,
a real composition of the ones that already exist.

Same `backend` resolution as `flux_evaluate` (`flux_cli.registry.make_evaluator`) — no separate
evaluator-selection mechanism to keep in sync. `result_db_path` (docs/decisions.md D86) opts into
the same real warm-start `flux_evaluate` already gets: a real, immediate opportunity here since
`sample_points` is caller-chosen and evaluated one-by-one with no dedup of its own (`sweep.py`'s
own per-sample loop calls `evaluator.evaluate()` once per entry, duplicates included) — a repeated
sample value within one call, or the same (workload, op_id, dim, value) triple recurring across
calls (e.g. a broader agentic sweep re-probing overlapping sample points against the same
architecture), is now a genuine cache hit, no new dependency-tracking logic beyond `CachingEvaluator`
(D19) itself.

`sample_points` can now be omitted in favor of `n_samples` (docs/decisions.md D87, closing
docs/gap-analysis.md G5's own last-named open piece): if `workload`'s own `dynamism.
distributions[dim]` names a real, ingested distribution (see `flux_workload_dynamism.
distributions` — as of D87, only `"empirical@corpus/kv-cache-len-v1"`, a real, measured ShareGPT
conversation-length distribution), `n_samples` real, evenly-probability-spaced quantile sample
points are drawn from it automatically, clipped to `op_id`'s own declared `{dyn: [lo, hi]}` range
— a real, distribution-aware sweep, not a caller-guessed one.
"""

from __future__ import annotations

from typing import Any

from chia.base.ChiaFunction import ChiaFunction
from flux_cli.registry import make_evaluator
from flux_evaluator_abi import Budget, Result
from flux_store import CachingEvaluator, ResultStore
from flux_workload_dynamism import (
    DynamicShapeError,
    dynamic_bound_range,
    load_empirical_distribution,
    quantile_sample_points,
    sweep_dynamic_shape,
)


def _resolve_sample_points(
    workload: dict[str, Any], op_id: str, dim: str, sample_points: list[int] | None, n_samples: int | None,
    corpus_root: str | None,
) -> list[int]:
    if sample_points is not None:
        return sample_points
    if n_samples is None:
        raise DynamicShapeError(
            "flux_sweep_dynamic_shape requires either sample_points or n_samples — neither was given."
        )
    ref = (workload.get("dynamism") or {}).get("distributions", {}).get(dim)
    if ref is None:
        raise DynamicShapeError(
            f"n_samples was given but workload {workload.get('id')!r} declares no "
            f"dynamism.distributions[{dim!r}] reference to resolve real sample points from — "
            "pass sample_points explicitly instead."
        )
    lo, hi = dynamic_bound_range(workload, op_id, dim)
    distribution = load_empirical_distribution(ref, corpus_root=corpus_root)
    return quantile_sample_points(distribution, n_samples, lo=lo, hi=hi)


@ChiaFunction()
def flux_sweep_dynamic_shape(
    backend: str,
    workload: dict[str, Any],
    op_id: str,
    dim: str,
    sample_points: list[int] | None = None,
    arch: dict[str, Any] | None = None,
    mapping: dict[str, Any] | None = None,
    metric: str = "latency_cycles",
    wall_clock_s: float | None = None,
    usd: float | None = None,
    result_db_path: str | None = None,
    n_samples: int | None = None,
    corpus_root: str | None = None,
) -> Result:
    """Evaluate `workload`'s op `op_id` at every concrete value in `sample_points` (resolving its
    `dim` bound from a declared `{dyn: [lo, hi]}` range to each one in turn) through the named
    evaluator `backend`, and aggregate into one honest `Result`: every metric present in every
    sample gets `Estimate.value` = the uniform mean across samples, `ci_low`/`ci_high` = the real
    observed min/max — an honest report of the real spread across the exact points evaluated, not
    a fabricated confidence interval.

    `result_db_path` opts into warm-start (docs/decisions.md D19/D86): pass the same path across
    calls and a repeated per-sample `(workload, arch, mapping)` triple — whether from a duplicate
    entry in `sample_points` or a call that overlaps a prior one — is served from the store instead
    of spending a real evaluator call per occurrence. Omit it (the default) for the original
    always-real-evaluation behavior — additive, not a behavior change for existing callers.

    `n_samples` (docs/decisions.md D87), given instead of `sample_points`, draws that many real,
    evenly-probability-spaced quantile sample points from `workload`'s own declared
    `dynamism.distributions[dim]` reference (raises `DynamicShapeError` if it names none, or if
    `flux_workload_dynamism.DistributionResolutionError` if it names one that isn't real, ingested
    data) — see `flux_workload_dynamism.distributions`'s own module docstring for why this is real
    quantile sampling, not invented weights. `corpus_root` overrides where ingested distributions
    are looked up (defaults to this repo's own `knowledge/corpus/distributions/`) — mainly for
    tests.

    Raises `flux_workload_dynamism.DynamicShapeError` if `op_id`/`dim` don't name a real dynamic
    bound on `workload`, if neither `sample_points` nor `n_samples` was given, or if
    `sample_points` (explicit or resolved) is empty. Raises whatever the underlying evaluator
    raises for the first sample point that fails.
    """
    resolved_sample_points = _resolve_sample_points(workload, op_id, dim, sample_points, n_samples, corpus_root)
    evaluator = make_evaluator(backend)
    budget = Budget(wall_clock_s=wall_clock_s, usd=usd)
    if result_db_path is None:
        return sweep_dynamic_shape(
            workload, op_id, dim, resolved_sample_points, evaluator,
            arch=arch, mapping=mapping, metric=metric, budget=budget,
        )
    with ResultStore(result_db_path) as store:
        cached = CachingEvaluator(evaluator, store, evaluator_prefix=backend)
        return sweep_dynamic_shape(
            workload, op_id, dim, resolved_sample_points, cached,
            arch=arch, mapping=mapping, metric=metric, budget=budget,
        )
