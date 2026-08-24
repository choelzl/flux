"""System-level composition (docs/decisions.md D251): per-op engines sized in BOTH compute
width and a memory level. The claims: geometry is the per-op (width x size) grid built from
the proven joint generator; the objective validates the new kind; and a full campaign over a
cost model with a real interior memory optimum finds HETEROGENEOUS engines — the big op
earns the big buffer, the small op doesn't pay for one."""

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
from flux_search_architecture.composition_candidates import generate_system_candidates
from flux_search_campaign import InvalidObjectiveError, parse_objective, run_campaign_steps
from flux_store import CampaignStore

FLUX_ROOT = Path(__file__).resolve().parents[2]

_WORKLOAD = {
    "schema_version": "0.1.0", "id": "test/two-layer", "ops": [
        {"id": "big", "kind": "einsum", "expr": "B C, C K -> B K",
         "bounds": {"B": 4, "C": 512, "K": 64}},
        {"id": "small", "kind": "einsum", "expr": "B C, C K -> B K",
         "bounds": {"B": 4, "C": 32, "K": 16}},
    ]}


@pytest.fixture(scope="module")
def base_arch():
    return yaml.safe_load(
        (FLUX_ROOT / "core/ir/architecture/examples/simple-npu-1d-v1.yaml").read_text())


def test_system_candidates_are_the_per_op_width_by_size_grid(base_arch):
    candidates = generate_system_candidates(
        base_arch, _WORKLOAD, [8, 16], "gbuf", [64, 512])
    assert len(candidates) == 16  # (2 widths x 2 sizes) ** 2 ops
    (c,) = [c for c in candidates
            if c.assignment == {"big": {"width": 16, "size_kb": 512},
                                "small": {"width": 8, "size_kb": 64}}]
    big = c.arch["components"]["big"]
    compute = next(n for n in big["hierarchy"] if n["class"] == "compute")
    gbuf = next(n for n in big["hierarchy"] if n.get("level") == "gbuf")
    assert list(compute["attrs"]["dims"].values()) == [16]
    assert gbuf["attrs"]["size_kb"] == 512
    small = c.arch["components"]["small"]
    small_gbuf = next(n for n in small["hierarchy"] if n.get("level") == "gbuf")
    assert small_gbuf["attrs"]["size_kb"] == 64
    assert c.arch["memory_level"] == "gbuf"


def _doc(base_arch, search):
    return {
        "schema_version": "0.1.0",
        "id": "test/system/v1",
        "objectives": [
            {"metric": "latency_cycles", "direction": "minimize"},
            {"metric": "energy_pj", "direction": "minimize"},
        ],
        "mode": "pareto",
        "workload": {"inline": _WORKLOAD},
        "base_arch": {"inline": base_arch},
        "backends": {"screening": "fake"},
        "search": search,
        "strategy": {"kind": "grid", "seed": 0},
        "budget": {"evaluations": 32},
    }


def test_composition_system_objective_validation(base_arch):
    parse_objective(_doc(base_arch, {
        "kind": "composition_system", "widths": [8, 16], "level": "gbuf",
        "sizes_kb": [64, 512]}))
    with pytest.raises(InvalidObjectiveError, match="needs widths"):
        parse_objective(_doc(base_arch, {"kind": "composition_system",
                                         "level": "gbuf", "sizes_kb": [64]}))
    with pytest.raises(InvalidObjectiveError, match="needs sizes_kb"):
        parse_objective(_doc(base_arch, {"kind": "composition_system",
                                         "widths": [8], "level": "gbuf"}))
    with pytest.raises(InvalidObjectiveError, match="needs level"):
        parse_objective(_doc(base_arch, {"kind": "composition_system",
                                         "widths": [8], "sizes_kb": [64]}))


class _SystemPricedInner:
    """Interior-optimum pricing, per single-op slice: latency = MACs/width; energy =
    MACs * (0.5 + size/512) + traffic_penalty where the penalty falls with buffer size but
    only matters when the op's working set exceeds the buffer. Consequence: the BIG op's
    energy optimum is the big buffer, the SMALL op's is the small one — a uniform memory
    size is dominated either way."""

    def evaluate(self, candidate: Candidate, budget: Budget, metrics: frozenset) -> Result:
        (op,) = candidate.workload["ops"]
        macs = 1.0
        for v in op["bounds"].values():
            macs *= v
        compute = next(n for n in candidate.arch["hierarchy"] if n["class"] == "compute")
        (width,) = compute["attrs"]["dims"].values()
        gbuf = next(n for n in candidate.arch["hierarchy"] if n.get("level") == "gbuf")
        size = gbuf["attrs"]["size_kb"]
        working_set_kb = macs / 1024.0  # a stand-in that makes big ops want big buffers
        traffic = 4.0 * macs * max(0.0, 1.0 - size / max(size, working_set_kb * 4))
        energy = macs * (0.5 + size / 512.0) + traffic
        mk = {
            "latency_cycles": Estimate(value=macs / width, ci_low=macs / width,
                                       ci_high=macs / width, unit="c",
                                       method=Method.ANALYTIC),
            "energy_pj": Estimate(value=energy, ci_low=energy, ci_high=energy, unit="pJ",
                                  method=Method.ANALYTIC),
        }
        return Result(
            metrics=mk, validity=Validity(ok=True, checker_version="t"),
            domain=Domain(in_domain=True),
            bottleneck=Bottleneck(limiter=Limiter.COMPUTE),
            provenance=Provenance(evaluator="fake@1", inputs={}),
            escalation=Escalation(recommended=False),
        )


def test_heterogeneous_engines_win_the_system_campaign(base_arch, tmp_path):
    doc = _doc(base_arch, {"kind": "composition_system", "widths": [8, 16],
                           "level": "gbuf", "sizes_kb": [64, 512]})
    objective = parse_objective(doc)
    store = CampaignStore(str(tmp_path / "sys.db"))
    cid, _ = store.start_campaign(doc, objective.objective_hash)
    report = run_campaign_steps(store, cid, make_evaluator=lambda n: _SystemPricedInner())

    assert report.status == "done"
    trials = store.ok_trials(cid)
    assert len(trials) == 16

    # per-op energy optima really are heterogeneous under this pricing: verify from the
    # trials themselves (not the model's intent) that at fixed widths, the best-energy
    # assignment gives the big op the big buffer and the small op the small one
    def energy(assignment):
        (t,) = [t for t in trials if t.candidate["assignment"] == assignment]
        return t.result.value_of("energy_pj")

    hetero = {"big": {"width": 16, "size_kb": 512}, "small": {"width": 16, "size_kb": 64}}
    uniform_small = {"big": {"width": 16, "size_kb": 64},
                     "small": {"width": 16, "size_kb": 64}}
    uniform_big = {"big": {"width": 16, "size_kb": 512},
                   "small": {"width": 16, "size_kb": 512}}
    assert energy(hetero) < energy(uniform_small)
    assert energy(hetero) < energy(uniform_big)

    # and the frontier contains that heterogeneous point (min-energy at max width)
    frontier_assignments = [f["candidate"]["assignment"] for f in report.frontier]
    assert hetero in frontier_assignments


def test_the_generator_attaches_word_width_bits_only_when_asked(base_arch):
    (c,) = generate_system_candidates(base_arch, _WORKLOAD, [8], "gbuf", [64],
                                      word_width_bits=64)
    for engine in c.arch["components"].values():
        gbuf = next(n for n in engine["hierarchy"] if n.get("level") == "gbuf")
        assert gbuf["attrs"]["word_width_bits"] == 64
    (plain,) = generate_system_candidates(base_arch, _WORKLOAD, [8], "gbuf", [64])
    for engine in plain.arch["components"].values():
        gbuf = next(n for n in engine["hierarchy"] if n.get("level") == "gbuf")
        assert "word_width_bits" not in gbuf["attrs"]


def test_word_width_bits_validation(base_arch):
    doc = _doc(base_arch, {"kind": "composition_system", "widths": [8], "level": "gbuf",
                           "sizes_kb": [64], "word_width_bits": 64})
    parse_objective(doc)
    with pytest.raises(InvalidObjectiveError, match="word_width_bits"):
        parse_objective(_doc(base_arch, {"kind": "composition_system", "widths": [8],
                                         "level": "gbuf", "sizes_kb": [64],
                                         "word_width_bits": 0}))


def test_the_area_rung_extracts_the_level_and_narrows_to_area(base_arch):
    """docs/decisions.md D252: the two load-bearing transformations. Extraction: the inner
    evaluator (which refuses multi-memory archs) receives ONLY the searched level plus tech.
    Narrowing: whatever the caller requested, the inner is asked for area_mm2 alone — CACTI's
    per-access energy under the objective's workload-energy name would silently corrupt the
    composite (deeper rung wins per metric)."""
    from flux_search_campaign import MemoryLevelAreaRung, slice_workload

    (c,) = generate_system_candidates(base_arch, _WORKLOAD, [8], "gbuf", [64],
                                      word_width_bits=64)
    engine = c.arch["components"]["big"]

    class _Inner:
        def __init__(self):
            self.seen = None

        def evaluate(self, candidate, budget, metrics):
            self.seen = (candidate.arch, metrics)
            return _SystemPricedInner().evaluate(
                Candidate(workload=candidate.workload, arch=engine, mapping=None),
                budget, frozenset({"latency_cycles", "energy_pj"}))

    inner = _Inner()
    rung = MemoryLevelAreaRung(inner, level="gbuf")
    rung.evaluate(Candidate(workload=slice_workload(_WORKLOAD, "big"), arch=engine,
                            mapping=None), Budget(),
                  frozenset({"latency_cycles", "energy_pj", "area_mm2"}))
    seen_arch, seen_metrics = inner.seen
    memory_nodes = [n for n in seen_arch["hierarchy"] if n.get("class") == "memory"]
    assert len(seen_arch["hierarchy"]) == 1 and len(memory_nodes) == 1
    assert memory_nodes[0]["level"] == "gbuf"
    assert memory_nodes[0]["attrs"]["word_width_bits"] == 64
    assert seen_arch["tech"] == engine["tech"]  # technology node travels
    assert seen_metrics == frozenset({"area_mm2"})  # narrowed, whatever was asked


class _FakeCacti:
    """Area proportional to size_kb; REFUSES multi-memory archs exactly like the real one,
    so the wiring test fails loudly if the runner ever stops extracting."""

    def evaluate(self, candidate, budget, metrics):
        memory_nodes = [n for n in candidate.arch["hierarchy"]
                        if n.get("class") == "memory"]
        assert len(memory_nodes) == 1, "extraction did not happen"
        assert metrics == frozenset({"area_mm2"}), "narrowing did not happen"
        size = memory_nodes[0]["attrs"]["size_kb"]
        area = size / 1000.0
        return Result(
            metrics={"area_mm2": Estimate(value=area, ci_low=area, ci_high=area,
                                          unit="mm2", method=Method.SIMULATED)},
            validity=Validity(ok=True, checker_version="t"),
            domain=Domain(in_domain=True),
            bottleneck=Bottleneck(limiter=Limiter.MEMORY),
            provenance=Provenance(evaluator="cacti7@fake", inputs={}),
            escalation=Escalation(recommended=False),
        )


def test_the_runner_wires_the_cacti_rung_through_extraction(base_arch, tmp_path):
    """The full loop with a cacti escalation rung: area joins the composite at cacti
    fidelity as the SUM of the engines' searched-level areas."""
    doc = _doc(base_arch, {"kind": "composition_system", "widths": [8, 16],
                           "level": "gbuf", "sizes_kb": [64, 512],
                           "word_width_bits": 64})
    doc["objectives"].append(
        {"metric": "area_mm2", "direction": "minimize", "measured_at": "escalation"})
    doc["backends"] = {"screening": "fake", "escalation": ["cacti"]}
    objective = parse_objective(doc)
    store = CampaignStore(str(tmp_path / "area.db"))
    cid, _ = store.start_campaign(doc, objective.objective_hash)

    def make(name):
        return _FakeCacti() if name == "cacti" else _SystemPricedInner()

    report = run_campaign_steps(store, cid, make_evaluator=make)
    assert report.status == "done"
    assert report.escalated_frontier
    for entry in report.escalated_frontier:
        assert entry["metrics"]["area_mm2"]["fidelity"] == "cacti"
        sizes = [a["size_kb"] for a in entry["candidate"]["assignment"].values()]
        assert entry["metrics"]["area_mm2"]["value"] == pytest.approx(sum(sizes) / 1000.0)


def test_scaling_factors_are_the_published_table_never_interpolated():
    """docs/decisions.md D253: vendored VERBATIM from Stillmaker & Baas 2017 Table 4 (7nm
    row), ratios reproducing the printed matrix within its own rounding; nodes the table
    does not carry are refused, never interpolated."""
    from flux_evaluator_cacti.scaling import (
        UnsupportedScalingNode, area_scaling_factor, scale_area_mm2,
    )

    assert area_scaling_factor(32, 7) == pytest.approx(7.8)
    assert area_scaling_factor(45, 7) == pytest.approx(17.0)
    # the ratio check the docstring claims: 45->32 via the 7nm row vs the printed 2.2
    assert area_scaling_factor(45, 32) == pytest.approx(2.2, abs=0.03)
    with pytest.raises(UnsupportedScalingNode, match="not in the published table"):
        area_scaling_factor(28, 7)
    scaled, note = scale_area_mm2(0.078, from_nm=32, to_nm=7)
    assert scaled == pytest.approx(0.01)
    assert "Stillmaker" in note and "7.8" in note


def test_cacti_refuses_sub_22nm_with_the_scaled_route_named():
    from flux_evaluator_cacti.architecture_translator import (
        NotExpressibleError, architecture_ir_to_technology_um,
    )

    with pytest.raises(NotExpressibleError, match="22nm floor") as exc:
        architecture_ir_to_technology_um(
            {"id": "x", "tech": {"node": "n7"}, "hierarchy": []})
    assert "cacti_scale_from_nm" in str(exc.value)


def test_the_scaled_rung_characterizes_at_the_native_node_and_annotates(base_arch):
    """The rung rewrites the extracted arch to the native node, scales the returned area by
    the published factor, downgrades the method (a scaled simulation is a model estimate),
    and the citation travels in provenance — asserted from a fake inner that records what
    node it was asked to run at."""
    import copy

    from flux_search_campaign import MemoryLevelAreaRung, slice_workload

    n7_arch = copy.deepcopy(base_arch)
    n7_arch["tech"]["node"] = "n7"
    (c,) = generate_system_candidates(n7_arch, _WORKLOAD, [8], "gbuf", [32],
                                      word_width_bits=64)
    engine = c.arch["components"]["big"]

    class _Inner:
        def __init__(self):
            self.seen_node = None

        def evaluate(self, candidate, budget, metrics):
            self.seen_node = candidate.arch["tech"]["node"]
            return Result(
                metrics={"area_mm2": Estimate(value=0.078, ci_low=0.078, ci_high=0.078,
                                              unit="mm2", method=Method.SIMULATED)},
                validity=Validity(ok=True, checker_version="t"),
                domain=Domain(in_domain=True),
                bottleneck=Bottleneck(limiter=Limiter.MEMORY),
                provenance=Provenance(evaluator="cacti7@fake", inputs={}),
                escalation=Escalation(recommended=False),
            )

    inner = _Inner()
    # scripted efficiency probe (the injectability pattern): 80% at the native node
    rung = MemoryLevelAreaRung(inner, level="gbuf", scale_from_nm=32,
                               efficiency_probe=lambda arch, nm: 0.8)
    result = rung.evaluate(
        Candidate(workload=slice_workload(_WORKLOAD, "big"), arch=engine, mapping=None),
        Budget(), frozenset({"area_mm2"}))
    assert inner.seen_node == "n32"  # characterized at the native node
    est = result.estimate_of("area_mm2")
    bits = 32 * 1024 * 8
    floor = bits * 0.027e-6
    assert est.value == pytest.approx(floor / 0.8)  # D256 refined estimate
    assert est.ci_low == pytest.approx(floor)  # the physical bound is the interval's floor
    # the upper bound keeps the larger of the two estimates (logic-scaled 0.078/7.8 = 0.01
    # vs refined 0.00885): neither model is established as an upper bound alone
    assert est.ci_high == pytest.approx(0.01)
    assert est.method == Method.ANALYTIC  # downgraded: scaled, not simulated
    assert result.provenance.evaluator == "cacti7@fake+scaled"
    assert "Stillmaker" in result.provenance.inputs["area_scaling"]
    assert "0.8000 array" in result.provenance.inputs["area_scaling"]

    # with the probe disabled the plain floor-clamped path runs (0.078/7.8 = 0.01 > floor)
    inner_p = _Inner()
    plain = MemoryLevelAreaRung(inner_p, level="gbuf", scale_from_nm=32,
                                efficiency_probe=False)
    r2 = plain.evaluate(
        Candidate(workload=slice_workload(_WORKLOAD, "big"), arch=engine, mapping=None),
        Budget(), frozenset({"area_mm2"}))
    assert r2.estimate_of("area_mm2").value == pytest.approx(0.01)

    # same-node declaration: no rewrite, no scaling, untouched result and provenance
    n32_arch = copy.deepcopy(base_arch)
    n32_arch["tech"]["node"] = "n32"
    inner2 = _Inner()
    rung32 = MemoryLevelAreaRung(inner2, level="gbuf", scale_from_nm=32)
    (c32,) = generate_system_candidates(n32_arch, _WORKLOAD, [8], "gbuf", [32],
                                        word_width_bits=64)
    r = rung32.evaluate(
        Candidate(workload=slice_workload(_WORKLOAD, "big"),
                  arch=c32.arch["components"]["big"], mapping=None),
        Budget(), frozenset({"area_mm2"}))
    assert inner2.seen_node == "n32"
    assert r.provenance.evaluator == "cacti7@fake"  # no +scaled, nothing was scaled
    assert r.estimate_of("area_mm2").value == pytest.approx(0.078)


def test_the_bitcell_floor_clamps_impossible_densities():
    """docs/decisions.md D255: the arc-2 check found the logic-geometry factor producing a
    7nm macro DENSER than the published N7 bitcell (0.0234 vs 0.027 um2/bit) — physically
    impossible. The floor clamps to bits x bitcell with the citation in the note; values
    already above the floor pass through untouched; nodes without a verified anchor never
    clamp (no guessed constants)."""
    from flux_evaluator_cacti.scaling import scale_area_mm2

    bits = 32 * 1024 * 8
    clamped, note = scale_area_mm2(0.047928, from_nm=32, to_nm=7, bits=bits)
    assert clamped == pytest.approx(bits * 0.027e-6)
    assert "bitcell floor" in note and "TSMC N7 HD" in note
    unclamped, note2 = scale_area_mm2(0.5, from_nm=32, to_nm=7, bits=bits)
    assert unclamped == pytest.approx(0.5 / 7.8) and "bitcell floor" not in note2
    # no anchor for 10nm -> no clamp, however dense
    dense, note3 = scale_area_mm2(1e-9, from_nm=32, to_nm=10, bits=bits)
    assert "bitcell floor" not in note3


def test_the_efficiency_refinement_is_sourced_and_bounded():
    """docs/decisions.md D256: bits x published bitcell / CACTI's OWN reported array
    efficiency — both inputs sourced, the one assumption stated in the note. The parser is
    pinned against the tool's real output line."""
    from flux_evaluator_cacti.scaling import parse_area_efficiency, scale_area_mm2

    eff = parse_area_efficiency(
        "  Area efficiency (Memory cell area/Total area) - 81.7717 %")
    assert eff == pytest.approx(0.817717)
    assert parse_area_efficiency("no such line") is None

    bits = 32 * 1024 * 8
    refined, note = scale_area_mm2(0.047928, from_nm=32, to_nm=7, bits=bits,
                                   array_efficiency=eff)
    assert refined == pytest.approx(bits * 0.027e-6 / eff)
    assert "array" in note and "node-invariant" in note and "TSMC N7 HD" in note
    with pytest.raises(ValueError, match="array_efficiency"):
        scale_area_mm2(0.047928, from_nm=32, to_nm=7, bits=bits, array_efficiency=1.5)


def test_timing_and_power_scaling_golden_vectors():
    """docs/decisions.md D257: both methods pinned against the primary source's own numbers.
    Measured (default): Table 2 FO4-inverter ratios at nominal voltages. Polynomial: exact
    against the authors' errata worked example, with the measured ~1.8x energy/power
    deviation carried as a caveat in its note."""
    from flux_evaluator_cacti.scaling import (
        UnsupportedScalingNode, scale_delay_ns, scale_energy_pj, scale_power_w,
    )

    # measured ratios, straight from the vendored Table 2 rows (delay needs the explicit
    # opt-in since D258 refused it as an SRAM proxy — the factor itself is still correct)
    d, dn = scale_delay_ns(1.0, from_nm=32, to_nm=7, allow_inverter_proxy=True)
    assert 1 / d == pytest.approx(9.8 / 2.47)
    assert "Table 2" in dn and "FO4-inverter" in dn
    e, _ = scale_energy_pj(1.0, from_nm=32, to_nm=7)
    assert 1 / e == pytest.approx(0.51 / 0.111)
    pw, _ = scale_power_w(1.0, from_nm=32, to_nm=7)
    assert 1 / pw == pytest.approx(2.47 / 0.789)

    # the errata golden vector: EF(32 HP, 0.9V)/EF(65 bulk, 1.3V) scaling 1 pJ -> 0.1755 pJ
    e2, note = scale_energy_pj(1.0, from_nm=65, to_nm=32, method="polynomial",
                               v_from=1.3, v_to=0.9)
    assert e2 == pytest.approx(0.1755, abs=0.0002)
    assert "deviate ~1.8x" in note  # the measured discrepancy travels with the method

    with pytest.raises(UnsupportedScalingNode, match="no vendored Table 2"):
        scale_delay_ns(1.0, from_nm=90, to_nm=7, allow_inverter_proxy=True)
    with pytest.raises(ValueError, match="needs voltages"):
        scale_delay_ns(1.0, from_nm=20, to_nm=7, method="polynomial",
                       allow_inverter_proxy=True)


def test_delay_scaling_is_refused_after_the_reference_check():
    """docs/decisions.md D258: validated against the ASAP7 fakeram7_256x32 macro OpenROAD
    ships (218 ps at 8192 bits), inverter-ratio delay scaling came out 3.2x too fast — SRAM
    access is bitline-RC dominated, an FO4 chain is not. So it is refused by default, the
    opt-in carries the measured error, and the published reference is what callers get."""
    from flux_evaluator_cacti.scaling import (
        UnsupportedScalingNode, scale_delay_ns, scale_power_w,
    )

    with pytest.raises(UnsupportedScalingNode, match="3.2x too fast"):
        scale_delay_ns(0.2651, from_nm=32, to_nm=7)
    proxied, note = scale_delay_ns(0.2651, from_nm=32, to_nm=7, allow_inverter_proxy=True)
    assert proxied == pytest.approx(0.0668, abs=0.0002)
    assert "3.2x optimistic" in note  # the error travels with the opt-in value

    # the published absolute, scaled by CACTI's own size/shape ratio (D259)
    from flux_evaluator_cacti.scaling import REFERENCE_ACCESS_NS, anchored_access_ns

    same, note_same = anchored_access_ns(0.1488, 0.1488)
    assert same == pytest.approx(REFERENCE_ACCESS_NS)  # reference geometry -> reference value
    bigger, note_big = anchored_access_ns(0.2651, 0.1488)  # 4KBx64 vs the reference geometry
    assert bigger == pytest.approx(0.3884, abs=0.0005)
    assert bigger > same  # a bigger array is slower, which the flat reference never showed
    assert "fakeram7_256x32" in note_big and "technology cancels in the ratio" in note_big

    # leakage survived the same check at 1.5x, conservative — and says so
    _, pnote = scale_power_w(0.0024507, from_nm=32, to_nm=7)
    assert "within 1.5x (conservative)" in pnote


def test_the_size_and_shape_dependence_the_flat_reference_lacks():
    """docs/decisions.md D259, the user's question answered: access, leakage and area all
    depend on bit count AND aspect ratio, and the anchored model carries that dependence.
    Measured inputs (CACTI@32nm): reference geometry 148.8 ps, 4KBx64 265.1 ps, 32KBx64
    315.0 ps; efficiency 0.707 / 0.736 / 0.818; and at EQUAL bits (16384) the wide 64x256
    shape runs 0.358 efficiency vs 0.715 for the deep 256x64 — a 2.0x area-per-bit spread
    that the published fakeram7 family shows too (0.0950 vs 0.0426 um2/bit)."""
    from flux_evaluator_cacti.scaling import anchored_access_ns, scale_area_mm2

    ref_access = 0.1488
    assert anchored_access_ns(0.2651, ref_access)[0] < anchored_access_ns(0.3150, ref_access)[0]

    # area: same bits, different shape -> different efficiency -> different area
    bits = 16384
    wide, _ = scale_area_mm2(0.006834, from_nm=32, to_nm=7, bits=bits, array_efficiency=0.358)
    deep, _ = scale_area_mm2(0.003426, from_nm=32, to_nm=7, bits=bits, array_efficiency=0.715)
    assert wide == pytest.approx(2 * deep, rel=0.01)  # the shape penalty survives scaling


def test_multi_port_macros_are_refused_by_the_generator():
    """CACTI models multi-port SRAM and the architecture translator accepts attrs.ports, but
    the macro generator emits a fixed 1RW pin interface — so it refuses rather than emit a
    LEF/liberty that misrepresents the requested ports (D259)."""
    from flux_evaluator_openroad.sram_macro import generate_sram_macro

    kw = dict(name="m", size_kb=4, word_width_bits=64, area_mm2=0.001202,
              access_ns=0.218, provenance="test")
    generate_sram_macro(**kw, ports={"rw": 1})  # the interface it really emits
    with pytest.raises(ValueError, match="1RW interface"):
        generate_sram_macro(**kw, ports={"r": 2, "w": 1})
