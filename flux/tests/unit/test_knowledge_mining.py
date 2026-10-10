"""Knowledge mining (D243) on real stores in tmp dirs with synthetic rows. Under test are the
anti-misleading rules: measured language, scope and not_established on every fact, pointers to
exact rows, caveated records never pooled, screen estimates never shown as measurements,
non-done campaigns counted not dropped, exact refusal messages kept."""

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


@pytest.fixture()
def campaign(tmp_path):
    """A done campaign with 2 analytic screen trials, 2 escalate trials at width 8 and 16, and 2 errors sharing one message."""
    base_arch = {"schema_version": "0.1.0", "id": "simple-npu-1d-v1",
                 "hierarchy": [{"level": "pe_array", "class": "compute", "attrs": {"dims": {"x": 16}}}]}
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


def test_measured_points_are_mined_whatever_phase_the_writer_used(tmp_path):
    """Measured points are found by the estimate's ABI `Method`, not the phase name."""
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
