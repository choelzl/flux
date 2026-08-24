"""`ComposedEvaluator` — evaluate a per-op engine assignment by running the REAL inner evaluator
once per component and composing the results (docs/decisions.md D236).

This is composition as plain code, not a new estimator: every number still comes from an
existing evaluator (`zigzag`, `rtl`, `openroad`, ...) called on a single-op slice of the
workload with that op's own engine architecture. What this class owns is only the composition
semantics, which follow from the engine-per-op physical model and are stated per metric:

- `latency_cycles`, `energy_pj`: SUM — the chain runs sequentially, each op once on its engine.
- `area_mm2`: SUM — the engines are separate silicon.
- everything else (`power_w`, `edp`, ...): OMITTED — average power across a time-multiplexed
  chain is not the sum of per-engine powers, and a metric this module cannot compose honestly is
  refused via the ABI's standard absence semantics, never approximated.

A notable consequence: this is the first path that can escalate a MULTI-op workload through
`evaluators/rtl` at all — that adapter refuses workloads with more than one einsum op by
design, and the per-op slicing here is exactly the decomposition it asks its caller to do.

Uncertainty composes conservatively: the sum's CI is the sum of the per-op CIs. Method is the
weakest across components (a chain measured everywhere but one analytic op is analytic-grade).
Validity requires every component valid; domain is the worst across components.
"""

from __future__ import annotations

from typing import Any

from flux_evaluator_abi import (
    Bottleneck,
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

_SUMMABLE = ("latency_cycles", "energy_pj", "area_mm2")
_METHOD_STRENGTH = {Method.ANALYTIC: 0, Method.SIMULATED: 1, Method.MEASURED: 2}


class NotACompositionDocument(ValueError):
    """`Candidate.arch` is not an engine_per_op composition document."""


def slice_workload(workload: dict[str, Any], op_id: str) -> dict[str, Any]:
    """A valid single-op Workload IR document for one op of `workload` — id derived from the
    parent's so provenance reads as what it is, tensors kept whole (evaluator translators read
    op bounds/precision; an unreferenced tensor entry is inert)."""
    ops = [op for op in workload.get("ops", []) if op.get("id") == op_id]
    if len(ops) != 1:
        raise NotACompositionDocument(
            f"workload {workload.get('id')!r} has {len(ops)} ops with id {op_id!r}"
        )
    sliced = dict(workload)
    sliced["id"] = f"{workload.get('id', 'workload')}/op/{op_id}"
    sliced["ops"] = ops
    return sliced


class MemoryLevelAreaRung:
    """Adapts a single-macro memory characterizer (CACTI) into a composition rung
    (docs/decisions.md D252). Two transformations, both load-bearing:

    1. **Extraction**: the cacti adapter deliberately refuses an arch with more than one
       memory node (it characterizes ONE physical macro). Each engine arch carries a full
       hierarchy, so this wrapper hands the inner evaluator a copy containing only the
       SEARCHED level (plus `tech`, which sets the technology node).
    2. **Metric narrowing to `area_mm2`**: CACTI's energy_pj is PER-ACCESS energy and its
       power_w is leakage — real numbers, but under the same names the objective uses for
       WORKLOAD energy/power. Passing them through would let the composite frontier prefer
       cacti's per-access energy over zigzag's workload energy (deeper rung wins per metric)
       — a silently wrong frontier, the exact class this repo's adapters refuse to produce.
       This rung answers the one question it is mounted for: the searched level's silicon
       area, summed across engines by the composed evaluator like any other area.
    """

    def __init__(self, inner: Any, *, level: str, scale_from_nm: int | None = None,
                 efficiency_probe: Any = None) -> None:
        self._inner = inner
        self._level = level
        # None -> the real CACTI DETAILED-output probe; a callable -> injected (tests, the
        # same injectability pattern as make_evaluator/llm everywhere else); False -> off.
        self._efficiency_probe = efficiency_probe
        # D253: when the arch declares a node below CACTI's 22nm floor (e.g. n7/ASAP7-class),
        # characterize at this published node instead and scale the area by the vendored
        # Stillmaker & Baas 2017 factor — explicit opt-in, never automatic, and every scaled
        # result carries the citation in its provenance.
        self._scale_from_nm = scale_from_nm

    def evaluate(
        self, candidate: Candidate, budget: Budget, metrics: frozenset[str]
    ) -> Result:
        import dataclasses
        import re

        arch = candidate.arch
        node = next(
            (n for n in arch.get("hierarchy", ())
             if n.get("class") == "memory" and n.get("level") == self._level), None)
        if node is None:
            raise NotACompositionDocument(
                f"engine arch {arch.get('id')!r} has no memory level {self._level!r} to "
                "characterize"
            )
        extracted = {k: v for k, v in arch.items() if k != "hierarchy"}
        extracted["hierarchy"] = [node]
        extracted["id"] = f"{arch.get('id', 'arch')}/{self._level}-only"

        declared_nm: int | None = None
        if self._scale_from_nm is not None:
            m = re.match(r"n(\d+)$", str(arch.get("tech", {}).get("node", "")))
            declared_nm = int(m.group(1)) if m else None
            if declared_nm is not None and declared_nm != self._scale_from_nm:
                extracted["tech"] = dict(extracted.get("tech", {}), node=f"n{self._scale_from_nm}")

        result = self._inner.evaluate(
            Candidate(workload=candidate.workload, arch=extracted, mapping=candidate.mapping),
            budget, frozenset({"area_mm2"}),
        )
        if (self._scale_from_nm is None or declared_nm is None
                or declared_nm == self._scale_from_nm):
            return result
        from flux_evaluator_cacti.scaling import scale_area_mm2

        est = result.metrics.get("area_mm2")
        if est is None:
            return result
        attrs = node.get("attrs", {})
        bits = None
        if attrs.get("size_kb"):
            bits = int(attrs["size_kb"] * 1024 * 8)
        # CACTI's own array efficiency at the native node refines the bitcell estimate
        # (D256); a failed efficiency probe degrades to the plain floor, never blocks.
        efficiency = None
        if bits is not None and self._efficiency_probe is not False:
            try:
                if callable(self._efficiency_probe):
                    efficiency = self._efficiency_probe(extracted, self._scale_from_nm)
                else:
                    from flux_evaluator_cacti.adapter import CactiEvaluator
                    from flux_evaluator_cacti.scaling import measure_area_efficiency

                    binary = CactiEvaluator()._ensure_cacti_binary()
                    efficiency = measure_area_efficiency(
                        extracted, self._scale_from_nm / 1000.0, cacti_path=str(binary))
            except Exception:  # noqa: BLE001 — refinement is optional, the floor is not
                efficiency = None
        scaled, note = scale_area_mm2(est.value, from_nm=self._scale_from_nm,
                                      to_nm=declared_nm, bits=bits,
                                      array_efficiency=efficiency)
        # bounds through the SAME division as the value — a ratio-multiply reorders the float
        # ops and can push value a ULP outside its own interval (the floor applies to them
        # identically). With the efficiency refinement (D256) the honest interval is
        # [bitcell floor, refined estimate]: the floor is a physical bound, the refined
        # figure the best estimate, and no tighter upper bound is established — min/max keep
        # the value inside whatever combination ran.
        low, _ = scale_area_mm2(est.ci_low, from_nm=self._scale_from_nm,
                                to_nm=declared_nm, bits=bits)
        high, _ = scale_area_mm2(est.ci_high, from_nm=self._scale_from_nm,
                                 to_nm=declared_nm, bits=bits)
        if efficiency is not None and bits is not None:
            from flux_evaluator_cacti.scaling import bitcell_floor_mm2

            floor = bitcell_floor_mm2(bits, declared_nm)
            if floor is not None:
                low = floor  # the physical bound anchors the interval regardless of models
        low, high = min(low, scaled), max(high, scaled)
        new_est = dataclasses.replace(
            est, value=scaled, ci_low=low, ci_high=high,
            method=Method.ANALYTIC,  # a scaled simulation is a model estimate, not a sim
        )
        return dataclasses.replace(
            result,
            metrics={**{k: v for k, v in result.metrics.items() if k != "area_mm2"},
                     "area_mm2": new_est},
            provenance=dataclasses.replace(
                result.provenance,
                evaluator=f"{result.provenance.evaluator}+scaled",
                inputs={**result.provenance.inputs, "area_scaling": note},
            ),
        )


class ComposedEvaluator:
    """Wraps any single-arch evaluator into an engine-per-op composition evaluator. Duck-types
    the Evaluator ABI's `evaluate(candidate, budget, metrics) -> Result` surface, so the
    campaign runner (and its caching probe) treat it exactly like the inner backend.

    `calibration_db_path` applies the D98/D234 flywheel PER COMPONENT (docs/decisions.md D237):
    each engine's estimate is corrected against its own (workload-slice, engine-arch) residual
    record before the sum — the composed-level hash matches no residual pool by construction,
    so component granularity is the only place calibration can reach a composition. The
    composed CI is then the sum of per-component calibrated CIs: components the pool has
    measured contribute tight intervals, extrapolated ones contribute honestly wide intervals,
    and the CI-aware contender set grows exactly where the composition's evidence is thin.
    Calibration happens AFTER the inner call, so an inner wrapped in `CachingEvaluator` serves
    raw cached rows that still get today's residual pool — the same freshness rule the runner
    applies to plain campaigns."""

    def __init__(self, inner: Any, *, calibration_db_path: str | None = None) -> None:
        self._inner = inner
        self._calibration_db_path = calibration_db_path
        # Request-keyed memo of RAW component results (docs/decisions.md D237): assignments
        # share engines, and one evaluator instance serves a whole rung sweep, so an identical
        # (slice, engine, metrics) request is answered once per instance. This is what the
        # store-backed CachingEvaluator cannot do at a partial rung: its covering rule demands
        # every REQUESTED metric in the stored row, and a rung result that legally omits a
        # metric (openroad stores area+power, the rung request names latency too) never
        # qualifies — measured as 8 placements where 4 engines existed. Keyed by the request
        # set, so it never serves a row a different request shape could read more into; raw,
        # so calibration still happens per serving with today's pool.
        self._memo: dict[tuple[str, str, str | None, frozenset[str]], Result] = {}

    def _component_result(
        self, sliced: dict[str, Any], engine_arch: dict[str, Any],
        budget: Budget, metrics: frozenset[str], mapping: Any = None,
    ) -> Result:
        import flux_ir

        key = (flux_ir.content_hash(sliced), flux_ir.content_hash(engine_arch),
               flux_ir.content_hash(mapping) if mapping is not None else None, metrics)
        raw = self._memo.get(key)
        if raw is None:
            raw = self._inner.evaluate(
                Candidate(workload=sliced, arch=engine_arch, mapping=mapping), budget, metrics)
            self._memo[key] = raw
        return self._calibrated(raw, sliced, engine_arch)

    def _calibrated(self, result: Result, sliced: dict[str, Any], engine_arch: dict[str, Any]) -> Result:
        if self._calibration_db_path is None:
            return result
        import flux_ir
        from flux_calibration import CalibrationStore, calibrate_result

        with CalibrationStore(self._calibration_db_path) as calibration:
            return calibrate_result(
                result, calibration,
                workload_hash=flux_ir.content_hash(sliced),
                arch_hash=flux_ir.content_hash(engine_arch),
            )

    def evaluate_batch(
        self, candidates: list[Candidate], budget: Budget, metrics: frozenset[str]
    ) -> list[Result]:
        """Batch composition with in-batch engine dedup (docs/decisions.md D238): the batch's
        candidates usually share engines — {8,16}^2's four assignments name only four distinct
        (op, width) components — so the UNIQUE components go to `inner.evaluate_batch` in one
        call (a CachingEvaluator/ChiaParallelEvaluator stack underneath batches and dispatches
        them concurrently), and each candidate's Result is composed from the shared answers.
        Composition and per-component calibration stay in `evaluate`'s code path via a
        one-call memo, so the two paths cannot drift."""
        import flux_ir

        unique: dict[tuple[str, str, str | None, frozenset[str]], Candidate] = {}
        for candidate in candidates:
            comp = candidate.arch
            if not isinstance(comp, dict) or comp.get("kind") != "engine_per_op":
                continue  # evaluate() below raises the typed error for this candidate
            for op_id, engine_arch in comp["components"].items():
                sliced = slice_workload(candidate.workload, op_id)
                mapping = candidate.mapping
                key = (flux_ir.content_hash(sliced), flux_ir.content_hash(engine_arch),
                       flux_ir.content_hash(mapping) if mapping is not None else None, metrics)
                if key not in self._memo:
                    unique.setdefault(
                        key, Candidate(workload=sliced, arch=engine_arch, mapping=mapping))
        if unique:
            keys = list(unique)
            shared = self._inner.evaluate_batch([unique[k] for k in keys], budget, metrics)
            self._memo.update(zip(keys, shared))
        return [self.evaluate(c, budget, metrics) for c in candidates]

    def evaluate(
        self, candidate: Candidate, budget: Budget, metrics: frozenset[str]
    ) -> Result:
        comp = candidate.arch
        if not isinstance(comp, dict) or comp.get("kind") != "engine_per_op":
            raise NotACompositionDocument(
                f"ComposedEvaluator needs an engine_per_op composition document, got "
                f"{type(comp).__name__} with kind={comp.get('kind') if isinstance(comp, dict) else None!r}"
            )
        components: dict[str, dict[str, Any]] = comp["components"]
        if not components:
            raise NotACompositionDocument("composition document has no components")

        per_op: dict[str, Result] = {}
        for op_id, engine_arch in components.items():
            sliced = slice_workload(candidate.workload, op_id)
            per_op[op_id] = self._component_result(
                sliced, engine_arch, budget, metrics, mapping=candidate.mapping)
        results = list(per_op.values())

        composed_metrics: dict[str, Estimate] = {}
        for name in _SUMMABLE:
            if name not in metrics:
                continue
            if any(r.refusal_for(name) is not None for r in results):
                continue  # a component refused it -> the composite honestly omits it
            estimates = [r.estimate_of(name) for r in results]
            units = {e.unit for e in estimates}
            if len(units) != 1:
                continue  # mixed units cannot be summed honestly
            composed_metrics[name] = Estimate(
                value=sum(e.value for e in estimates),
                ci_low=sum(e.ci_low for e in estimates),
                ci_high=sum(e.ci_high for e in estimates),
                unit=units.pop(),
                method=min((e.method for e in estimates),
                           key=lambda m: _METHOD_STRENGTH[m]),
            )

        violations = tuple(v for r in results for v in r.validity.violations)
        validity = Validity(
            ok=all(r.validity.ok for r in results),
            violations=violations,
            checker_version="composed:" + (results[0].validity.checker_version or "-"),
        )
        worst = min(results, key=lambda r: (r.domain.in_domain, -r.domain.distance))
        domain = Domain(
            in_domain=all(r.domain.in_domain for r in results),
            distance=max(r.domain.distance for r in results),
            nearest_calibration=worst.domain.nearest_calibration,
        )
        # The bottleneck worth reporting is the dominant component's: the op that contributes
        # the most latency is where the chain's time goes.
        dominant_id = max(
            per_op,
            key=lambda op_id: per_op[op_id].metric("latency_cycles").value_or(0.0),
        )
        bottleneck = Bottleneck(
            limiter=per_op[dominant_id].bottleneck.limiter,
            per_level_utilisation=dict(per_op[dominant_id].bottleneck.per_level_utilisation),
            roofline=per_op[dominant_id].bottleneck.roofline,
            top_costs=tuple(f"{dominant_id}: {c}" for c in per_op[dominant_id].bottleneck.top_costs),
        )
        recommending = [r for r in results if r.escalation.recommended]
        escalation = Escalation(
            recommended=bool(recommending),
            next_rung=recommending[0].escalation.next_rung if recommending else None,
            reason=recommending[0].escalation.reason if recommending else None,
        )
        # `<inner>+composed`: keeps the inner backend's own string as the prefix, so the
        # campaign cache probe (evaluator_prefix = the backend name) matches these rows too.
        provenance = Provenance(
            evaluator=f"{results[0].provenance.evaluator}+composed",
            inputs={
                "composition": comp.get("id", "engine_per_op"),
                **{f"component:{op_id}": r.provenance.evaluator for op_id, r in per_op.items()},
            },
            calibration=next(
                (r.provenance.calibration for r in results if r.provenance.calibration), None),
            wall_clock_s=sum(r.provenance.wall_clock_s or 0.0 for r in results) or None,
        )
        return Result(
            metrics=composed_metrics,
            validity=validity,
            domain=domain,
            bottleneck=bottleneck,
            provenance=provenance,
            escalation=escalation,
        )
