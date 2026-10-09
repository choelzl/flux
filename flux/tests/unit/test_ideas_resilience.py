"""Notebook edge cases against real records, agent processes and loop passes."""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from flux_llm import ScriptedProposer
from flux_loop import LoopState, PromptProblem, TaskSpec, request_for, run_loop
from flux_loop.ideas import bind, capture, context, notebook, proposals, propose, record_failure
from flux_loop.types import Candidate
from flux_records import Records
from flux_store import CampaignStore

from test_ideas import FUTURE, IDEA, state, trial


@pytest.fixture()
def st(tmp_path):
    value = state(tmp_path)
    yield value
    value.records.close("paused")
    value.records.store.close()


def test_repeating_a_hypothesis_deduplicates_it_but_different_parts_and_tests_do_not(st):
    first = propose(st, "core", IDEA)
    assert propose(st, "core", {**IDEA, "title": "  " + IDEA["title"] + "  "}) == first
    other_part = propose(st, "memory", IDEA)
    other_test = propose(st, "core", {**IDEA, "test": "Measure memory usage."})
    assert len({first, other_part, other_test}) == 3
    assert len(notebook(st.records.store, st.records.campaign_id)["ideas"]) == 3
    assert len([e for e in st.records.store.events(st.records.campaign_id) if e["kind"] == "idea"]) == 3


def test_another_campaign_cannot_select_or_read_a_hypothesis(st):
    ident = propose(st, None, IDEA)
    other = Records(st.request.db, {"study": "other"}, name="other")
    try:
        fresh = LoopState(request=st.request, records=other, proposer=None, feedback=None, say=lambda _: None)
        with pytest.raises(ValueError, match="unknown idea"):
            propose(fresh, None, {"id": ident})
        assert context(fresh, None) == ""
        assert notebook(other.store, other.campaign_id)["ideas"] == []
    finally:
        other.close("paused")
        other.store.close()


@pytest.mark.parametrize("stage", ["prototype", "gate", "admit"])
def test_successful_checks_with_scores_are_checked_rather_than_measured(st, stage):
    cand = Candidate("check-only", "source")
    bind(st, cand, {"idea": IDEA})
    trial(st, cand, stage, {"score": 0})
    idea, = notebook(st.records.store, st.records.campaign_id)["ideas"]
    assert idea["status"] == "checked"
    assert idea["evaluations"][0]["metrics"] == {"score": 0}


@pytest.mark.parametrize("status,expected", [("running", "interrupted"), ("interrupted", "interrupted"),
                                            ("refused", "failed"), ("error", "failed")])
def test_trials_without_success_report_failure_or_interruption(st, status, expected):
    cand = Candidate("unfinished", "source")
    bind(st, cand, {"idea": IDEA})
    store, cid = st.records.store, st.records.campaign_id
    seq = store.begin_trial(cid, phase="generate", candidate=cand.to_record(), candidate_key=cand.name,
                            workload_hash="", arch_hash=None, strategy_kind="loop", stage="bench")
    if status != "running":
        store.complete_trial(cid, seq, status=status, result=None, error="endpoint interrupted", wall_clock_s=0)
    idea, = notebook(store, cid)["ideas"]
    assert idea["status"] == expected and idea["evaluations"][0]["status"] == status
    assert idea["evaluations"][0]["metrics"] == {}


def test_zero_measurements_survive_later_failed_retries(st):
    cand = Candidate("good", "source")
    bind(st, cand, {"idea": IDEA})
    trial(st, cand, "bench", {"cycles": 0, "area": -1}, pass_no=1)
    record_failure(st, cand.with_artifact("bad source"), "build", "syntax error")
    idea, = notebook(st.records.store, st.records.campaign_id)["ideas"]
    assert idea["status"] == "measured"
    assert idea["evaluations"][0]["metrics"] == {"cycles": 0, "area": -1}
    assert idea["evaluations"][-1]["error"] == "syntax error"
    assert "cycles=0" in context(st, None) and "syntax error" in context(st, None)


@pytest.mark.parametrize("finite", [True, False])
def test_nonfinite_measurements_are_excluded_from_notebook_json(st, finite):
    cand = Candidate("sparse", "source")
    bind(st, cand, {"idea": IDEA})
    metrics = {"positive_inf": float("inf"), "negative_inf": -float("inf")}
    if finite:
        metrics["cycles"] = 0
    trial(st, cand, "bench", metrics)
    data = notebook(st.records.store, st.records.campaign_id)
    idea, = data["ideas"]
    assert idea["evaluations"][0]["status"] == "ok"
    assert idea["evaluations"][0]["metrics"] == ({"cycles": 0} if finite else {})
    assert (idea["status"] == "measured") == finite
    json.dumps(data, allow_nan=False)


def test_baseline_intent_is_not_recorded_as_an_agent_experiment(st):
    baseline = Candidate("baseline", "# INTENT: Original baseline implementation\ncode", meta={"baseline": True})
    assert bind(st, baseline) == []
    trial(st, baseline, "bench", {"cycles": 30}, pass_no=0)
    assert notebook(st.records.store, st.records.campaign_id)["ideas"] == []


def test_prompt_memory_is_bounded_and_reserves_pending_alternatives(st):
    for index in range(20):
        propose(st, "core", {"title": f"Pending {index}", "hypothesis": "P" * 2000})
    for index in range(12):
        cand = Candidate(f"tested-{index}", "SECRET_SOURCE", subgoal="core")
        bind(st, cand, {"idea": {"title": f"Tested {index}", "hypothesis": "H" * 2000}})
        trial(st, cand, "bench", {"cycles": index})
    propose(st, "other", {"title": "OTHER_PART", "hypothesis": "Do not show this here."})
    text = context(st, "core", limit=8)
    assert text.count("\n- idea-") == 8
    assert "Pending 19" in text and "Tested 11" in text and "cycles=11" in text
    assert "Pending 0 [" not in text and "Tested 0 [" not in text
    assert "SECRET_SOURCE" not in text and "OTHER_PART" not in text
    assert len(text) < 7000


def test_many_future_notes_are_bounded_without_losing_the_selected_hypothesis(st):
    notes = [{"title": f"Alternative {i}", "hypothesis": f"Try approach {i}"} for i in range(100)]
    ids = capture(st, None, {"idea": IDEA, "ideas": notes})
    assert len(ids) == 1 and proposals(st)[ids[0]]["title"] == IDEA["title"]
    assert len(notebook(st.records.store, st.records.campaign_id)["ideas"]) == 13


def test_record_failure_does_not_create_evidence_for_unselected_notes(st):
    cand = Candidate("unlinked", "source")
    bind(st, cand, {"ideas": [IDEA]})
    record_failure(st, cand, "build", "syntax error")
    assert st.records.store.trials(st.records.campaign_id) == []
    idea, = notebook(st.records.store, st.records.campaign_id)["ideas"]
    assert idea["status"] == "proposed" and idea["evaluations"] == []


def test_parallel_parts_record_all_hypotheses_and_failures_without_cross_links(st):
    ready = Barrier(4)

    def experiment(part):
        ready.wait(timeout=10)
        cand = Candidate(part, "SAME_SOURCE", subgoal=part)
        ids = bind(st, cand, {"idea": IDEA, "ideas": [FUTURE]})
        record_failure(st, cand, "build", f"{part} compile failed")
        trial(st, cand, "bench", {"cycles": int(part[-1])}, pass_no=2)
        return ids[0]

    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(experiment, [f"part{i}" for i in range(4)]))
    assert len(set(ids)) == 4
    data = notebook(st.records.store, st.records.campaign_id)
    assert len(data["ideas"]) == 8
    for idea in data["ideas"]:
        if idea["id"] in ids:
            assert idea["status"] == "measured" and len(idea["evaluations"]) == 2
            assert {r["design"] for r in idea["evaluations"]} == {idea["part"]}
            assert idea["evaluations"][-1]["metrics"]["cycles"] == int(idea["part"][-1])
        else:
            assert idea["status"] == "proposed" and not idea["evaluations"]


def test_notebook_read_and_write_failures_do_not_prevent_design_evaluation(st, monkeypatch):
    def unavailable(*_args, **_kwargs):
        raise OSError("record unavailable")

    monkeypatch.setattr(st.records.store, "append_event", unavailable)
    monkeypatch.setattr(st.records.store, "events", unavailable)
    cand = Candidate("draft", "source")
    ids = bind(st, cand, {"idea": IDEA})
    assert ids and context(st, None).count(IDEA["title"]) == 1
    assert bind(st, cand.with_artifact("repair"), {"idea": {"id": ids[0]}}) == ids
    monkeypatch.setattr(st.records, "trial", unavailable)
    record_failure(st, cand, "build", "compile failed")
    assert context(st, None)


def test_a_failed_notebook_write_keeps_the_idea_in_the_next_prompt(st, monkeypatch):
    def unavailable(*_args, **_kwargs):
        raise OSError("write unavailable")

    monkeypatch.setattr(st.records.store, "append_event", unavailable)
    cand = Candidate("draft", "source")
    ids = bind(st, cand, {"idea": IDEA, "ideas": [FUTURE]})
    assert proposals(st)[ids[0]]["title"] == IDEA["title"]
    assert IDEA["title"] in context(st, None) and FUTURE["title"] in context(st, None)


def test_a_run_without_a_database_can_still_keep_and_revisit_ideas(st):
    fresh = LoopState(request=st.request, records=None, proposer=None, feedback=None, say=lambda _: None)
    cand = Candidate("draft", "source")
    ids = bind(fresh, cand, {"idea": IDEA, "ideas": [FUTURE]})
    assert bind(fresh, cand.with_artifact("repair"), {"idea": {"id": ids[0]}}) == ids
    assert IDEA["title"] in context(fresh, None) and FUTURE["title"] in context(fresh, None)
    record_failure(fresh, cand, "build", "compile failed")


def test_pending_fallback_notes_do_not_replace_or_duplicate_saved_evidence(st, monkeypatch):
    cand = Candidate("measured", "source")
    bind(st, cand, {"idea": IDEA})
    trial(st, cand, "bench", {"cycles": 8})

    def unavailable(*_args, **_kwargs):
        raise OSError("write unavailable")

    monkeypatch.setattr(st.records.store, "append_event", unavailable)
    propose(st, None, FUTURE)
    text = context(st, None)
    assert text.count(IDEA["title"]) == 1 and "cycles=8" in text and "[measured]" in text
    assert text.count(FUTURE["title"]) == 1 and "[proposed]" in text


def test_an_independent_pass_does_not_inherit_the_previously_selected_idea(st):
    from flux_loop.novelty import fresh_context
    from flux_loop.tools import _ideas_tool

    ident = _ideas_tool(None, st).run({"action": "select", "idea": IDEA})
    assert bind(st, Candidate("old", "source")) == [ident]
    with fresh_context(st, None):
        assert bind(st, Candidate("new", "different source"), {"ideas": [FUTURE]}) == []
        assert IDEA["title"] in context(st, None) and FUTURE["title"] in context(st, None)


def test_model_build_failure_is_retained_when_a_later_draft_succeeds(tmp_path, monkeypatch):
    monkeypatch.setenv("FLUX_TRACE_ROOT", str(tmp_path / "traces"))
    script = "import pathlib,sys; bad=pathlib.Path(sys.argv[1]).read_text()=='bad'; print('compile error' if bad else 'ok'); sys.exit(3 if bad else 0)"
    task = TaskSpec.from_dict({"id": "ideas-build", "statement": "Build a valid design", "flow": {
        "knowledge": "off", "test": {"build": ["{python}", "-c", script, "{artifact}"]},
        "measure": {"bench": {"command": ["{python}", "-c", "print('cycles=0')"], "metrics": ["cycles"]}}},
        "budget": {"steps": 1, "repair_attempts": 2, "patching": False, "prototype": False}})
    req = request_for(task, db=str(tmp_path / "loop.db"))
    model = ScriptedProposer([json.dumps({"artifact": code, "idea": IDEA}) for code in ("bad", "good")])
    result = run_loop(PromptProblem(task), req, proposer=model, log=lambda _: None)
    assert result.decision is not None
    rec = CampaignStore(req.db)
    try:
        idea, = notebook(rec, task.id)["ideas"]
        assert idea["status"] == "measured"
        assert any(r["stage"] == "build" and "compile error" in r["error"] for r in idea["evaluations"])
        assert any(r["metrics"] == {"cycles": 0} for r in idea["evaluations"])
    finally:
        rec.close()


@pytest.mark.parametrize("notes", ["not json", "[]", '{"idea":42,"ideas":[null,{}]}'])
def test_malformed_agent_notes_do_not_refuse_a_valid_artifact(tmp_path, monkeypatch, notes):
    from test_agent_sessions import _digits, _fake

    monkeypatch.setenv("FLUX_TRACE_ROOT", str(tmp_path / "traces"))
    fake = _fake(tmp_path)
    fake.write_text(fake.read_text() + "\nartifact.with_name(artifact.name + '.ideas.json').write_text(" + repr(notes) + ")\n")
    task = _digits(fake, role="genok")
    req = request_for(task, db=str(tmp_path / "loop.db"))
    result = run_loop(PromptProblem(task), req, proposer=None, log=lambda _: None)
    assert result.decision is not None
    rec = CampaignStore(req.db)
    try:
        assert notebook(rec, task.id)["ideas"] == []
    finally:
        rec.close()


@pytest.mark.parametrize("reuse_id", [False, True])
def test_proposals_retry_persistence_after_a_transient_write_failure(st, monkeypatch, reuse_id):
    saved = st.records.store.append_event

    def unavailable(*_args, **_kwargs):
        raise OSError("temporarily read-only record")

    monkeypatch.setattr(st.records.store, "append_event", unavailable)
    cand = Candidate("experiment", "source")
    ident, = bind(st, cand, {"idea": IDEA})
    trial(st, cand, "bench", {"cycles": 8})
    assert notebook(st.records.store, st.records.campaign_id)["ideas"] == []
    monkeypatch.setattr(st.records.store, "append_event", saved)
    assert propose(st, None, {"id": ident} if reuse_id else IDEA) == ident
    # Read through a separate store: no in-memory fallback can hide a loss.
    st.records.close("paused")
    store = CampaignStore(st.request.db)
    try:
        idea, = notebook(store, st.records.campaign_id)["ideas"]
        assert idea["id"] == ident and idea["status"] == "measured"
        assert idea["evaluations"][0]["metrics"] == {"cycles": 8}
        assert propose(st, None, IDEA) == ident
        assert len([e for e in store.events(st.records.campaign_id) if e["kind"] == "idea"]) == 1
    finally:
        store.close()


def test_prompt_evidence_recovers_after_a_transient_read_failure(st, monkeypatch):
    cand = Candidate("experiment", "source")
    bind(st, cand, {"idea": IDEA})
    trial(st, cand, "bench", {"cycles": 8})
    saved = st.records.store.events

    def unavailable(*_args, **_kwargs):
        raise OSError("temporarily unavailable record")

    monkeypatch.setattr(st.records.store, "events", unavailable)
    fallback = context(st, None)
    assert IDEA["title"] in fallback and "cycles=8" not in fallback
    monkeypatch.setattr(st.records.store, "events", saved)
    recovered = context(st, None)
    assert recovered.count(IDEA["title"]) == 1 and "cycles=8" in recovered and "[measured]" in recovered


def test_stale_agent_notes_are_removed_before_a_repair_and_pending_notes_stay_untested(tmp_path, monkeypatch):
    from pathlib import Path
    from test_agent_sessions import _digits, _fake, _turns

    monkeypatch.setenv("FLUX_TRACE_ROOT", str(tmp_path / "traces"))
    fake = _fake(tmp_path)
    fake.write_text(fake.read_text() + "\nif mode == 'first':\n    artifact.with_name(artifact.name + '.ideas.json').write_text(" +
                    repr(json.dumps({"ideas": [FUTURE]})) + ")\n")
    task = _digits(fake)  # First draft fails; the resumed turn leaves no notes file.
    req = request_for(task, db=str(tmp_path / "loop.db"))
    result = run_loop(PromptProblem(task), req, proposer=None, log=lambda _: None)
    assert result.decision is not None
    first, repair = _turns(tmp_path)
    assert repair["mode"] == "resume"
    assert not list(Path(first["cwd"]).glob("*.ideas.json"))
    store = CampaignStore(req.db)
    try:
        idea, = notebook(store, task.id)["ideas"]
        assert idea["title"] == FUTURE["title"] and idea["status"] == "proposed" and not idea["evaluations"]
    finally:
        store.close()
