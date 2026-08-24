"""The capstone chain (docs/decisions.md D239): one sentence of prose becomes a real,
fidelity-labeled latency/area frontier over per-layer engine assignments — every stage the
standalone-agentic target names, composed and REAL end to end:

    prose --qwen--> validated composition Objective (D232 + D239's authoring seam)
          --real zigzag vs rtl--> calibration pool at never-visited widths (D237)
          --campaign--> calibrated parallel screening (D237/D238), all assignments contenders
          --rtl + openroad rungs--> the SAME pinned silicon frontier D237 measured directly.

Nothing here is hand-authored except the sentence: the workload is ONNX-born, the objective is
LLM-authored and parser-validated, every number is a real tool's output, and the final frontier
must land on D237's independently-pinned values — the chain reproducing what direct
construction measured is the integration claim.

Needs BOTH a local Ollama and openroad (nix develop .#physical)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

import _helpers

FLUX_ROOT = Path(__file__).resolve().parents[2]




pytestmark = [
    _helpers.requires_ollama,
    pytest.mark.skipif(shutil.which("openroad") is None,
                       reason="needs openroad on PATH (nix develop .#physical)"),
]




def test_one_sentence_becomes_the_pinned_silicon_frontier(tmp_path):
    import flux_ir
    import yaml
    from flux_calibration import CalibrationStore
    from flux_chia_nodes import flux_author_objective
    from flux_cli.registry import make_evaluator
    from flux_evaluator_abi import Budget, Candidate
    from flux_search_architecture.candidates import generate_width_candidates
    from flux_search_campaign import parse_objective, run_campaign_steps, slice_workload
    from flux_store import CampaignStore

    workload = _helpers.chain_workload()
    base_arch = yaml.safe_load(
        (FLUX_ROOT / "core/ir/architecture/examples/simple-npu-1d-v1.yaml").read_text())

    # -- 1. the sentence
    prose = (
        "This model is a 2-layer chain; size each layer's own engine separately "
        "(per-layer engine widths, from widths 8 and 16). Minimize latency_cycles and "
        "real placed silicon area_mm2 — area is measured only at the escalation rungs. "
        "Screen with zigzag, then escalate through rtl and then openroad. "
        "Spend at most 16 evaluations."
    )
    authored = flux_author_objective(prose, workload, base_arch)
    assert authored.success, authored.error
    doc = authored.objective

    # The strongly-cued clauses must land (the same variance discipline as the D232 test:
    # these extracted reliably on real runs; anything weaker is asserted where it is used).
    assert doc["search"]["kind"] == "composition_width"
    assert sorted(doc["search"]["widths"]) == [8, 16]
    metrics = {o["metric"]: o for o in doc["objectives"]}
    assert "latency_cycles" in metrics and "area_mm2" in metrics
    assert metrics["area_mm2"].get("measured_at") == "escalation"
    assert doc["backends"]["screening"] == "zigzag"
    assert doc["backends"]["escalation"] == ["rtl", "openroad"]
    assert doc["provenance"]["prose"] == prose

    # -- 2. calibration pool at widths the campaign never visits (D237's mechanism): real
    # zigzag vs real rtl on the slices
    zigzag, rtl = make_evaluator("zigzag"), make_evaluator("rtl")
    pool_metrics = frozenset({"latency_cycles"})
    cal_path = str(tmp_path / "cal.db")
    with CalibrationStore(cal_path) as cal:
        for op_id, width in [("mm0", 4), ("mm1", 4), ("mm0", 2)]:
            sliced = slice_workload(workload, op_id)
            (cand,) = generate_width_candidates(base_arch, [width])
            zz = zigzag.evaluate(Candidate(workload=sliced, arch=cand.arch, mapping=None),
                                 Budget(), pool_metrics)
            rr = rtl.evaluate(Candidate(workload=sliced, arch=cand.arch, mapping=None),
                              Budget(), pool_metrics)
            cal.add_record(
                workload_hash=flux_ir.content_hash(sliced),
                arch_hash=flux_ir.content_hash(cand.arch),
                evaluator=zz.provenance.evaluator, metric="latency_cycles",
                predicted_value=zz.value_of("latency_cycles"),
                reference_value=rr.value_of("latency_cycles"),
                reference_source="rtl_sim",
            )

    # -- 3. the campaign the authored document describes, with calibrated PARALLEL screening
    objective = parse_objective(doc)
    store = CampaignStore(str(tmp_path / "capstone.db"))
    cid, _ = store.start_campaign(doc, objective.objective_hash)
    report = run_campaign_steps(store, cid, calibration_db_path=cal_path,
                                screening_parallelism=4)
    assert report.status == "done"

    # every assignment stayed a contender (wide extrapolated CIs) and both rungs ran all four
    assert len(store.ok_trials(cid, phase="screen")) == 4
    escalate = store.ok_trials(cid, phase="escalate")
    assert sorted(
        (t.rung_index, tuple(t.candidate["assignment"][op] for op in ("mm0", "mm1")))
        for t in escalate
    ) == sorted((r, a) for r in (0, 1)
                for a in [(8, 8), (8, 16), (16, 8), (16, 16)])

    # -- 4. the chain lands on D237's independently-measured pins: same frontier, same
    # exclusion, same numbers (areas rel 5% for placer noise)
    composite = {
        tuple(t["candidate"]["assignment"][op] for op in ("mm0", "mm1")): t["metrics"]
        for t in report.escalated_frontier
    }
    assert set(composite) == {(8, 8), (16, 8), (16, 16)}  # (8,16) dominated at equal area
    pins = {
        (8, 8): (18530.0, 802e-6),
        (16, 8): (10306.0, 1208e-6),
        (16, 16): (9266.0, 1614e-6),
    }
    for key, (cycles, area) in pins.items():
        assert composite[key]["latency_cycles"]["fidelity"] == "rtl"
        assert composite[key]["latency_cycles"]["value"] == pytest.approx(cycles)
        assert composite[key]["area_mm2"]["fidelity"] == "openroad"
        assert composite[key]["area_mm2"]["value"] == pytest.approx(area, rel=0.05)
