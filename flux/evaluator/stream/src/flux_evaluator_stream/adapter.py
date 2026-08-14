"""Stream (multi-core/layer-fused DSE, KU Leuven MICAS) backend adapter implementing the Flux
Evaluator ABI (docs/evaluator-abi.md, docs/decisions.md D80–D82).

Composes three already-independently-verified real pieces, each proven standalone before being
wired together here: `frontends/onnx`'s `workload_ir_to_onnx_model` (D81, workload translation),
this package's own `architecture_ir_to_stream_hardware_yaml` (D82, real multi-core hardware
translation reusing `evaluators/zigzag`'s own existing per-core translator), and real
`stream.api.optimize_allocation_co_generic` itself (D80, the real, pinned nix-provided package —
`backend="ortools_highs"`, not Stream's own default `"ortools_gscip"`, since this repo's pinned
`ortools` wheel has no GSCIP solver registered at all, confirmed directly in D80).

v0.1 scope: `Candidate.workload` must be an inline Workload IR dict expressible by
`workload_ir_to_onnx_model` (a chained sequence of 2D-GEMM `einsum` ops); `Candidate.arch` must
be an inline Architecture IR dict with a real `interconnect.multi_core` block; `Candidate.mapping`
is `None` for Stream's own automatic allocation+mapping search, OR a fusion-only Mapping IR
document (docs/decisions.md D103): the mapping's `fusion` block — in the IR schema since day one,
first consumed here — translates to Stream's real `intra_core_tiling` layer-fusion parameter.
Anything else in a mapping document is loudly rejected, never silently ignored (see
`fusion_translator.py` for the exact contract and the empirically-pinned facts it stands on).

**Real bottleneck reporting (docs/decisions.md D84)**: Stream's own real
`StageContext.data["group_allocations"][group_id]["performance"]` carries a genuine, structured
bottleneck breakdown — confirmed by direct inspection of a real run before this was trusted, not
assumed from any doc — `bottleneck.{compute_bound_cycles, transfer_bound_cycles,
compute_bound_pct, transfer_bound_pct}` and `aggregate.{compute_cores_available,
compute_cores_used, latency_weighted_mac_spatial_utilization}`. No `energy`/`power`/`area` key
exists anywhere in that structure (checked directly, not assumed absent) — v0.1 stays
`latency_cycles`-only for real metrics, same as before, but the *why* behind that latency number
is now real, not a placeholder `Limiter.DEPENDENCY` with no supporting data.
"""

from __future__ import annotations

import tempfile
import threading
from pathlib import Path
from typing import Any

import flux_ir
from flux_evaluator_abi import (
    Bottleneck,
    Budget,
    Candidate,
    Domain,
    Escalation,
    Estimate,
    Limiter,
    Method,
    Provenance,
    Result,
    Validity,
)
from flux_frontend_onnx import workload_ir_to_onnx_model

from .architecture_translator import architecture_ir_to_stream_hardware_yaml
from .errors import NotExpressibleError
from .fusion_translator import mapping_fusion_to_intra_core_tiling, sanitize_node_name

_BACKEND = "ortools_highs"  # see module docstring — Stream's own default has no solver registered


def _bottleneck_from_group_allocations(group_allocations: dict[Any, Any]) -> Bottleneck:
    """Real bottleneck reporting from Stream's own `performance` breakdown (docs/decisions.md
    D84) — v0.1 always has exactly one real group (no layer fusion enabled, so Stream itself
    never splits a workload into more than one fusion group here), but this aggregates correctly
    across however many real groups exist rather than assuming one: cycle counts sum (real,
    sequential cost across groups), percentages are recomputed from the summed cycles (not
    averaged — averaging percentages from differently-sized groups would be a real, silent
    wrong answer), and core-count/utilization figures take the real min/max across groups since
    they describe hardware facts, not additive costs.
    """
    total_compute_cycles = 0.0
    total_transfer_cycles = 0.0
    cores_available = 0
    cores_used = 0
    utilizations: list[float] = []

    for group in group_allocations.values():
        performance = group.get("performance", {})
        bottleneck = performance.get("bottleneck", {})
        aggregate = performance.get("aggregate", {})
        total_compute_cycles += bottleneck.get("compute_bound_cycles", 0.0)
        total_transfer_cycles += bottleneck.get("transfer_bound_cycles", 0.0)
        cores_available = max(cores_available, aggregate.get("compute_cores_available", 0))
        cores_used = max(cores_used, aggregate.get("compute_cores_used", 0))
        if "latency_weighted_mac_spatial_utilization" in aggregate:
            utilizations.append(aggregate["latency_weighted_mac_spatial_utilization"])

    total_cycles = total_compute_cycles + total_transfer_cycles
    if total_cycles <= 0:
        # No real per-group performance breakdown was reported at all — a real, honest "no
        # data" case (e.g. group_allocations was empty), not silently faked as compute-bound.
        return Bottleneck(limiter=Limiter.DEPENDENCY)

    compute_pct = 100.0 * total_compute_cycles / total_cycles
    transfer_pct = 100.0 * total_transfer_cycles / total_cycles
    # Real Stream terminology: "transfer" here means real inter-core/off-chip data movement —
    # NoC territory (this repo's own Limiter vocabulary), not a generic "memory" stall inside one
    # core's own local hierarchy (CoreCostEstimationStage's own, separate concern).
    limiter = Limiter.COMPUTE if compute_pct >= transfer_pct else Limiter.NOC

    per_level_utilisation = {
        "compute_bound_cycles": total_compute_cycles,
        "transfer_bound_cycles": total_transfer_cycles,
        "compute_bound_pct": compute_pct,
        "transfer_bound_pct": transfer_pct,
        "compute_cores_available": float(cores_available),
        "compute_cores_used": float(cores_used),
    }
    if utilizations:
        per_level_utilisation["mac_spatial_utilization"] = min(utilizations)

    return Bottleneck(limiter=limiter, per_level_utilisation=per_level_utilisation)


class StreamEvaluator:
    """Runs real Stream end to end against a translated Workload/Architecture IR pair — the real
    multi-core/layer-fusion evaluator this repo's own Phase 5 roadmap item named.
    """

    name = "stream"

    def __init__(self, *, timeout_s: float = 300.0) -> None:
        self.timeout_s = timeout_s
        self._lock = threading.Lock()

    def evaluate(self, candidate: Candidate, budget: Budget, metrics: frozenset[str]) -> Result:
        if not isinstance(candidate.workload, dict):
            raise NotExpressibleError(
                "StreamEvaluator v0.1 requires an inline Workload IR dict as Candidate.workload "
                "(no result-store hash resolution yet)."
            )
        if not isinstance(candidate.arch, dict):
            raise NotExpressibleError(
                "StreamEvaluator v0.1 requires an inline Architecture IR dict as Candidate.arch "
                "with a real interconnect.multi_core block."
            )
        intra_core_tiling: list[dict[str, Any]] | None = None
        if candidate.mapping is not None:
            if not isinstance(candidate.mapping, dict):
                raise NotExpressibleError(
                    "StreamEvaluator requires Candidate.mapping as None or an inline Mapping IR "
                    "dict carrying a `fusion` block (docs/decisions.md D103)."
                )
            # The one mapping concern Stream has a real translation target for: layer fusion
            # (the Mapping IR's own `fusion` block → Stream's intra_core_tiling, D103). Anything
            # else in the document is loudly rejected inside the translator, never ignored.
            intra_core_tiling = mapping_fusion_to_intra_core_tiling(candidate.mapping, candidate.workload)

        # Real translation, both directions independently verified before this adapter existed
        # (D81 for the workload direction, D82 for this architecture direction) — composed here,
        # not re-derived.
        try:
            model = workload_ir_to_onnx_model(candidate.workload)
        except ValueError as exc:  # frontends/onnx's own NotExpressibleError is a ValueError
            raise NotExpressibleError(str(exc)) from exc

        # Sanitize node names dot→underscore before Stream parses the model: Stream's
        # intra_core_tiling filter splits entry dims on the FIRST dot, so a dotted node name
        # (Flux op ids are conventionally dotted) can never match a tiling entry — verified
        # empirically, entries against dotted names are silently dropped (D103). Node names are
        # ONNX metadata (graph edges are value names), so this changes no semantics; it does
        # change the names Stream's own diagnostic output shows, documented in the README.
        sanitized = [sanitize_node_name(n.name) for n in model.graph.node]
        if len(set(sanitized)) != len(sanitized):
            raise NotExpressibleError(
                f"op ids collide after dot→underscore sanitization ({sorted(sanitized)}) — "
                "rename the ops; Stream's tiling filter cannot address dotted node names."
            )
        for node in model.graph.node:
            node.name = sanitize_node_name(node.name)

        workload_hash = flux_ir.content_hash(candidate.workload)
        arch_hash = flux_ir.content_hash(candidate.arch)

        with tempfile.TemporaryDirectory(prefix=f"flux-stream-{workload_hash[:12]}-") as tmp:
            work = Path(tmp)
            hw_dir = work / "hw"
            hw_dir.mkdir()
            hardware_path = architecture_ir_to_stream_hardware_yaml(candidate.arch, hw_dir)

            import onnx

            onnx_path = work / "workload.onnx"
            onnx.save(model, str(onnx_path))

            total_latency, bottleneck = self._run_stream(
                hardware_path, onnx_path, work, workload_hash, intra_core_tiling,
            )

        result_metrics: dict[str, Estimate] = {}
        if not metrics or "latency_cycles" in metrics:
            result_metrics["latency_cycles"] = Estimate(
                value=total_latency, ci_low=total_latency, ci_high=total_latency,
                unit="cycles", method=Method.ANALYTIC,
            )

        return Result(
            metrics=result_metrics,
            validity=Validity(ok=True, checker_version="none-v0.1"),
            domain=Domain(in_domain=True),
            bottleneck=bottleneck,
            provenance=Provenance(
                evaluator="stream-dse@real",
                inputs={"workload_hash": workload_hash, "arch_hash": arch_hash, "backend": _BACKEND},
            ),
            escalation=Escalation(recommended=False),
        )

    def evaluate_batch(
        self, candidates: list[Candidate], budget: Budget, metrics: frozenset[str]
    ) -> list[Result]:
        return [self.evaluate(c, budget, metrics) for c in candidates]

    def _run_stream(
        self, hardware_path: Path, onnx_path: Path, work_dir: Path, workload_hash: str,
        intra_core_tiling: list[dict[str, Any]] | None = None,
    ) -> tuple[float, Bottleneck]:
        # Real Stream's own module-level state (its own logging configuration, some internal
        # caches) is not proven thread-safe for concurrent calls — serialize real Stream
        # invocations the same cautious way every other adapter here that wraps a real,
        # not-explicitly-reentrant external Python API does.
        with self._lock:
            from stream.api import optimize_allocation_co_generic

            ctx = optimize_allocation_co_generic(
                hardware=str(hardware_path),
                workload=str(onnx_path),
                experiment_id=f"flux-{workload_hash[:12]}",
                output_path=str(work_dir / "outputs"),
                backend=_BACKEND,
                intra_core_tiling=intra_core_tiling,
            )
        total_latency = ctx.get("total_latency")
        if total_latency is None:
            raise NotExpressibleError(
                "real Stream run completed but reported no total_latency — check "
                f"{work_dir / 'outputs'} for its own real diagnostic output."
            )
        return float(total_latency), _bottleneck_from_group_allocations(ctx.get("group_allocations") or {})
