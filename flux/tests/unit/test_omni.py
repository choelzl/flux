"""flux_omni: the introspected catalog, typed-plan validation, the executor, and the loop
(docs/decisions.md D377). The loop tests drive a scripted stub proposer -- the real model
is exercised by the demo, not here; what must hold regardless of model quality is that
bad proposals become refusals fed back verbatim, good steps execute with binds resolved,
and provenance replays to the same outcomes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from flux_omni import (
    ParamSpec, Refusal, Step, ToolSpec, build_catalog, load_plan_file, parse_proposal,
    resolve_refs, run_omni, run_plan, summarize, validate_step,
)

FLUX_ROOT = Path(__file__).resolve().parents[2]


# ---------- catalog ----------

def test_catalog_introspects_the_real_mcp_surface():
    catalog = build_catalog()
    assert "evaluate" in catalog and "search" in catalog and "agentic_dse_loop" in catalog
    assert len(catalog) >= 30  # the full surface, not a curated shortlist
    ev = catalog["evaluate"]
    params = {p.name: p for p in ev.params}
    assert params["backend"].required and params["workload"].required
    assert not params["arch"].required
    assert "self" not in params
    assert ev.summary.startswith("Evaluate a candidate")


def test_catalog_subset_filters_and_rejects_unknown_names():
    catalog = build_catalog(["evaluate", "flux_search"])  # both name forms accepted
    assert set(catalog) == {"evaluate", "search"}
    with pytest.raises(KeyError):
        build_catalog(["evaluate", "no_such_tool"])


def test_catalog_render_lists_params_with_requiredness():
    catalog = build_catalog(["evaluate"])
    text = catalog["evaluate"].render()
    assert "### evaluate" in text
    assert "backend" in text and "required" in text and "optional" in text


def test_catalog_menu_is_one_compact_line_per_tool():
    """D377: the round prompt shows signatures only; the full render of the whole surface
    measured ~21k tokens and crashed the serving window. Budget: the entire menu must stay
    under ~4k tokens (12k chars)."""
    from flux_omni import render_catalog
    catalog = build_catalog()
    menu = render_catalog(catalog)
    assert len(menu.splitlines()) == len(catalog)
    assert len(menu) < 12000
    line = catalog["evaluate"].signature()
    assert line.startswith("evaluate(backend, workload, +") and " -- " in line


def test_describe_meta_tool_validates_and_returns_full_detail(tmp_path):
    cat = _stub_catalog()
    r = validate_step(0, Step("describe", {"tool": "nope"}), cat, set(), tmp_path)
    assert "no tool named" in r.reason
    assert validate_step(0, Step("describe", {"tool": "add"}), cat, set(), tmp_path) is None
    outcomes, refusals = run_plan((Step("describe", {"tool": "add"}, bind="d"),), cat, tmp_path)
    assert refusals == [] and "### add" in outcomes[0].result["detail"]


# ---------- proposal parsing ----------

def test_parse_proposal_accepts_fenced_and_prose_wrapped_json():
    fenced = '```json\n{"steps": [{"tool": "t", "args": {}}], "done": false}\n```'
    p = parse_proposal(fenced)
    assert p.parse_error is None and p.steps[0].tool == "t"
    wrapped = 'Sure! Here is my plan: {"steps": [], "done": true, "conclusion": "x"} Hope it helps.'
    p = parse_proposal(wrapped)
    assert p.parse_error is None and p.done and p.conclusion == "x"


def test_parse_proposal_turns_garbage_into_a_parse_error_not_a_crash():
    assert parse_proposal("no json here").parse_error is not None
    assert parse_proposal('{"steps": "not-a-list"}').parse_error is not None
    assert parse_proposal('{"steps": [{"args": {}}]}').parse_error is not None


# ---------- validation ----------

def _stub_catalog():
    def add(a: int, b: int = 1):
        return {"sum": a + b}

    def boom():
        raise RuntimeError("tool exploded")

    def wrap(name, fn, params):
        return ToolSpec(name=name, summary=f"{name} stub", params=tuple(params), fn=fn)

    import inspect
    empty = inspect.Parameter.empty
    return {
        "add": wrap("add", add, [ParamSpec("a", "int", True, empty, ""),
                                 ParamSpec("b", "int", False, 1, "")]),
        "boom": wrap("boom", boom, []),
    }


def test_validate_refuses_unknown_tool_unknown_arg_and_missing_required(tmp_path):
    cat = _stub_catalog()
    assert "unknown tool" in validate_step(0, Step("nope", {}), cat, set(), tmp_path).reason
    assert "unknown argument" in validate_step(
        0, Step("add", {"a": 1, "z": 2}), cat, set(), tmp_path).reason
    assert "missing required" in validate_step(
        0, Step("add", {"b": 2}), cat, set(), tmp_path).reason
    assert validate_step(0, Step("add", {"a": 1}), cat, set(), tmp_path) is None


def test_validate_refuses_forward_and_malformed_references(tmp_path):
    cat = _stub_catalog()
    r = validate_step(0, Step("add", {"a": "$later.sum"}), cat, set(), tmp_path)
    assert "names no earlier bind" in r.reason
    assert validate_step(0, Step("add", {"a": "$ok.sum"}), cat, {"ok"}, tmp_path) is None
    r = validate_step(0, Step("add", {"a": "$bad syntax"}), cat, {"bad"}, tmp_path)
    assert "malformed reference" in r.reason


def test_validate_sandboxes_write_file_and_load_ir_paths(tmp_path):
    cat = _stub_catalog()
    r = validate_step(0, Step("write_file", {"path": "/etc/x", "text": "y"}), cat, set(), tmp_path)
    assert "escapes" in r.reason
    r = validate_step(0, Step("write_file", {"path": "../x", "text": "y"}), cat, set(), tmp_path)
    assert "escapes" in r.reason
    r = validate_step(0, Step("load_ir", {"path": "/abs/doc.yaml"}), cat, set(), tmp_path)
    assert "relative" in r.reason
    assert validate_step(
        0, Step("write_file", {"path": "sub/x.txt", "text": "y"}), cat, set(), tmp_path) is None


# ---------- reference resolution ----------

def test_resolve_refs_traverses_fields_and_indices():
    bindings = {"r": {"items": [{"v": 7}, {"v": 9}], "name": "x"}}
    assert resolve_refs("$r.name", bindings) == "x"
    assert resolve_refs("$r.items[1].v", bindings) == 9
    assert resolve_refs({"nested": ["$r.items[0].v"]}, bindings) == {"nested": [7]}
    assert resolve_refs("not a ref", bindings) == "not a ref"
    with pytest.raises(KeyError):
        resolve_refs("$missing.x", bindings)


# ---------- executor ----------

def test_run_plan_refuses_everything_before_running_anything(tmp_path):
    cat = _stub_catalog()
    steps = (Step("add", {"a": 1}, bind="ok"), Step("typo_tool", {}))
    outcomes, refusals = run_plan(steps, cat, tmp_path)
    assert outcomes == [] and len(refusals) == 1  # the valid step did NOT run


def test_run_plan_executes_with_bind_chaining_and_records_tool_crashes(tmp_path):
    cat = _stub_catalog()
    steps = (
        Step("add", {"a": 2, "b": 3}, bind="first"),
        Step("add", {"a": "$first.sum"}, bind="second"),
        Step("boom", {}),
        Step("write_file", {"path": "out.txt", "text": "hello"}),
    )
    outcomes, refusals = run_plan(steps, cat, tmp_path)
    assert refusals == []
    assert outcomes[0].result == {"sum": 5}
    assert outcomes[1].result == {"sum": 6}  # $first.sum resolved to 5, default b=1
    assert not outcomes[2].ok and "tool exploded" in outcomes[2].error
    assert outcomes[3].ok and (tmp_path / "out.txt").read_text() == "hello"


# ---------- the loop with a scripted proposer ----------

class ScriptedProposer:
    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts = []

    def propose(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.replies.pop(0)


def test_run_omni_feeds_refusals_back_and_reaches_a_grounded_done(tmp_path, monkeypatch):
    import flux_omni.pilot as pilot
    monkeypatch.setattr(pilot, "build_catalog", lambda subset=None: _stub_catalog())
    proposer = ScriptedProposer([
        json.dumps({"steps": [{"tool": "add", "args": {"a": 1, "wrong": 2}}]}),
        json.dumps({"steps": [{"tool": "add", "args": {"a": 1, "b": 2}, "bind": "r"}],
                    "done": True, "conclusion": "sum is 3"}),
    ])
    report = pilot.run_omni("add one and two", proposer, workdir=tmp_path)
    assert report.done and report.conclusion == "sum is 3"
    assert report.llm_calls == 2 and len(report.outcomes) == 1
    assert report.outcomes[0].result == {"sum": 3}
    # the second prompt carried the first round's refusal verbatim
    assert "unknown argument" in proposer.prompts[1]
    assert "repair" in proposer.prompts[1]


def test_run_omni_refuses_duplicate_steps_and_bad_bind_names(tmp_path, monkeypatch):
    import flux_omni.pilot as pilot
    monkeypatch.setattr(pilot, "build_catalog", lambda subset=None: _stub_catalog())
    proposer = ScriptedProposer([
        json.dumps({"steps": [{"tool": "add", "args": {"a": 1}},
                              {"tool": "add", "args": {"a": 1}},
                              {"tool": "add", "args": {"a": 2}, "bind": "$bad"}]}),
        json.dumps({"steps": [], "done": True, "conclusion": "ok"}),
    ])
    report = pilot.run_omni("dedup", proposer, workdir=tmp_path)
    assert len(report.outcomes) == 1  # the duplicate and the bad-bind step were refused
    reasons = [r.reason for r in report.refusals]
    assert any("identical to executed step [0]" in r for r in reasons)
    assert any("bare identifier" in r for r in reasons)
    assert "identical to executed step" in proposer.prompts[1]


def test_round_prompt_announces_the_last_round(tmp_path, monkeypatch):
    import flux_omni.pilot as pilot
    monkeypatch.setattr(pilot, "build_catalog", lambda subset=None: _stub_catalog())
    proposer = ScriptedProposer([
        json.dumps({"steps": []}),
        json.dumps({"steps": []}),
    ])
    pilot.run_omni("hold", proposer, workdir=tmp_path, max_rounds=2)
    assert "Round 1 of 2." in proposer.prompts[0]
    assert "LAST round" in proposer.prompts[1]


def test_wall_clock_budget_stop_still_gets_a_conclude_only_round(tmp_path, monkeypatch):
    import flux_omni.pilot as pilot
    monkeypatch.setattr(pilot, "build_catalog", lambda subset=None: _stub_catalog())
    class SlowScripted(ScriptedProposer):
        def propose(self, prompt):
            import time as _t
            _t.sleep(0.06)
            return super().propose(prompt)

    proposer = SlowScripted([
        json.dumps({"steps": [{"tool": "add", "args": {"a": 1, "b": 1}}]}),
        "the sum is 2",
    ])
    report = pilot.run_omni("add", proposer, workdir=tmp_path, wall_clock_budget_s=0.05)
    # round 1 runs (the budget is checked at round start, when ~0s have elapsed) and
    # spends the budget; round 2's check trips, and the conclude-only call fires
    # instead of ending on silence
    assert not report.done
    assert report.conclusion == "the sum is 2"
    assert "budget is exhausted" in proposer.prompts[1]
    assert len(report.outcomes) == 1


def test_run_omni_budget_stop_reports_done_false(tmp_path, monkeypatch):
    import flux_omni.pilot as pilot
    monkeypatch.setattr(pilot, "build_catalog", lambda subset=None: _stub_catalog())
    proposer = ScriptedProposer([
        json.dumps({"steps": [{"tool": "add", "args": {"a": 1}}]}),
        json.dumps({"steps": [{"tool": "add", "args": {"a": 2}}]}),
    ])
    report = pilot.run_omni("keep adding", proposer, workdir=tmp_path, max_rounds=2)
    assert not report.done and report.rounds == 2 and len(report.outcomes) == 2


def test_run_omni_provenance_is_itself_a_replayable_plan(tmp_path, monkeypatch):
    import flux_omni.pilot as pilot
    monkeypatch.setattr(pilot, "build_catalog", lambda subset=None: _stub_catalog())
    proposer = ScriptedProposer([
        json.dumps({"steps": [{"tool": "add", "args": {"a": 4, "b": 5}, "bind": "r"},
                              {"tool": "add", "args": {"a": "$r.sum"}}],
                    "done": True, "conclusion": "done"}),
    ])
    report = pilot.run_omni("chain", proposer, workdir=tmp_path / "run1")
    steps = load_plan_file(report.provenance_path)
    outcomes, refusals = run_plan(steps, _stub_catalog(), tmp_path / "run2")
    assert refusals == []
    assert [o.result for o in outcomes] == [o.result for o in report.outcomes]


# ---------- canned plans stay valid against the real catalog ----------

@pytest.mark.parametrize("plan_file", sorted(
    (FLUX_ROOT / "applications/omni/plans").glob("*.json")), ids=lambda p: p.name)
def test_canned_plans_validate_against_the_real_catalog(plan_file, tmp_path):
    """The rot guard: a canned plan whose tool or argument names drift from the MCP
    surface fails HERE, at unit speed, not in front of a demo audience."""
    catalog = build_catalog()
    steps = load_plan_file(plan_file)
    bound: set[str] = set()
    for i, step in enumerate(steps):
        assert validate_step(i, step, catalog, bound, tmp_path) is None, (plan_file, i)
        if step.bind:
            bound.add(step.bind)


# ---------- summaries ----------

def test_summarize_truncates_long_leaves_and_whole_renderings():
    assert "...(500 chars)" in summarize({"x": "a" * 500})
    long_list = summarize({"items": list(range(100))})
    assert "...(100 items)" in long_list
    assert len(summarize({"x": ["y" * 100] * 100}, budget=300)) < 340


# ---------- the rim: operator feedback + campaign record (D401) ----------

def _Channel(*texts):
    from flux_feedback import scripted_channel

    return scripted_channel(*texts)


def test_run_omni_consumes_operator_feedback_and_keeps_a_record(tmp_path, monkeypatch):
    import flux_omni.pilot as pilot
    monkeypatch.setattr(pilot, "build_catalog", lambda subset=None: _stub_catalog())
    db = str(tmp_path / "omni.db")
    done_reply = json.dumps({"steps": [{"tool": "add", "args": {"a": 2, "b": 3}}],
                             "done": True, "conclusion": "sum is 5 (measured)"})
    proposer = ScriptedProposer([done_reply])
    report = pilot.run_omni("add two and three", proposer, workdir=tmp_path,
                            feedback=_Channel("prefer the direct tool"), db_path=db)
    assert report.done
    # the note reached the round prompt, labelled, and the run left a record
    assert "HUMAN GUIDANCE" in proposer.prompts[0]
    assert "prefer the direct tool" in proposer.prompts[0]
    # a second run of the SAME prompt resumes and reads the conclusion back
    proposer2 = ScriptedProposer([done_reply])
    pilot.run_omni("add two and three", proposer2, workdir=tmp_path, db_path=db)
    assert "earlier run of this exact task concluded" in proposer2.prompts[0]
    assert "sum is 5 (measured)" in proposer2.prompts[0]
    # and the earlier run's typed note rejoins the prompt, honestly stamped (D403)
    assert "prefer the direct tool" in proposer2.prompts[0]
    assert "[earlier run]" in proposer2.prompts[0]
    # while a DIFFERENT prompt starts its own campaign, blind
    proposer3 = ScriptedProposer([done_reply])
    pilot.run_omni("add nine and one", proposer3, workdir=tmp_path, db_path=db)
    assert "earlier run" not in proposer3.prompts[0]
