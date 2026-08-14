"""Real, honest cost estimation for a Workload IR op with a declared dynamic bound
(docs/gap-analysis.md G5, docs/decisions.md D63).

Every real evaluator in this repo (evaluators/zigzag, evaluators/timeloop) requires every einsum
op's bounds to be a fixed static integer, raising NotExpressibleError outright the moment it sees
a `{dyn: [lo, hi]}` declaration (docs/ir.md's own dynamic-shape escape hatch) — a genuine, correct
scope limit for each translator's own bilinear-GEMM cost model, not a bug. This module doesn't
change that: it builds ON TOP of it, resolving a dynamic bound to a real, concrete integer at each
of several caller-chosen sample points, evaluating each real, fully-static resulting workload
through an EXISTING, unmodified evaluator, then aggregating the real per-sample results into one
honest `Result` whose `Estimate.ci_low`/`ci_high` genuinely span the real spread across samples —
not a fabricated confidence interval, an honest report of "here is the real range of outcomes at
the sample points you asked about."

**Deliberately not weighted by a distribution's own probability mass — but real ingested
distribution data now exists (docs/decisions.md D87), superseding this module's original "every
distribution reference is a placeholder URI" framing.** `distributions.py` resolves
`empirical@corpus/kv-cache-len-v1` against a real, ingested percentile table (69,601 real
ShareGPT-derived observations), and `quantile_sample_points` derives real quantile-spaced
`sample_points` from it. What remains deliberate: the sweep itself still weights every sample
uniformly — the quantile *placement* already encodes the distribution's mass (equal-probability
bins), so uniform weighting over quantile points is the honest estimator, not a gap.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Protocol

from flux_evaluator_abi import (
    Budget,
    Candidate,
    Domain,
    Escalation,
    Estimate,
    Method,
    Provenance,
    Result,
    Validity,
)


class DynamicShapeError(ValueError):
    """The requested (op_id, dim) doesn't name a real dynamic bound on this workload — caught
    here, before any real evaluator call, so a typo'd op/dim name fails loudly and immediately
    rather than silently evaluating the wrong thing or surfacing as a confusing downstream error.
    """


class _EvaluatorProtocol(Protocol):
    def evaluate(self, candidate: Candidate, budget: Budget, metrics: frozenset[str]) -> Result: ...


def dynamic_bound_range(workload: dict[str, Any], op_id: str, dim: str) -> tuple[int, int]:
    """Return op `op_id`'s own declared `[lo, hi]` range for its `{dyn: [lo, hi]}` bound `dim` —
    the same real lookup+validation `resolve_dynamic_bound` does internally, exposed on its own
    (docs/decisions.md D87) for callers that need the *range* itself, not a resolved value — e.g.
    `flux_workload_dynamism.distributions.quantile_sample_points`'s own `lo`/`hi` clipping.
    Raises `DynamicShapeError` under the same conditions `resolve_dynamic_bound` does (an unknown
    op/dim, or a bound that isn't actually a dynamic declaration).
    """
    ops = workload.get("ops", [])
    op = next((o for o in ops if o.get("id") == op_id), None)
    if op is None:
        raise DynamicShapeError(f"workload {workload.get('id')!r} has no op with id={op_id!r}")
    bounds = op.get("bounds", {})
    if dim not in bounds:
        raise DynamicShapeError(f"op {op_id!r} has no bound named {dim!r}")
    current = bounds[dim]
    if not (isinstance(current, dict) and "dyn" in current):
        raise DynamicShapeError(
            f"op {op_id!r}'s bound {dim!r}={current!r} is not a dynamic ({{'dyn': [lo, hi]}}) "
            "declaration — refusing to silently overwrite an already-static bound."
        )
    # workload.schema.json leaves `bounds` an unconstrained object, so a malformed `dyn` (one
    # element, a bare int, inverted lo>hi) is schema-valid and reached the bare-unpacking
    # ValueError below / silently collapsed quantile sampling to `hi` (review finding) — enforce
    # the real shape here, behind the same typed error the docstring already promises.
    dyn = current["dyn"]
    if not (isinstance(dyn, (list, tuple)) and len(dyn) == 2 and all(isinstance(v, int) for v in dyn)):
        raise DynamicShapeError(
            f"op {op_id!r}'s bound {dim!r} has malformed dyn={dyn!r} — expected exactly "
            "[lo, hi], two integers."
        )
    lo, hi = dyn
    if lo > hi:
        raise DynamicShapeError(
            f"op {op_id!r}'s bound {dim!r} has inverted dyn range [{lo}, {hi}] — lo must be <= hi."
        )
    return lo, hi


def resolve_dynamic_bound(workload: dict[str, Any], op_id: str, dim: str, value: int) -> dict[str, Any]:
    """Return a new Workload IR document — `workload` itself is never mutated — with op `op_id`'s
    `bounds[dim]` replaced by the concrete integer `value`. Every other field, including every
    other op, is untouched. Raises `DynamicShapeError` if `op_id`/`dim` don't name a real op/bound
    on this workload, if that bound isn't actually a `{dyn: [...]}` declaration (resolving an
    already-static bound would silently overwrite a real, intentional fixed value), or if `value`
    falls outside the declared `[lo, hi]` range.
    """
    lo, hi = dynamic_bound_range(workload, op_id, dim)
    if not (lo <= value <= hi):
        raise DynamicShapeError(
            f"value={value} is outside op {op_id!r}'s declared dynamic range for {dim!r}, "
            f"[{lo}, {hi}]"
        )

    resolved = copy.deepcopy(workload)
    for resolved_op in resolved["ops"]:
        if resolved_op.get("id") == op_id:
            resolved_op["bounds"][dim] = value
    return resolved


@dataclass(frozen=True, slots=True)
class SamplePoint:
    value: int
    result: Result


def sweep_dynamic_shape(
    workload: dict[str, Any],
    op_id: str,
    dim: str,
    sample_points: list[int],
    evaluator: _EvaluatorProtocol,
    *,
    arch: dict[str, Any] | None = None,
    mapping: dict[str, Any] | None = None,
    metric: str,
    budget: Budget | None = None,
) -> Result:
    """Evaluate `workload` at every concrete value in `sample_points` (each obtained via
    `resolve_dynamic_bound`), through the real, unmodified `evaluator`, and aggregate into one
    honest `Result`: every metric present in every sample's own `Result` gets `Estimate.value` =
    the uniform mean across samples, `ci_low`/`ci_high` = the real observed min/max — not a
    fabricated interval, the real spread across the exact points evaluated. `metric` picks which
    metric decides the "representative" sample used for `bottleneck` (bottleneck isn't a quantity
    that meaningfully averages across samples, so the sample closest to the aggregate mean stands
    in for it — a defensible simplification, not a synthesized new value).

    Raises `DynamicShapeError` if `sample_points` is empty, or (via `resolve_dynamic_bound`) if
    `op_id`/`dim` don't name a real dynamic bound. Raises whatever `evaluator.evaluate()` itself
    raises for the first sample that fails — a real per-sample failure is not silently dropped
    from the sweep.
    """
    if not sample_points:
        raise DynamicShapeError("sample_points must be non-empty — nothing to sweep")

    budget = budget if budget is not None else Budget()
    samples: list[SamplePoint] = []
    # Repeated sample points are normal, not pathological: `quantile_sample_points` clips into a
    # workload's declared bound, so every quantile above `hi` collapses onto it — measured, 7 of 8
    # points land on the same value for a small `hi` (docs/decisions.md D194). Each duplicate is
    # real probability weight and must stay in `samples` for the aggregate to be correct, but
    # re-evaluating an identical resolved workload buys nothing, and this module exists to drive
    # expensive evaluators. Memoised per value; the sweep's observable output is unchanged.
    evaluated: dict[int, Result] = {}
    for value in sample_points:
        if value in evaluated:
            samples.append(SamplePoint(value=value, result=evaluated[value]))
            continue
        resolved_workload = resolve_dynamic_bound(workload, op_id, dim, value)
        candidate = Candidate(workload=resolved_workload, arch=arch, mapping=mapping)
        result = evaluator.evaluate(candidate, budget, frozenset({metric}))
        if result.refusal_for(metric) is not None:
            # Fail after ONE real evaluation with a clear typed error, not with a raw KeyError
            # after all N samples have already been evaluated (review finding) — evaluators are
            # free to ignore the requested-metrics set (zigzag does), so this is only knowable
            # from a real result.
            raise DynamicShapeError(
                f"evaluator {result.provenance.evaluator!r} does not emit metric {metric!r}; "
                f"it returned {sorted(result.metrics)} — pick `metric` from that set."
            )
        evaluated[value] = result
        samples.append(SamplePoint(value=value, result=result))

    metrics_present = set(samples[0].result.metrics)
    for s in samples[1:]:
        metrics_present &= set(s.result.metrics)

    aggregated_metrics: dict[str, Estimate] = {}
    for metric_name in metrics_present:
        estimates = [s.result.estimate_of(metric_name) for s in samples]
        values = [e.value for e in estimates]
        methods = {e.method for e in estimates}
        units = {e.unit for e in estimates}
        mean_value = sum(values) / len(values)
        aggregated_metrics[metric_name] = Estimate(
            value=mean_value,
            ci_low=min(values),
            ci_high=max(values),
            unit=units.pop() if len(units) == 1 else "mixed",
            method=methods.pop() if len(methods) == 1 else Method.ANALYTIC,
        )

    in_domain = all(s.result.domain.in_domain for s in samples)
    validity_ok = all(s.result.validity.ok for s in samples)
    representative = min(
        samples, key=lambda s: abs(s.result.value_of(metric) - aggregated_metrics[metric].value)
    )

    return Result(
        metrics=aggregated_metrics,
        validity=Validity(ok=validity_ok, checker_version="dynamic-shape-sweep-v0.1"),
        domain=Domain(in_domain=in_domain),
        bottleneck=representative.result.bottleneck,
        provenance=Provenance(
            evaluator=f"dynamic-shape-sweep+{representative.result.provenance.evaluator}",
            inputs={
                "op_id": op_id,
                "dim": dim,
                "sample_points": sample_points,
                "per_sample_workload_hashes": [
                    s.result.provenance.inputs.get("workload_hash") for s in samples
                ],
            },
        ),
        escalation=Escalation(recommended=False),
    )
