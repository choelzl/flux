"""Knowledge mining (docs/decisions.md D243) — REAL stores in tmp dirs, synthetic rows with
known values, and the claims under test are the module's own anti-misleading rules: measured
language, scope + not_established on every fact, pointers to the exact rows, caveated records
never pooled, screen estimates never presented as measurements, non-done campaigns counted
not dropped, exact refusal messages never normalized."""

from __future__ import annotations

import hashlib
import json

import pytest
import yaml
from pathlib import Path

from flux_evaluator_abi import (
    Bottleneck,
    Domain,
    Escalation,
    Estimate,
    Limiter,
    Method,
    Provenance,
    Result,
    Validity,
)
from flux_records.mining import (
    mine_estimator_bias,
    mine_knowledge,
    mine_observed_ratios,
    mine_refusal_patterns,
)
from flux_store import CampaignStore

FLUX_ROOT = Path(__file__).resolve().parents[2]


def _result(value: float, *, metric="latency_cycles", unit="cycles",
            method=Method.SIMULATED, evaluator="rtl@1") -> Result:
    return Result(
        metrics={metric: Estimate(value=value, ci_low=value, ci_high=value,
                                  unit=unit, method=method)},
        validity=Validity(ok=True, checker_version="test"),
        domain=Domain(in_domain=True),
        bottleneck=Bottleneck(limiter=Limiter.COMPUTE),
        provenance=Provenance(evaluator=evaluator, inputs={}),
        escalation=Escalation(recommended=False),
    )


# -- estimator bias -------------------------------------------------------------------------


def test_bias_facts_report_ranges_with_pointers_and_never_pool_caveated_records(tmp_path):
    from flux_calibration import CalibrationStore

    path = str(tmp_path / "cal.db")
    with CalibrationStore(path) as cal:
        ids = [
            cal.add_record(workload_hash=f"w{i}", arch_hash=f"a{i}", evaluator="zigzag@9",
                           metric="latency_cycles", predicted_value=p, reference_value=r,
                           reference_source="rtl_sim")
            for i, (p, r) in enumerate([(300.0, 100.0), (150.0, 50.0), (90.0, 30.1)])
        ]
        caveated_id = cal.add_record(
            workload_hash="wX", arch_hash="aX", evaluator="zigzag@9",
            metric="latency_cycles", predicted_value=1000.0, reference_value=10.0,
            reference_source="rtl_sim", caveat="pool does not describe this point")

    (fact,) = mine_estimator_bias(path)
    # measured language with the exact observed range — the 100x caveated outlier NOT pooled
    assert "over-predicted" in fact.statement
    assert "2.990x-3.000x" in fact.statement
    assert "3 measured (workload, arch) point(s)" in fact.statement
    assert fact.pointers["record_ids"] == ids
    assert fact.pointers["excluded_caveated_record_ids"] == [caveated_id]
    assert any("caveated" in c for c in fact.caveats)
    # the anti-overgeneralization line is a FIELD, not documentation
    assert "unmeasured point" in fact.not_established


def test_below_threshold_families_carry_the_d106_caveat(tmp_path):
    from flux_calibration import CalibrationStore

    path = str(tmp_path / "cal.db")
    with CalibrationStore(path) as cal:
        cal.add_record(workload_hash="w", arch_hash="a", evaluator="e@1", metric="m",
                       predicted_value=2.0, reference_value=1.0, reference_source="rtl_sim")
    (fact,) = mine_estimator_bias(path)
    assert any("below the correction threshold" in c for c in fact.caveats)


def test_a_range_spanning_one_makes_no_direction_claim(tmp_path):
    from flux_calibration import CalibrationStore

    path = str(tmp_path / "cal.db")
    with CalibrationStore(path) as cal:
        for i, (p, r) in enumerate([(90.0, 100.0), (110.0, 100.0)]):
            cal.add_record(workload_hash=f"w{i}", arch_hash=f"a{i}", evaluator="e@1",
                           metric="m", predicted_value=p, reference_value=r,
                           reference_source="rtl_sim")
    (fact,) = mine_estimator_bias(path)
    assert "predicted within" in fact.statement
    assert "over-predicted" not in fact.statement and "under-predicted" not in fact.statement


# -- campaign miners ------------------------------------------------------------------------


@pytest.fixture()
def campaign(tmp_path):
    """A done campaign with: 2 ok screen trials (analytic — must NOT become measured points),
    2 ok escalate trials at width 8 and 16 (the observed-ratio pair), and 2 error trials
    sharing one exact message."""
    base_arch = yaml.safe_load(
        (FLUX_ROOT / "core/ir/architecture/examples/simple-npu-1d-v1.yaml").read_text())
    doc = {
        "schema_version": "0.1.0",
        "id": "test/mining/v1",
        "objectives": [{"metric": "latency_cycles", "direction": "minimize"}],
        "mode": "pareto",
        "workload": {"inline": {"schema_version": "0.1.0", "id": "w", "ops": [
            {"id": "op", "kind": "einsum", "expr": "B C, C K -> B K",
             "bounds": {"B": 4, "C": 32, "K": 32}}]}},
        "base_arch": {"inline": base_arch},
        "backends": {"screening": "fake", "escalation": ["rtl"]},
        "search": {"kind": "architecture_width", "widths": [8, 16]},
        "strategy": {"kind": "grid", "seed": 0},
        "budget": {"evaluations": 16},
    }
    path = str(tmp_path / "camp.db")
    store = CampaignStore(path)
    cid, _ = store.start_campaign(doc, hashlib.sha256(json.dumps(doc, sort_keys=True, default=str).encode()).hexdigest())

    def _trial(phase, width, *, status="ok", result=None, error=None, stage=None):
        seq = store.begin_trial(
            cid, phase=phase, candidate={"width": width}, candidate_key=f'{{"width": {width}}}',
            workload_hash="wh", arch_hash=f"ah{width}", strategy_kind="grid", stage=stage,
        )
        store.complete_trial(cid, seq, status=status, result=result, error=error,
                             wall_clock_s=0.1)
        return seq

    _trial("screen", 8, result=_result(1000.0, method=Method.ANALYTIC, evaluator="zigzag@9"))
    _trial("screen", 16, result=_result(500.0, method=Method.ANALYTIC, evaluator="zigzag@9"))
    esc8 = _trial("escalate", 8, result=_result(330.0), stage="rtl")
    esc16 = _trial("escalate", 16, result=_result(165.0), stage="rtl")
    err_msg = "NotExpressibleError: K=10 is not a multiple of LANES=16"
    e1 = _trial("screen", 10, status="error", error=err_msg)
    e2 = _trial("screen", 20, status="error", error=err_msg)
    store.set_status(cid, "done")
    return path, cid, {"esc": [esc8, esc16], "err": [e1, e2]}


def test_measured_points_come_only_from_escalation(campaign):
    path, cid, seqs = campaign
    mined = mine_knowledge(campaign_db_paths=[path])
    points = [f for f in mined.facts if f.kind == "measured_point"]
    (fact,) = points
    assert fact.pointers["trial_seqs"] == seqs["esc"]  # the two rtl trials, nothing analytic
    assert "Stage 'rtl' measured latency_cycles" in fact.statement
    assert "330" in fact.statement and "165" in fact.statement
    assert "1000" not in fact.statement  # the screen estimate never reads as a measurement


def test_observed_ratios_are_pairs_never_laws(campaign):
    path, cid, seqs = campaign
    (fact,) = mine_observed_ratios(path)
    assert fact.evidence["ratio"] == pytest.approx(0.5)
    assert "Doubling width 8->16 changed latency_cycles by 0.500x" in fact.statement
    assert fact.pointers["trial_seqs"] == seqs["esc"]
    assert "scaling law" in fact.not_established


def test_refusals_group_by_the_exact_message(campaign):
    path, cid, seqs = campaign
    (fact,) = mine_refusal_patterns(path)
    assert fact.evidence["message"] == "NotExpressibleError: K=10 is not a multiple of LANES=16"
    assert fact.pointers["trial_seqs"] == seqs["err"]
    assert "2 trial(s)" in fact.statement


def test_non_done_campaigns_are_counted_not_silently_dropped(campaign, tmp_path):
    path, cid, _ = campaign
    with CampaignStore(path) as store:
        store.set_status(cid, "paused")
    mined = mine_knowledge(campaign_db_paths=[path])
    assert not [f for f in mined.facts if f.kind == "frontier_outcome"]
    assert any("status 'paused'" in s for s in mined.skipped)


def test_a_done_campaign_without_a_frontier_reader_is_counted_as_skipped(campaign):
    """D521: the old campaign package's frontier reader went with it; a done campaign is an
    outcome only through an injected reader, and without one it is skipped, saying so."""
    path, cid, _ = campaign
    mined = mine_knowledge(campaign_db_paths=[path])
    assert not [f for f in mined.facts if f.kind == "frontier_outcome"]
    assert any("no frontier reader" in s and cid in s for s in mined.skipped)


def test_measured_points_are_mined_whatever_phase_the_writer_used(tmp_path):
    """Mining used to read only `phase="escalate"`, the campaign runner's word; the loop
    and the applications write through `flux_records` under their own phase names
    (`search`, `confirm`, ...), so a whole class of stores mined nothing. The
    discriminator is the ABI `Method` on the estimate, not the phase."""
    from flux_records import Records

    db = str(tmp_path / "loop.db")
    r = Records(db, objective={"study": "nlu", "seed": 3})
    r.trial({"op": "recip", "name": "lut"}, "lut@admit", stage="admit", strategy="loop",
            metrics={"error_rate": 0.0}, analytic=False, evaluator="nlu@exhaustive")
    r.trial({"op": "recip", "name": "guess"}, "guess@screen", stage="screen", strategy="loop",
            metrics={"fmax_mhz": 120.0})          # analytic: a prediction, not a point
    mined = mine_knowledge(campaign_db_paths=[db])
    points = [f for f in mined.facts if f.kind == "measured_point"]
    assert [(f.pointers["stage"], f.pointers["metric"]) for f in points] == [("admit", "error_rate")]
    assert points[0].evidence["points"][0]["method"] == "simulated"


def test_frontier_outcomes_read_through_an_injected_reader(campaign):
    """Mining is the mentor's; the campaign's frontier rendering is the orchestrator's.
    The reader is injected (D428): a caller can supply its own, and without any reader
    the campaign is counted as skipped rather than mined wrongly."""
    from flux_records.mining.mining import FrontierOutcome, mine_frontier_outcomes

    path, cid, _ = campaign

    def reader(store, campaign_id):
        assert campaign_id == cid
        return FrontierOutcome(
            entries=[{"candidate": {"width": 16}, "metrics": {"latency_cycles": {"value": 165.0, "fidelity": "rtl"}}}],
            fidelity_note="injected", objective_id="obj/1", mode="pareto",
            objectives=[("minimize", "latency_cycles")])

    facts, skipped = mine_frontier_outcomes(path, frontier_reader=reader)
    (fact,) = facts
    assert "'obj/1'" in fact.statement and "[injected]" in fact.statement and not skipped
    facts, skipped = mine_frontier_outcomes(path, frontier_reader=lambda s, c: None)
    assert not facts and any("declined" in s for s in skipped)
