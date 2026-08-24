"""Real, honest cost estimation for a Workload IR op with `kind: data_dependent` and MoE-style
routing semantics (docs/gap-analysis.md G5's own explicitly-named remaining gap, docs/decisions.md
D63's own "Not implemented" note, closed here — D68).

`docs/ir.md`'s own flagship reference example (`llama3-8b-decode-layer0.yaml`) has declared a
`moe.route` `data_dependent` op since D1 — real routing decisions (which experts actually run for
a given token) are runtime data, not something any static IR document can carry as a fixed shape.
Every real evaluator in this repo already, correctly, has no translation for `data_dependent` ops
(checked directly, not assumed: `flux_evaluator_zigzag.workload_translator.
workload_to_zigzag_layers` silently *skips* non-einsum ops rather than raising — meaning a raw,
unresolved MoE workload doesn't fail loudly, it silently evaluates as if *every* candidate expert
ran, wildly overstating real per-token cost, a genuinely dangerous silent-wrong-answer trap this
module exists to close).

Mirrors `sweep.py`'s own real pattern exactly: resolve the abstract declaration (here, a routing
decision — which `top_k` of the declared candidate expert ops actually ran) to a concrete, fully
static Workload IR document at each of several caller-chosen sample routings, evaluate each
through a real, **unmodified** evaluator (its own already-proven multi-op aggregation, D59/D62,
handles the "more than one real einsum op in one workload" case with zero further changes needed
here), then aggregate the real per-sample results into one honest `Result` — `ci_low`/`ci_high`
span the real observed spread across the sample routings actually evaluated, not a fabricated
confidence interval.

**Deliberately not weighted by a distribution's own probability mass**, the same reasoning
`sweep.py` already established for dynamic shapes: `semantics.distribution` (e.g.
`"measured@corpus/moe-route-v1"`) is, in every real workload example in this repo, an unresolved
placeholder URI, never backed by real, ingested routing-frequency data — inventing weights from an
unresolved reference would fabricate precision this repo doesn't actually have. `routing_samples`
are caller-chosen, explicit lists of `top_k` expert-op ids, uniformly weighted.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

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

from .sweep import _EvaluatorProtocol


class MoeRoutingError(ValueError):
    """Raised when the requested (op_id, routing sample) doesn't name a real, resolvable MoE
    routing decision on this workload — caught here, before any real evaluator call, the same
    fail-loudly posture `DynamicShapeError` already established for dynamic bounds.
    """


def resolve_moe_routing(workload: dict[str, Any], op_id: str, selected_expert_ids: list[str]) -> dict[str, Any]:
    """Return a new Workload IR document — `workload` itself is never mutated — with the
    `data_dependent` op `op_id` removed, every one of its declared `semantics.candidate_ops` NOT
    in `selected_expert_ids` removed (the real, physical point of sparse MoE routing: an expert
    that isn't selected genuinely isn't computed), and every other op (including any candidate
    expert that *is* selected, and anything not part of this routing decision at all) left
    untouched. Raises `MoeRoutingError` if `op_id` doesn't name a real `data_dependent` op on this
    workload, if `selected_expert_ids` isn't a real subset of that op's own declared
    `candidate_ops`, or if its length doesn't match a declared `semantics.top_k` (when present).
    """
    ops = workload.get("ops", [])
    op = next((o for o in ops if o.get("id") == op_id), None)
    if op is None:
        raise MoeRoutingError(f"workload {workload.get('id')!r} has no op with id={op_id!r}")
    if op.get("kind") != "data_dependent":
        raise MoeRoutingError(f"op {op_id!r} has kind={op.get('kind')!r}, not 'data_dependent'")

    semantics = op.get("semantics", {})
    candidate_ops = semantics.get("candidate_ops")
    if not candidate_ops:
        raise MoeRoutingError(
            f"op {op_id!r} has no semantics.candidate_ops — nothing to route between. See "
            "core/ir/workload/examples/moe-ffn-8experts-top2-v1.yaml for the expected shape."
        )
    candidate_set = set(candidate_ops)
    selected_set = set(selected_expert_ids)
    if not selected_set.issubset(candidate_set):
        raise MoeRoutingError(
            f"op {op_id!r}: selected_expert_ids {sorted(selected_set - candidate_set)!r} are not "
            f"in its own declared candidate_ops {candidate_ops!r}."
        )
    top_k = semantics.get("top_k")
    if top_k is not None and len(selected_expert_ids) != top_k:
        raise MoeRoutingError(
            f"op {op_id!r} declares top_k={top_k}, but {len(selected_expert_ids)} expert(s) "
            f"were selected ({selected_expert_ids!r})."
        )

    dropped = candidate_set - selected_set
    resolved = copy.deepcopy(workload)
    resolved["ops"] = [
        resolved_op for resolved_op in resolved["ops"]
        if resolved_op.get("id") != op_id and resolved_op.get("id") not in dropped
    ]
    return resolved


@dataclass(frozen=True, slots=True)
class RoutingSample:
    selected_expert_ids: tuple[str, ...]
    result: Result


def sweep_moe_routing(
    workload: dict[str, Any],
    op_id: str,
    routing_samples: list[list[str]],
    evaluator: _EvaluatorProtocol,
    *,
    arch: dict[str, Any] | None = None,
    mapping: dict[str, Any] | None = None,
    metric: str,
    budget: Budget | None = None,
) -> Result:
    """Evaluate `workload` at every routing decision in `routing_samples` (each obtained via
    `resolve_moe_routing`), through the real, unmodified `evaluator`, and aggregate into one
    honest `Result`: every metric present in every sample's own `Result` gets `Estimate.value` =
    the uniform mean across samples, `ci_low`/`ci_high` = the real observed min/max — not a
    fabricated interval, the real spread across the exact routing decisions evaluated. `metric`
    picks which metric decides the "representative" sample used for `bottleneck` (the same
    defensible simplification `sweep_dynamic_shape` already uses).

    Raises `MoeRoutingError` if `routing_samples` is empty, or (via `resolve_moe_routing`) if
    `op_id` doesn't name a real MoE routing decision, or a sample isn't a valid selection. Raises
    whatever `evaluator.evaluate()` itself raises for the first sample that fails.
    """
    if not routing_samples:
        raise MoeRoutingError("routing_samples must be non-empty — nothing to sweep")

    budget = budget if budget is not None else Budget()
    samples: list[RoutingSample] = []
    for selected in routing_samples:
        resolved_workload = resolve_moe_routing(workload, op_id, selected)
        candidate = Candidate(workload=resolved_workload, arch=arch, mapping=mapping)
        result = evaluator.evaluate(candidate, budget, frozenset({metric}))
        if result.refusal_for(metric) is not None:
            # The guard `sweep_dynamic_shape` already carries and this sibling never got
            # (docs/decisions.md D169). Evaluators are free to ignore the requested-metrics set —
            # zigzag does, and `evaluators/rtl` returns an empty metrics dict for anything but
            # `latency_cycles` — so this is only knowable from a real result. Without it the
            # sweep evaluated every routing sample and then died on a bare `KeyError: 'energy_pj'`
            # from the `representative` selection below, naming neither the evaluator nor the
            # metrics it actually emits.
            raise MoeRoutingError(
                f"evaluator {result.provenance.evaluator!r} does not emit metric {metric!r}; "
                f"it returned {sorted(result.metrics)} — pick `metric` from that set."
            )
        samples.append(RoutingSample(selected_expert_ids=tuple(selected), result=result))

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
        validity=Validity(ok=validity_ok, checker_version="moe-routing-sweep-v0.1"),
        domain=Domain(in_domain=in_domain),
        bottleneck=representative.result.bottleneck,
        provenance=Provenance(
            evaluator=f"moe-routing-sweep+{representative.result.provenance.evaluator}",
            inputs={
                "op_id": op_id,
                "routing_samples": [list(s) for s in routing_samples],
                "per_sample_workload_hashes": [
                    s.result.provenance.inputs.get("workload_hash") for s in samples
                ],
            },
        ),
        escalation=Escalation(recommended=False),
    )
