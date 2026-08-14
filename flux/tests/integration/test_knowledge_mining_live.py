"""Knowledge mining over stores REAL tools produced (docs/decisions.md D243): a calibration
pool (real ZigZag vs real Verilator) and a calibrated campaign with full rtl escalation, then
`flux_mine_knowledge` — and every mined fact is checked against numbers this suite pins
independently (the D231 wide-proj family). The mining layer adds wording, scope and pointers;
it must not add or shift a single number."""

from __future__ import annotations

import pytest

import _helpers




def test_mined_facts_match_the_independently_pinned_numbers(tmp_path):
    from pathlib import Path

    import flux_ir
    import yaml
    from flux_calibration import CalibrationStore
    from flux_chia_nodes import flux_mine_knowledge
    from flux_cli.registry import make_evaluator
    from flux_evaluator_abi import Budget, Candidate
    from flux_search_architecture.candidates import generate_width_candidates
    from flux_search_campaign import parse_objective, run_campaign_steps
    from flux_store import CampaignStore

    flux_root = Path(__file__).resolve().parents[2]
    workload = _helpers.wide_proj_workload()
    base_arch = yaml.safe_load(
        (flux_root / "core/ir/architecture/examples/simple-npu-1d-v1.yaml").read_text())

    # -- calibration pool at widths the campaign never visits (keeps every campaign point
    # extrapolated -> every width a contender -> full rtl escalation, the D237 mechanism)
    zigzag, rtl = make_evaluator("zigzag"), make_evaluator("rtl")
    metrics = frozenset({"latency_cycles"})
    cal_path = str(tmp_path / "cal.db")
    with CalibrationStore(cal_path) as cal:
        for width in (2, 4, 64):
            (cand,) = generate_width_candidates(base_arch, [width])
            zz = zigzag.evaluate(Candidate(workload=workload, arch=cand.arch, mapping=None),
                                 Budget(), metrics)
            rr = rtl.evaluate(Candidate(workload=workload, arch=cand.arch, mapping=None),
                              Budget(), metrics)
            cal.add_record(
                workload_hash=flux_ir.content_hash(workload),
                arch_hash=flux_ir.content_hash(cand.arch),
                evaluator=zz.provenance.evaluator, metric="latency_cycles",
                predicted_value=zz.value_of("latency_cycles"),
                reference_value=rr.value_of("latency_cycles"),
                reference_source="rtl_sim",
            )

    doc = {
        "schema_version": "0.1.0",
        "id": "test/mining-live/v1",
        "objectives": [{"metric": "latency_cycles", "direction": "minimize"}],
        "mode": "pareto",
        "workload": {"inline": workload},
        "base_arch": {"inline": base_arch},
        "backends": {"screening": "zigzag", "escalation": ["rtl"]},
        "search": {"kind": "architecture_width", "widths": [8, 16, 32]},
        "strategy": {"kind": "grid", "seed": 0},
        "budget": {"evaluations": 16},
    }
    objective = parse_objective(doc)
    camp_path = str(tmp_path / "camp.db")
    store = CampaignStore(camp_path)
    cid, _ = store.start_campaign(doc, objective.objective_hash)
    report = run_campaign_steps(store, cid, calibration_db_path=cal_path)
    assert report.status == "done"
    assert len(store.ok_trials(cid, phase="escalate")) == 3  # all widths were contenders

    mined = flux_mine_knowledge(
        campaign_db_paths=[camp_path], calibration_db_paths=[cal_path])
    by_kind: dict[str, list] = {}
    for f in mined["facts"]:
        by_kind.setdefault(f["kind"], []).append(f)
    assert mined["skipped"] == []

    # -- estimator bias: the family's real ~3x, over-predicted, 3 points, record pointers
    (bias,) = by_kind["estimator_bias"]
    assert "over-predicted latency_cycles" in bias["statement"]
    assert all(2.9 < r < 3.1 for r in bias["evidence"]["ratios"])
    assert "3 measured (workload, arch) point(s)" in bias["statement"]
    assert len(bias["pointers"]["record_ids"]) == 3
    assert "unmeasured point" in bias["not_established"]

    # -- measured points: the rtl pins this suite already owns (D231's family), verbatim
    (points,) = by_kind["measured_point"]
    values = {p["candidate"]["width"]: p["value"] for p in points["evidence"]["points"]}
    assert values == {8: pytest.approx(32833.0), 16: pytest.approx(16417.0),
                      32: pytest.approx(8209.0)}
    assert all(p["evaluator"].startswith("rtl") for p in points["evidence"]["points"])

    # -- observed ratios: both doublings at ~0.50x, each anchored to its two trial rows;
    # NOT presented as a law
    ratios = {tuple(f["evidence"]["knob_values"]): f for f in by_kind["observed_ratio"]}
    assert set(ratios) == {(8.0, 16.0), (16.0, 32.0)}
    for f in ratios.values():
        assert f["evidence"]["ratio"] == pytest.approx(0.5, abs=0.01)
        assert len(f["pointers"]["trial_seqs"]) == 2
        assert "scaling law" in f["not_established"]

    # -- frontier outcome: width 32 at rtl fidelity with the pinned value
    (outcome,) = by_kind["frontier_outcome"]
    assert "'test/mining-live/v1'" in outcome["statement"]
    assert "latency_cycles=8209 (rtl)" in outcome["statement"]
    (entry,) = outcome["evidence"]["frontier"]
    assert entry["candidate"]["width"] == 32

    # no refusals happened, so no refusal facts — mining reports what exists, nothing else
    assert "refusal_pattern" not in by_kind