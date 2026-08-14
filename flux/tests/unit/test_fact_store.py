"""FactStore (docs/decisions.md D250): content-addressed persistence with re-derivation as
the staleness check — real CampaignStore + real FactStore in tmp dirs, synthetic evaluator
rows with known values. The claim under test is the lifecycle: mine -> persist (idempotent)
-> recall -> verify, with all three verification outcomes exercised for real (the source
store is actually deleted, actually extended)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from flux_chia_nodes import flux_mine_knowledge, flux_recall_facts
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
from flux_knowledge_mining import FactStore, fact_id
from flux_store import CampaignStore

FLUX_ROOT = Path(__file__).resolve().parents[2]


def _result(value: float) -> Result:
    return Result(
        metrics={"latency_cycles": Estimate(value=value, ci_low=value, ci_high=value,
                                            unit="cycles", method=Method.SIMULATED)},
        validity=Validity(ok=True, checker_version="t"),
        domain=Domain(in_domain=True),
        bottleneck=Bottleneck(limiter=Limiter.COMPUTE),
        provenance=Provenance(evaluator="rtl@1", inputs={}),
        escalation=Escalation(recommended=False),
    )


def _make_campaign(tmp_path, name="camp"):
    from flux_search_campaign import parse_objective

    base_arch = yaml.safe_load(
        (FLUX_ROOT / "core/ir/architecture/examples/simple-npu-1d-v1.yaml").read_text())
    doc = {
        "schema_version": "0.1.0",
        "id": f"test/facts/{name}/v1",
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
    path = str(tmp_path / f"{name}.db")
    store = CampaignStore(path)
    cid, _ = store.start_campaign(doc, parse_objective(doc).objective_hash)

    def esc(width, value, seq_rung=0):
        seq = store.begin_trial(
            cid, phase="escalate", candidate={"width": width},
            candidate_key=f'{{"width": {width}}}', workload_hash="wh",
            arch_hash=f"ah{width}", mapping_hash=None, strategy_kind="grid", seed=0,
            deterministic=True, rung="rtl", rung_index=seq_rung)
        store.complete_trial(cid, seq, status="ok", result=_result(value), error=None,
                             wall_clock_s=0.1)

    esc(8, 330.0)
    esc(16, 165.0)
    store.set_status(cid, "done")
    return path, cid, store


def test_the_lifecycle_mine_persist_recall_verify_intact(tmp_path):
    camp_path, cid, _ = _make_campaign(tmp_path)
    facts_db = str(tmp_path / "facts.db")

    mined = flux_mine_knowledge(campaign_db_paths=[camp_path], facts_db_path=facts_db)
    assert mined["fact_ids"] and len(mined["fact_ids"]) == len(mined["facts"])

    # idempotent: re-mining the unchanged store adds nothing
    again = flux_mine_knowledge(campaign_db_paths=[camp_path], facts_db_path=facts_db)
    assert again["fact_ids"] == mined["fact_ids"]
    with FactStore(facts_db) as store:
        assert len(store.facts()) == len(set(mined["fact_ids"]))

    recalled = flux_recall_facts(facts_db, kind="measured_point", verify=True)
    (entry,) = recalled["facts"]
    assert "330" in entry["fact"]["statement"]
    assert entry["verification"] == "intact"
    assert entry["id"] == fact_id(entry["fact"])


def test_filters_recall_by_kind_and_statement_substring(tmp_path):
    camp_path, cid, _ = _make_campaign(tmp_path)
    facts_db = str(tmp_path / "facts.db")
    flux_mine_knowledge(campaign_db_paths=[camp_path], facts_db_path=facts_db)

    ratios = flux_recall_facts(facts_db, kind="observed_ratio")
    assert len(ratios["facts"]) == 1  # the 8->16 doubling
    by_text = flux_recall_facts(facts_db, contains="DOUBLING WIDTH")  # case-insensitive
    assert len(by_text["facts"]) == 1
    assert by_text["facts"][0]["fact"]["kind"] == "observed_ratio"
    nothing = flux_recall_facts(facts_db, contains="no such statement")
    assert nothing["facts"] == []


def test_a_deleted_source_store_makes_facts_dangling(tmp_path):
    camp_path, cid, _ = _make_campaign(tmp_path)
    facts_db = str(tmp_path / "facts.db")
    flux_mine_knowledge(campaign_db_paths=[camp_path], facts_db_path=facts_db)

    Path(camp_path).unlink()  # the source is really gone
    recalled = flux_recall_facts(facts_db, verify=True)
    assert recalled["facts"]
    assert all(e["verification"] == "dangling" for e in recalled["facts"])


def test_new_evidence_supersedes_the_old_statement(tmp_path):
    """The honest staleness case: the source store still exists but gained a trial, so the
    measured-point fact's statement is no longer what mining derives — the old fact must
    read `superseded`, not `intact`."""
    camp_path, cid, store = _make_campaign(tmp_path)
    facts_db = str(tmp_path / "facts.db")
    flux_mine_knowledge(campaign_db_paths=[camp_path], facts_db_path=facts_db)

    # the campaign measures one more point after persistence
    seq = store.begin_trial(
        cid, phase="escalate", candidate={"width": 32}, candidate_key='{"width": 32}',
        workload_hash="wh", arch_hash="ah32", mapping_hash=None, strategy_kind="grid",
        seed=0, deterministic=True, rung="rtl", rung_index=0)
    store.complete_trial(cid, seq, status="ok", result=_result(82.0), error=None,
                         wall_clock_s=0.1)

    recalled = flux_recall_facts(facts_db, kind="measured_point", verify=True)
    (entry,) = recalled["facts"]
    assert entry["verification"] == "superseded"
    # persisting the re-mined facts stores the NEW statement alongside (new content id)
    mined = flux_mine_knowledge(campaign_db_paths=[camp_path], facts_db_path=facts_db)
    fresh = flux_recall_facts(facts_db, kind="measured_point", verify=True)
    assert len(fresh["facts"]) == 2
    assert {e["verification"] for e in fresh["facts"]} == {"superseded", "intact"}
