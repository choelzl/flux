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
        workload_hash="wh", arch_hash="ah", mapping_hash=None,
        strategy_kind="grid", seed=0, deterministic=True, **kw,
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
    from flux_search_campaign import parse_objective

    objective = parse_objective(_OBJECTIVE_DOC)
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

    remaining = store.remaining(cid, objective.budget)
    assert remaining.evaluations == 4 - 3
    assert not remaining.exhausted

    # top-up arrives as an event, and the derived ledger sees it without any stored counter
    store.append_event(cid, "topped_up", {"added": {"evaluations": 10}})
    assert store.remaining(cid, objective.budget).evaluations == 11


def test_budget_exhaustion_latches_at_zero(store):
    from flux_search_campaign import parse_objective

    objective = parse_objective({**_OBJECTIVE_DOC, "budget": {"evaluations": 1}})
    cid = _start(store)
    seq = _begin(store, cid)
    store.complete_trial(cid, seq, status="ok", result=_result(), error=None, wall_clock_s=1.0)
    remaining = store.remaining(cid, objective.budget)
    assert remaining.evaluations == 0 and remaining.exhausted


def test_usd_spend_is_charged_when_a_backend_reports_it(store):
    from flux_search_campaign import parse_objective

    objective = parse_objective({**_OBJECTIVE_DOC, "budget": {"usd": 1.0}})
    cid = _start(store)
    seq = _begin(store, cid)
    store.complete_trial(
        cid, seq, status="ok", result=_result(usd=0.75), error=None, wall_clock_s=1.0
    )
    remaining = store.remaining(cid, objective.budget)
    assert remaining.usd == pytest.approx(0.25)
    assert not remaining.exhausted


def test_escalation_bookkeeping_is_by_rung_index_not_name(store):
    """Two rungs may share a backend name (the D112 ["rtl", "rtl"] lesson) — idempotence keys on
    the position."""
    cid = _start(store)
    seq = _begin(store, cid, key="w4", phase="escalate", rung="rtl", rung_index=0)
    store.complete_trial(cid, seq, status="ok", result=_result(), error=None, wall_clock_s=1.0)
    assert store.already_escalated(cid, "w4", 0)
    assert not store.already_escalated(cid, "w4", 1)
