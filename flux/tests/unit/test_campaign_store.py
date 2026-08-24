"""CampaignStore bookkeeping (docs/decisions.md D217): trial-transaction atomicity, the derived
ledger, interrupted-trial classification, resume refusals. Synthetic Results are fine — every
claim here is about the store's own arithmetic and transactions, not about any evaluator."""

from __future__ import annotations

import pytest
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
from flux_store import CampaignStore, CampaignStoreError


def _result(cycles: float = 100.0, usd: float | None = None) -> Result:
    return Result(
        metrics={
            "latency_cycles": Estimate(
                value=cycles, ci_low=cycles, ci_high=cycles, unit="cycles", method=Method.ANALYTIC
            )
        },
        validity=Validity(ok=True, checker_version="test"),
        domain=Domain(in_domain=False),
        bottleneck=Bottleneck(limiter=Limiter.COMPUTE),
        provenance=Provenance(evaluator="test@0", inputs={}, usd_cost=usd),
        escalation=Escalation(recommended=False),
    )


_OBJECTIVE_DOC = {
    "schema_version": "0.1.0",
    "id": "t/v1",
    "objectives": [{"metric": "latency_cycles", "direction": "minimize"}],
    "mode": "pareto",
    "workload": {"ref": "w"},
    "base_arch": {"ref": "a"},
    "backends": {"screening": "zigzag"},
    "search": {"kind": "architecture_width", "widths": [4, 8]},
    "strategy": {"kind": "grid"},
    "budget": {"evaluations": 4},
}


@pytest.fixture
def store(tmp_path):
    with CampaignStore(str(tmp_path / "c.db")) as s:
        yield s


def _start(store) -> str:
    import flux_ir

    cid, created = store.start_campaign(_OBJECTIVE_DOC, flux_ir.content_hash(_OBJECTIVE_DOC))
    assert created
    return cid


def _begin(store, cid, key="w4", phase="screen", **kw):
    return store.begin_trial(
        cid, phase=phase, candidate={"width": 4}, candidate_key=key,
        workload_hash="wh", arch_hash="ah", strategy_kind="grid", **kw,
    )


def test_restarting_the_same_objective_resumes_not_forks(store):
    cid = _start(store)
    import flux_ir

    cid2, created2 = store.start_campaign(_OBJECTIVE_DOC, flux_ir.content_hash(_OBJECTIVE_DOC))
    assert cid2 == cid and not created2
    # exactly one 'started' event — the second call added nothing
    assert [e["kind"] for e in store.events(cid)] == ["started"]


def test_trial_completion_is_one_transaction_with_the_result(store):
    cid = _start(store)
    seq = _begin(store, cid)
    result_id = store.complete_trial(
        cid, seq, status="ok", result=_result(), error=None, wall_clock_s=1.5
    )
    assert result_id is not None
    # the trial references a result row that genuinely exists in the SAME database
    trial = store.trials(cid)[0]
    assert trial.result_id == result_id
    assert store.results.get_result(result_id)["evaluator"] == "test@0"
    assert trial.result is not None and trial.result.value_of("latency_cycles") == 100.0


def test_double_completion_is_a_loud_bug_not_a_silent_overwrite(store):
    cid = _start(store)
    seq = _begin(store, cid)
    store.complete_trial(cid, seq, status="ok", result=_result(), error=None, wall_clock_s=1.0)
    with pytest.raises(CampaignStoreError, match="double completion"):
        store.complete_trial(cid, seq, status="ok", result=_result(), error=None, wall_clock_s=1.0)


def test_running_rows_classify_as_interrupted_and_free_their_candidate(store):
    cid = _start(store)
    _begin(store, cid, key="w4")
    seq2 = _begin(store, cid, key="w8")
    store.complete_trial(cid, seq2, status="ok", result=_result(), error=None, wall_clock_s=1.0)

    assert store.classify_interrupted(cid) == 1
    # the interrupted candidate is re-proposable; the completed one is not
    assert store.visited_keys(cid) == {"w8"}
    assert any(e["kind"] == "interrupted_trials_found" for e in store.events(cid))
    # idempotent: a second pass finds nothing
    assert store.classify_interrupted(cid) == 0


def test_the_ledger_is_derived_and_cache_hits_are_free(store):
    from flux_store import BudgetGrant

    budget = BudgetGrant(evaluations=4)
    cid = _start(store)

    for i, (status, hit) in enumerate(
        [("ok", False), ("ok", True), ("error", False), ("refused", False)]
    ):
        seq = _begin(store, cid, key=f"k{i}")
        store.complete_trial(
            cid, seq, status=status, result=_result() if status == "ok" else None,
            error=None if status == "ok" else "x", wall_clock_s=2.0, cache_hit=hit,
        )

    spent = store.spent(cid)
    # 4 trials, but the cache hit spent no real evaluator call: 3 evaluations
    assert spent["evaluations"] == 3
    assert spent["wall_clock_s"] == pytest.approx(8.0)
    # no backend reported usd: unknown stays None, never 0.0
    assert spent["usd"] is None

    remaining = store.remaining(cid, budget)
    assert remaining.evaluations == 4 - 3
    assert not remaining.exhausted

    # top-up arrives as an event, and the derived ledger sees it without any stored counter
    store.append_event(cid, "topped_up", {"added": {"evaluations": 10}})
    assert store.remaining(cid, budget).evaluations == 11


def test_budget_exhaustion_latches_at_zero(store):
    from flux_store import BudgetGrant

    cid = _start(store)
    seq = _begin(store, cid)
    store.complete_trial(cid, seq, status="ok", result=_result(), error=None, wall_clock_s=1.0)
    remaining = store.remaining(cid, BudgetGrant(evaluations=1))
    assert remaining.evaluations == 0 and remaining.exhausted


def test_usd_spend_is_charged_when_a_backend_reports_it(store):
    from flux_store import BudgetGrant

    cid = _start(store)
    seq = _begin(store, cid)
    store.complete_trial(
        cid, seq, status="ok", result=_result(usd=0.75), error=None, wall_clock_s=1.0
    )
    remaining = store.remaining(cid, BudgetGrant(usd=1.0))
    assert remaining.usd == pytest.approx(0.25)
    assert not remaining.exhausted


def test_a_record_written_before_d524_loses_the_nine_columns_on_open_and_says_so(tmp_path):
    """D524 (Cedric: "migrate it"): a v1 file carries the accelerator era's nine trial
    columns; opening it drops them in place, marks the file schema 2, and puts a `migrated`
    event on each campaign; the trials read back whole; a v2 file has nothing to drop."""
    import sqlite3

    from flux_store import DROPPED_COLUMNS, SCHEMA_VERSION, CampaignStore

    db = str(tmp_path / "v1.db")
    with CampaignStore(db) as store:                      # a v2 file, then made to look like v1
        cid = _start(store)
        seq = _begin(store, cid)
        store.complete_trial(cid, seq, status="ok", result=_result(), error=None, wall_clock_s=1.0)
    con = sqlite3.connect(db)
    for col, typ in (("mapping_hash", "TEXT"), ("seed", "INTEGER"), ("deterministic", "INTEGER NOT NULL DEFAULT 1"),
                     ("llm_model", "TEXT"), ("prompt_sha256", "TEXT"), ("response_sha256", "TEXT"),
                     ("used_fallback", "INTEGER"), ("fallback_reason", "TEXT"), ("stage_index", "INTEGER")):
        con.execute(f"ALTER TABLE trials ADD COLUMN {col} {typ}")
    con.execute("PRAGMA user_version = 0")
    con.commit()
    con.close()
    with CampaignStore(db) as store:
        assert store.upgraded == list(DROPPED_COLUMNS) and store.schema_version() == SCHEMA_VERSION
        columns = {r[1] for r in store._conn.execute("PRAGMA table_info(trials)")}
        assert not (columns & set(DROPPED_COLUMNS)) and {"stage", "arch_hash", "usd_cost", "cache_hit"} <= columns
        (t,) = store.trials(cid)
        assert t.candidate == {"width": 4} and t.status == "ok" and t.result is not None
        kinds = [e["kind"] for e in store.events(cid)]
        assert "migrated" in kinds
        detail = next(e["detail"] for e in store.events(cid) if e["kind"] == "migrated")
        assert detail == {"schema": SCHEMA_VERSION, "dropped": list(DROPPED_COLUMNS)}
    with CampaignStore(db) as store:
        assert store.upgraded == [] and store.schema_version() == SCHEMA_VERSION, "idempotent"


def test_a_campaign_is_keyed_by_its_document_name_and_a_hashed_one_can_be_renamed_to_it(tmp_path):
    """D524: `Records(name=...)` opens the campaign under the problem's name; `rename_campaign`
    moves a campaign the loop opened under an objective hash -- with every trial and event --
    under that name, and the document then resumes it."""
    from flux_records import Records
    from flux_store import CampaignStore, CampaignStoreError

    db = str(tmp_path / "named.db")
    hashed = Records(db, objective={"study": "nlu", "ops": ["exp"]})
    assert hashed.campaign_id != "nlu" and len(hashed.campaign_id) == 64
    hashed.trial({"op": "exp", "name": "a"}, "a@screen", stage="screen", strategy="loop", metrics={"fmax_mhz": 1.0})
    hashed.store.close()
    with CampaignStore(db) as store:
        with pytest.raises(CampaignStoreError, match="no such campaign"):
            store.rename_campaign("nope", "nlu")
        store.rename_campaign(hashed.campaign_id, "nlu")
        assert [r["campaign_id"] for r in store.list_campaigns()] == ["nlu"]
        assert store.trials("nlu") and store.trials(hashed.campaign_id) == []
        assert any(e["kind"] == "renamed" and e["detail"] == {"from": hashed.campaign_id, "to": "nlu"} for e in store.events("nlu"))
        with pytest.raises(CampaignStoreError, match="must be a different"):
            store.rename_campaign("nlu", "nlu")
        other, _ = store.start_campaign({"study": "other"}, "otherhash")
        with pytest.raises(CampaignStoreError, match="already exists"):
            store.rename_campaign(other, "nlu")
    named = Records(db, objective={"study": "nlu", "ops": ["exp"]}, name="nlu")
    assert named.campaign_id == "nlu" and named.resumed
    named.store.close()
    # a document names its campaign (D524): the problem's `campaign_name` is the record's id
    from flux_loop import LoopRequest, PromptProblem, TaskSpec

    prob = PromptProblem(TaskSpec.from_dict({"id": "t", "statement": "s", "gate": {"test": ["true"]},
                                             "campaign": {"name": "nlu", "study": "nlu"}}))
    req = LoopRequest(db=db)
    assert prob.campaign_name(req) == "nlu" and prob.objective(req) == {"study": "nlu"}
    assert prob.open_records(req, lambda _m: None).campaign_id == "nlu"
