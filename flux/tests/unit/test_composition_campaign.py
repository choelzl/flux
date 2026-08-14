"""Per-op engine composition against an objective (docs/decisions.md D236): the candidate
generator's geometry, the ComposedEvaluator's per-metric composition semantics, and the full
campaign loop over a composition space — synthetic inner evaluators here (the claims are about
composition arithmetic and campaign wiring, not any evaluator; the live suite runs the real
zigzag/rtl chain).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
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
from flux_search_architecture.composition_candidates import (
    NotACompositionCandidate,
    generate_composition_candidates,
)
from flux_search_campaign import (
    ComposedEvaluator,
    InvalidObjectiveError,
    NotACompositionDocument,
    parse_objective,
    run_campaign_steps,
    slice_workload,
)

FLUX_ROOT = Path(__file__).resolve().parents[2]

_WORKLOAD = {
    "schema_version": "0.1.0",
    "id": "test/two-layer",
    "ops": [
        {"id": "layer0", "kind": "einsum", "expr": "B C, C K -> B K",
         "bounds": {"B": 4, "C": 64, "K": 32}},
        {"id": "layer1", "kind": "einsum", "expr": "B C, C K -> B K",
         "bounds": {"B": 4, "C": 32, "K": 16}},
    ],
}


@pytest.fixture(scope="module")
def base_arch():
    return yaml.safe_load(
        (FLUX_ROOT / "core/ir/architecture/examples/simple-npu-1d-v1.yaml").read_text())


# -- geometry -------------------------------------------------------------------------------


def test_composition_candidates_are_the_full_per_op_grid(base_arch):
    candidates = generate_composition_candidates(base_arch, _WORKLOAD, [8, 16])
    assignments = {tuple(sorted(c.assignment.items())) for c in candidates}
    assert assignments == {
        (("layer0", 8), ("layer1", 8)), (("layer0", 8), ("layer1", 16)),
        (("layer0", 16), ("layer1", 8)), (("layer0", 16), ("layer1", 16)),
    }
    for c in candidates:
        assert c.arch["kind"] == "engine_per_op"
        # each component really is base_arch at the assigned width — the same document
        # generate_width_candidates produces, never a new shape
        for op_id, width in c.assignment.items():
            engine = c.arch["components"][op_id]
            compute = next(n for n in engine["hierarchy"] if n["class"] == "compute")
            (dim_width,) = compute["attrs"]["dims"].values()
            assert dim_width == width
        # the trial's dedup key is the assignment, not the (huge) arch document
        d = c.to_dict()
        assert set(d) == {"assignment", "arch"}


def test_per_op_width_lists_shape_the_grid_per_op(base_arch):
    """docs/decisions.md D241: a chain whose ops have different divisibility gets per-op
    lists — layer1 restricted to {4}, layer0 falling back to the global list — and the grid
    is the product of each op's OWN choices, never the global cross product."""
    candidates = generate_composition_candidates(
        base_arch, _WORKLOAD, [8, 16], widths_per_op={"layer1": [4]})
    assignments = {tuple(sorted(c.assignment.items())) for c in candidates}
    assert assignments == {
        (("layer0", 8), ("layer1", 4)), (("layer0", 16), ("layer1", 4)),
    }
    # engines exist at every named width, including the per-op-only one
    (c,) = [c for c in candidates if c.assignment["layer0"] == 8]
    compute = next(n for n in c.arch["components"]["layer1"]["hierarchy"]
                   if n["class"] == "compute")
    assert list(compute["attrs"]["dims"].values()) == [4]

    with pytest.raises(NotACompositionCandidate, match="has no allowed widths"):
        generate_composition_candidates(
            base_arch, _WORKLOAD, widths_per_op={"layer1": [4]})  # layer0 uncovered
    with pytest.raises(NotACompositionCandidate, match="not in workload"):
        generate_composition_candidates(
            base_arch, _WORKLOAD, [8], widths_per_op={"layer9": [4]})


def test_widths_per_op_objective_validation(base_arch):
    from flux_search_campaign import InvalidObjectiveError

    doc = _objective_doc(base_arch, None, search={
        "kind": "composition_width", "widths_per_op": {"layer0": [8, 16], "layer1": [4]}})
    parse_objective(doc)  # per-op only, no global list: valid
    with pytest.raises(InvalidObjectiveError, match="widths_per_op must map"):
        parse_objective(_objective_doc(base_arch, None, search={
            "kind": "composition_width", "widths_per_op": {"layer0": []}}))
    with pytest.raises(InvalidObjectiveError, match="and/or"):
        parse_objective(_objective_doc(base_arch, None,
                                       search={"kind": "composition_width"}))


def test_a_workload_without_einsum_ops_is_refused(base_arch):
    with pytest.raises(NotACompositionCandidate, match="no einsum ops"):
        generate_composition_candidates(
            base_arch, {"schema_version": "0.1.0", "id": "x",
                        "ops": [{"id": "k", "kind": "compute_kernel"}]}, [8])


def test_sliced_workloads_are_valid_single_op_ir():
    import flux_ir

    sliced = slice_workload(_WORKLOAD, "layer1")
    flux_ir.validate("workload", sliced)  # the REAL schema
    assert [op["id"] for op in sliced["ops"]] == ["layer1"]
    assert sliced["id"] == "test/two-layer/op/layer1"
    assert [op["id"] for op in _WORKLOAD["ops"]] == ["layer0", "layer1"]  # parent untouched


# -- composition semantics ------------------------------------------------------------------


def _result(metrics: dict[str, tuple], *, method=Method.ANALYTIC, evaluator="fake@1",
            limiter=Limiter.COMPUTE, valid=True, in_domain=True) -> Result:
    return Result(
        metrics={
            name: Estimate(value=v, ci_low=lo, ci_high=hi, unit=unit, method=method)
            for name, (lo, v, hi, unit) in metrics.items()
        },
        validity=Validity(ok=valid, checker_version="test"),
        domain=Domain(in_domain=in_domain),
        bottleneck=Bottleneck(limiter=limiter),
        provenance=Provenance(evaluator=evaluator, inputs={}),
        escalation=Escalation(recommended=False),
    )


class _PerOpInner:
    """Scripted single-arch evaluator: answers by the sliced workload's op id."""

    def __init__(self, by_op: dict[str, Result]) -> None:
        self.by_op = by_op
        self.calls: list[tuple[str, dict]] = []

    def evaluate(self, candidate: Candidate, budget: Budget, metrics: frozenset) -> Result:
        (op,) = candidate.workload["ops"]
        self.calls.append((op["id"], candidate.arch))
        return self.by_op[op["id"]]


def _composition_arch(base_arch, assignment):
    (c,) = [c for c in generate_composition_candidates(base_arch, _WORKLOAD,
                                                       sorted(set(assignment.values())))
            if c.assignment == assignment]
    return c.arch


def test_summable_metrics_sum_with_ci_sums_and_the_weakest_method(base_arch):
    inner = _PerOpInner({
        "layer0": _result({"latency_cycles": (90.0, 100.0, 120.0, "cycles"),
                           "energy_pj": (10.0, 10.0, 10.0, "pJ")}, method=Method.MEASURED),
        "layer1": _result({"latency_cycles": (40.0, 50.0, 55.0, "cycles"),
                           "energy_pj": (5.0, 5.0, 5.0, "pJ")}, method=Method.ANALYTIC),
    })
    comp = ComposedEvaluator(inner)
    result = comp.evaluate(
        Candidate(workload=_WORKLOAD,
                  arch=_composition_arch(base_arch, {"layer0": 16, "layer1": 8}),
                  mapping=None),
        Budget(), frozenset({"latency_cycles", "energy_pj"}),
    )
    est = result.estimate_of("latency_cycles")
    assert (est.ci_low, est.value, est.ci_high) == (130.0, 150.0, 175.0)
    assert result.value_of("energy_pj") == 15.0
    # a chain measured on one op and estimated on the other is estimate-grade, not measured
    assert est.method == Method.ANALYTIC
    # each engine got ITS op's arch: layer0 at width 16, layer1 at width 8
    widths = {}
    for op_id, arch in inner.calls:
        compute = next(n for n in arch["hierarchy"] if n["class"] == "compute")
        (widths[op_id],) = compute["attrs"]["dims"].values()
    assert widths == {"layer0": 16, "layer1": 8}


def test_unsummable_and_component_refused_metrics_are_omitted_never_guessed(base_arch):
    inner = _PerOpInner({
        "layer0": _result({"latency_cycles": (1, 1, 1, "cycles"),
                           "power_w": (2, 2, 2, "W")}),
        "layer1": _result({"latency_cycles": (1, 1, 1, "cycles"),
                           "power_w": (3, 3, 3, "W")}),  # no energy_pj anywhere
    })
    result = ComposedEvaluator(inner).evaluate(
        Candidate(workload=_WORKLOAD,
                  arch=_composition_arch(base_arch, {"layer0": 8, "layer1": 8}),
                  mapping=None),
        Budget(), frozenset({"latency_cycles", "energy_pj", "power_w"}),
    )
    assert result.refusal_for("latency_cycles") is None
    # energy: a component refused it -> composite omits it
    assert result.refusal_for("energy_pj") is not None
    # power: not summable across a time-multiplexed chain -> omitted BY POLICY even though
    # every component carries it
    assert result.refusal_for("power_w") is not None


def test_provenance_keeps_the_inner_prefix_and_names_every_component(base_arch):
    inner = _PerOpInner({
        "layer0": _result({"latency_cycles": (1, 1, 1, "c")}, evaluator="zigzag@9.9"),
        "layer1": _result({"latency_cycles": (2, 2, 2, "c")}, evaluator="zigzag@9.9"),
    })
    result = ComposedEvaluator(inner).evaluate(
        Candidate(workload=_WORKLOAD,
                  arch=_composition_arch(base_arch, {"layer0": 8, "layer1": 8}),
                  mapping=None),
        Budget(), frozenset({"latency_cycles"}),
    )
    # startswith the backend name -> the campaign cache probe accepts these rows (D217)
    assert result.provenance.evaluator == "zigzag@9.9+composed"
    assert result.provenance.inputs["component:layer0"] == "zigzag@9.9"
    # the dominant (slowest) component supplies the bottleneck story
    assert result.bottleneck.limiter == Limiter.COMPUTE


def test_a_non_composition_arch_is_a_typed_refusal(base_arch):
    inner = _PerOpInner({})
    with pytest.raises(NotACompositionDocument):
        ComposedEvaluator(inner).evaluate(
            Candidate(workload=_WORKLOAD, arch=base_arch, mapping=None),
            Budget(), frozenset({"latency_cycles"}),
        )


# -- objective + full loop ------------------------------------------------------------------


def _objective_doc(base_arch, widths, **overrides):
    doc = {
        "schema_version": "0.1.0",
        "id": "test/composition/v1",
        "objectives": [
            {"metric": "latency_cycles", "direction": "minimize"},
            {"metric": "area_mm2", "direction": "minimize"},
        ],
        "mode": "pareto",
        "workload": {"inline": _WORKLOAD},
        "base_arch": {"inline": base_arch},
        "backends": {"screening": "fake"},
        "search": {"kind": "composition_width", "widths": widths},
        "strategy": {"kind": "grid", "seed": 0},
        "budget": {"evaluations": 32},
    }
    doc.update(overrides)
    return doc


def test_composition_width_objective_validation(base_arch):
    parse_objective(_objective_doc(base_arch, [8, 16]))
    with pytest.raises(InvalidObjectiveError, match="composition_width needs widths"):
        parse_objective(_objective_doc(base_arch, None,
                                       search={"kind": "composition_width"}))


class _WidthPricedInner:
    """Deterministic single-arch evaluator: latency = MACs / width, area = width / 100 —
    the real trade-off shape (wider engine: faster op, more silicon)."""

    def evaluate(self, candidate: Candidate, budget: Budget, metrics: frozenset) -> Result:
        (op,) = candidate.workload["ops"]
        macs = 1.0
        for v in op["bounds"].values():
            macs *= v
        compute = next(n for n in candidate.arch["hierarchy"] if n["class"] == "compute")
        (width,) = compute["attrs"]["dims"].values()
        return _result({
            "latency_cycles": (macs / width,) * 3 + ("cycles",),
            "area_mm2": (width / 100.0,) * 3 + ("mm2",),
        }, method=Method.ANALYTIC, evaluator="fake@1")


def test_calibration_reaches_inside_the_composition_per_component(base_arch, tmp_path):
    """docs/decisions.md D237: the flywheel at component granularity. A real CalibrationStore
    holds residuals for the inner evaluator (3x bias, the family's real shape); the composed
    result must carry the SUM OF CORRECTED component values, with per-component interval
    honesty composing too — an assignment whose every engine is in-pool gets a tight interval,
    one containing an unmeasured engine gets an honestly wide one. That width difference is
    exactly what grows the CI-aware contender set."""
    import flux_ir
    from flux_calibration import CalibrationStore

    inner = _PerOpInner({
        "layer0": _result({"latency_cycles": (300.0, 300.0, 300.0, "cycles")}),
        "layer1": _result({"latency_cycles": (150.0, 150.0, 150.0, "cycles")}),
    })
    cal_path = str(tmp_path / "cal.db")
    arch8 = _composition_arch(base_arch, {"layer0": 8, "layer1": 8})
    with CalibrationStore(cal_path) as cal:
        # pool: both width-8 engine points measured (plus a third distinct point so n >= 3 and
        # the bias is corrected, not just fenced — D106's rule), all at the family's 3x bias
        for op_id, predicted in (("layer0", 300.0), ("layer1", 150.0)):
            cal.add_record(
                workload_hash=flux_ir.content_hash(slice_workload(_WORKLOAD, op_id)),
                arch_hash=flux_ir.content_hash(arch8["components"][op_id]),
                evaluator="fake@1", metric="latency_cycles",
                predicted_value=predicted, reference_value=predicted / 3.0,
                reference_source="rtl_sim",
            )
        cal.add_record(
            workload_hash="elsewhere", arch_hash="elsewhere",
            evaluator="fake@1", metric="latency_cycles",
            predicted_value=90.0, reference_value=30.0, reference_source="rtl_sim",
        )

    composed = ComposedEvaluator(inner, calibration_db_path=cal_path)
    in_pool = composed.evaluate(
        Candidate(workload=_WORKLOAD, arch=arch8, mapping=None),
        Budget(), frozenset({"latency_cycles"}),
    )
    est = in_pool.estimate_of("latency_cycles")
    assert est.value == pytest.approx(150.0)  # (300 + 150) / 3 — corrected, then summed
    assert (est.ci_high - est.ci_low) / est.value < 0.10
    assert in_pool.provenance.calibration is not None

    # same widths for layer0, an unmeasured width-16 engine for layer1: corrected value still,
    # but the unmeasured component's conservative interval makes the composed one wide
    inner16 = _PerOpInner({
        "layer0": _result({"latency_cycles": (300.0, 300.0, 300.0, "cycles")}),
        "layer1": _result({"latency_cycles": (75.0, 75.0, 75.0, "cycles")}),
    })
    held_out = ComposedEvaluator(inner16, calibration_db_path=cal_path).evaluate(
        Candidate(workload=_WORKLOAD,
                  arch=_composition_arch(base_arch, {"layer0": 8, "layer1": 16}),
                  mapping=None),
        Budget(), frozenset({"latency_cycles"}),
    )
    held_est = held_out.estimate_of("latency_cycles")
    assert held_est.value == pytest.approx(125.0)  # 100 + 25, both corrected
    assert (held_est.ci_high - held_est.ci_low) > 3 * (est.ci_high - est.ci_low)


class _CountingInner:
    """Real-shaped fake: distinct answers per (op, width), counting every evaluation."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    def evaluate(self, candidate: Candidate, budget: Budget, metrics: frozenset) -> Result:
        (op,) = candidate.workload["ops"]
        compute = next(n for n in candidate.arch["hierarchy"] if n["class"] == "compute")
        (width,) = compute["attrs"]["dims"].values()
        self.calls.append((op["id"], width))
        macs = 1.0
        for v in op["bounds"].values():
            macs *= v
        return _result({"latency_cycles": (macs / width,) * 3 + ("cycles",)})


def test_shared_engines_are_evaluated_once_across_assignments(base_arch, tmp_path):
    """docs/decisions.md D237: component-level caching. The four assignments of {8,16}^2 name
    only four distinct (op, width) engines; the campaign must pay for each exactly once — at a
    real rung that difference is four placements instead of eight."""
    from flux_store import CampaignStore

    doc = _objective_doc(base_arch, [8, 16],
                         objectives=[{"metric": "latency_cycles", "direction": "minimize"}])
    objective = parse_objective(doc)
    store = CampaignStore(str(tmp_path / "dedupe.db"))
    cid, _ = store.start_campaign(doc, objective.objective_hash)
    inner = _CountingInner()
    report = run_campaign_steps(store, cid, make_evaluator=lambda name: inner)

    assert report.status == "done"
    assert len(store.ok_trials(cid, phase="screen")) == 4
    assert sorted(set(inner.calls)) == [
        ("layer0", 8), ("layer0", 16), ("layer1", 8), ("layer1", 16)]
    assert len(inner.calls) == 4  # 4 unique engines for 4 assignments x 2 components


def test_a_full_composition_campaign_finds_the_per_op_frontier(base_arch, tmp_path):
    """The end-to-end claim: the campaign walks the per-op grid, every trial's numbers are the
    sums of its components, the composition documents land in the store under their own kind,
    and the frontier is the real per-assignment Pareto set (all 4 points here are optimal: the
    latency/area trade is monotone along each axis of the assignment grid)."""
    from flux_store import CampaignStore

    doc = _objective_doc(base_arch, [8, 16])
    objective = parse_objective(doc)
    store = CampaignStore(str(tmp_path / "comp.db"))
    cid, _ = store.start_campaign(doc, objective.objective_hash)
    report = run_campaign_steps(store, cid, make_evaluator=lambda name: _WidthPricedInner())

    assert report.status == "done"
    trials = store.ok_trials(cid)
    assert len(trials) == 4

    l0_macs = 4 * 64 * 32  # 8192
    l1_macs = 4 * 32 * 16  # 2048
    by_assignment = {tuple(sorted(t.candidate["assignment"].items())): t for t in trials}
    for (l0w, l1w) in [(8, 8), (8, 16), (16, 8), (16, 16)]:
        t = by_assignment[(("layer0", l0w), ("layer1", l1w))]
        assert t.result.value_of("latency_cycles") == l0_macs / l0w + l1_macs / l1w
        assert t.result.value_of("area_mm2") == pytest.approx((l0w + l1w) / 100.0)
        assert t.result.provenance.evaluator == "fake@1+composed"
        # the stored arch document is a composition, filed as one
        stored = store.results.get_document(t.arch_hash)
        assert stored["kind"] == "engine_per_op"

    # The frontier says "spend area where the MACs are": at equal area (0.24), widening the
    # HEAVY layer (16,8: latency 768) dominates widening the light one (8,16: latency 1152) —
    # the non-uniform point a uniform width sweep cannot even express is on the frontier, and
    # the wrong non-uniform point is correctly excluded.
    frontier_keys = {f["candidate_key"] for f in report.frontier}
    assert frontier_keys == {
        '{"assignment": {"layer0": 8, "layer1": 8}}',
        '{"assignment": {"layer0": 16, "layer1": 8}}',
        '{"assignment": {"layer0": 16, "layer1": 16}}',
    }
