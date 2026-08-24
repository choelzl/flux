"""The real-area composition frontier (docs/decisions.md D237): per-layer engine assignments
ranked on REAL Verilator cycles and REAL OpenROAD placed silicon, end to end through one
campaign — the flagship the composition axis exists for.

The mechanism chain, every link load-bearing:
1. the calibration pool is built at engine widths the campaign never visits, so every campaign
   point is honestly EXTRAPOLATED — corrected value, conservative interval (D122);
2. wide per-component intervals compose into wide assignment intervals, so the CI-aware
   contender set keeps ALL assignments (a point-estimate screen would collapse to one);
3. every contender is escalated through two composed rungs — rtl (latency) and openroad
   (area_mm2, engines summed) — with component-level caching, so the four assignments' eight
   component-measurements collapse to four placements and four simulations;
4. the composite frontier carries latency at rtl fidelity and area at openroad fidelity — the
   per-layer latency/area trade-off, measured.

Skips without `openroad` (nix develop .#physical)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

import _helpers

pytestmark = pytest.mark.skipif(
    shutil.which("openroad") is None,
    reason="needs openroad on PATH (nix develop .#physical)",
)

FLUX_ROOT = Path(__file__).resolve().parents[2]




def test_the_real_latency_area_frontier_over_per_layer_assignments(tmp_path):
    import flux_ir
    import yaml
    from flux_calibration import CalibrationStore
    from flux_cli.registry import make_evaluator
    from flux_evaluator_abi import Budget, Candidate
    from flux_search_architecture.candidates import generate_width_candidates
    from flux_search_campaign import parse_objective, run_campaign_steps, slice_workload
    from flux_store import CampaignStore

    workload = _helpers.chain_workload()
    base_arch = yaml.safe_load(
        (FLUX_ROOT / "core/ir/architecture/examples/simple-npu-1d-v1.yaml").read_text())

    # -- 1. pool at widths {2, 4}, which the campaign never visits: real zigzag vs real rtl on
    # the slices (3 records: n >= 3 corrects the bias, D106; zero exact matches at campaign
    # widths keeps every campaign point extrapolated, D122)
    zigzag, rtl = make_evaluator("zigzag"), make_evaluator("rtl")
    metrics = frozenset({"latency_cycles"})
    cal_path = str(tmp_path / "cal.db")
    pool_points = [("mm0", 4), ("mm1", 4), ("mm0", 2)]
    with CalibrationStore(cal_path) as cal:
        for op_id, width in pool_points:
            sliced = slice_workload(workload, op_id)
            (cand,) = generate_width_candidates(base_arch, [width])
            zz = zigzag.evaluate(Candidate(workload=sliced, arch=cand.arch, mapping=None),
                                 Budget(), metrics)
            rr = rtl.evaluate(Candidate(workload=sliced, arch=cand.arch, mapping=None),
                              Budget(), metrics)
            cal.add_record(
                workload_hash=flux_ir.content_hash(sliced),
                arch_hash=flux_ir.content_hash(cand.arch),
                evaluator=zz.provenance.evaluator, metric="latency_cycles",
                predicted_value=zz.value_of("latency_cycles"),
                reference_value=rr.value_of("latency_cycles"),
                reference_source="rtl_sim",
            )

    # -- 2. the campaign: latency screened (calibrated), area measured only at the openroad rung
    doc = {
        "schema_version": "0.1.0",
        "id": "test/composition-area-frontier/v1",
        "objectives": [
            {"metric": "latency_cycles", "direction": "minimize"},
            {"metric": "area_mm2", "direction": "minimize", "measured_at": "escalation"},
        ],
        "mode": "pareto",
        "workload": {"inline": workload},
        "base_arch": {"inline": base_arch},
        "backends": {"screening": "zigzag", "escalation": ["rtl", "openroad"]},
        "search": {"kind": "composition_width", "widths": [8, 16]},
        "strategy": {"kind": "grid", "seed": 0},
        "budget": {"evaluations": 16},
    }
    objective = parse_objective(doc)
    store = CampaignStore(str(tmp_path / "frontier.db"))
    cid, _ = store.start_campaign(doc, objective.objective_hash)
    report = run_campaign_steps(store, cid, calibration_db_path=cal_path)

    assert report.status == "done"

    # -- 3. extrapolation kept every assignment a contender: 4 screen trials, every interval
    # honestly wide, and BOTH rungs ran all 4
    screen = store.ok_trials(cid, phase="screen")
    assert len(screen) == 4
    for t in screen:
        est = t.result.estimate_of("latency_cycles")
        assert (est.ci_high - est.ci_low) > est.value, "extrapolated point must be wide"
    escalate = store.ok_trials(cid, phase="escalate")
    by_rung: dict[int, list] = {}
    for t in escalate:
        by_rung.setdefault(t.rung_index, []).append(t)
    assert len(by_rung[0]) == 4 and len(by_rung[1]) == 4

    # -- 4. component caching held: 4 assignments x 2 engines per rung, but only 4 distinct
    # (op, width) engines exist — count the real openroad rows in the result store
    openroad_rows = [
        r for op_id in ("mm0", "mm1")
        for r in store.results.find_results(
            workload_hash=flux_ir.content_hash(slice_workload(workload, op_id)),
            evaluator_prefix="openroad",
        )
    ]
    assert len(openroad_rows) == 4, "shared engines must be placed exactly once"

    # -- 5. the composite frontier: latency at rtl fidelity, area at openroad fidelity, and the
    # trade-off is real in both directions
    frontier = report.escalated_frontier
    assert frontier, "a full-coverage two-rung escalation must produce a composite"
    for entry in frontier:
        assert entry["metrics"]["latency_cycles"]["fidelity"] == "rtl"
        assert entry["metrics"]["area_mm2"]["fidelity"] == "openroad"

    composite = {
        tuple(t["candidate"]["assignment"][op] for op in ("mm0", "mm1")): t["metrics"]
        for t in frontier
    }
    # uniform-16 is the latency end, uniform-8 the area end — both must be on the frontier
    assert (16, 16) in composite and (8, 8) in composite
    assert (composite[(16, 16)]["latency_cycles"]["value"]
            < composite[(8, 8)]["latency_cycles"]["value"])
    assert (composite[(16, 16)]["area_mm2"]["value"]
            > composite[(8, 8)]["area_mm2"]["value"])

    # the flagship comparison, on real measurements: between the two mixed assignments, the one
    # that widens the HEAVY layer is strictly faster — "spend area where the MACs are",
    # measured (whether the light-wide point survives on area is silicon's call, read below
    # from the escalation trials directly, frontier or not)
    esc_by_assignment = {}
    for t in escalate:
        key = tuple(t.candidate["assignment"][op] for op in ("mm0", "mm1"))
        esc_by_assignment.setdefault(key, {}).update({
            m: t.result.value_of(m) for m in ("latency_cycles", "area_mm2")
            if t.result.refusal_for(m) is None
        })
    heavy_wide, light_wide = esc_by_assignment[(16, 8)], esc_by_assignment[(8, 16)]
    assert heavy_wide["latency_cycles"] < light_wide["latency_cycles"]

    # What silicon actually said, pinned (areas rel 5% for placer noise, cycles exact): the two
    # mixed assignments are the SAME two engines swapped, so their real areas are equal — and
    # at that equal area, heavy-wide is 1.7x faster, so light-wide is off the frontier. The
    # unit suite's synthetic "spend area where the MACs are" claim, reproduced by real tools.
    assert heavy_wide["area_mm2"] == pytest.approx(light_wide["area_mm2"], rel=0.02)
    assert set(composite) == {(8, 8), (16, 8), (16, 16)}
    assert (8, 16) not in composite
    pins = {
        (8, 8): (18530.0, 802e-6),
        (16, 8): (10306.0, 1208e-6),
        (16, 16): (9266.0, 1614e-6),
    }
    for key, (cycles, area) in pins.items():
        assert composite[key]["latency_cycles"]["value"] == pytest.approx(cycles)
        assert composite[key]["area_mm2"]["value"] == pytest.approx(area, rel=0.05)
