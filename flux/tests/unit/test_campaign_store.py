"""CampaignStore bookkeeping (D217): trial-transaction atomicity, the derived ledger,
interrupted-trial classification, resume refusals. Synthetic Results only."""

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
    row = store._conn.execute("SELECT evaluator FROM results WHERE id = ?", (result_id,)).fetchone()
    assert row[0] == "test@0"
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
    assert any(e["kind"] == "interrupted_trials_found" for e in store.events(cid))
    # idempotent: a second pass finds nothing
    assert store.classify_interrupted(cid) == 0


def test_a_campaign_is_keyed_by_its_document_name(tmp_path):
    """`Records(name=...)` opens the campaign under the problem's name, beside a hash-named one
    (D524)."""
    from flux_records import Records
    db = str(tmp_path / "named.db")
    hashed = Records(db, objective={"study": "nlu", "ops": ["exp"]})
    assert hashed.campaign_id != "nlu" and len(hashed.campaign_id) == 64
    hashed.trial({"op": "exp", "name": "a"}, "a@screen", stage="screen", strategy="loop", metrics={"fmax_mhz": 1.0})
    hashed.store.close()
    named = Records(db, objective={"study": "nlu", "ops": ["exp"]}, name="nlu")
    assert named.campaign_id == "nlu" and not named.resumed
    assert {r["campaign_id"] for r in named.store.list_campaigns()} == {"nlu", hashed.campaign_id}
    named.store.close()
    assert Records(db, objective={"study": "nlu", "ops": ["exp"]}, name="nlu").resumed
    # a document names its campaign (D524): the problem's `campaign_name` is the record's id
    from flux_loop import LoopRequest, PromptProblem, TaskSpec

    prob = PromptProblem(TaskSpec.from_dict({"id": "nlu", "statement": "s", "flow": {"test": {"test": ["true"]}}}))
    req = LoopRequest(db=db)
    assert prob.campaign_name(req) == "nlu" and prob.objective(req) == {"study": "nlu"}
    assert prob.open_records(req, lambda _m: None).campaign_id == "nlu"
