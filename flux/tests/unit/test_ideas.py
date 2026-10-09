"""Ideas persist independently of drafts; checks and measurements are real trial evidence."""
from __future__ import annotations

import json
from flux_llm import ScriptedProposer
from flux_loop import LoopRequest, LoopState, PromptProblem, TaskSpec, request_for, run_loop
from flux_loop.ideas import bind, bind_code, capture, code_ids, context, notebook, propose, sidecar
from flux_loop.provenance import stamp
from flux_loop.tools import _ideas_tool
from flux_loop.types import Candidate, Verdict
from flux_profile import tagged
from flux_records import Records
from flux_store import CampaignStore


IDEA = {"title": "Fewer stages", "hypothesis": "Reducing redundant stages should lower latency.", "test": "Measure cycles and area."}
FUTURE = {"title": "Lookup table", "hypothesis": "A lookup table might trade area for fewer cycles."}


def state(tmp_path, name="ideas"):
    return LoopState(request=LoopRequest(db=str(tmp_path / "record.db")), say=lambda _: None,
                     proposer=None, feedback=None, records=Records(str(tmp_path / "record.db"), {"study": name}, name=name))


def trial(st, cand, stage, metrics, error=None, pass_no=1):
    with tagged(**{"pass": pass_no}):
        st.records.trial({**cand.to_record(), "meta": {**cand.meta, "provenance": stamp()}}, cand.name,
                         stage=stage, strategy="loop", metrics=metrics, error=error)


def test_notebook_retains_untested_ideas_and_results_across_passes_and_restarts(tmp_path):
    st = state(tmp_path)
    cand = Candidate("a", "SECRET_IMPLEMENTATION")
    bind(st, cand, {"idea": IDEA, "ideas": [FUTURE]})
    ident = cand.meta["idea_ids"][0]
    trial(st, cand, "gate", {"score": 1}, "incorrect corner case")
    st.records.close("paused")
    restarted = state(tmp_path)
    assert propose(restarted, None, IDEA) == ident
    improved = Candidate("b", "ANOTHER_SECRET_IMPLEMENTATION")
    bind(restarted, improved, {"idea": {"id": ident}})
    trial(restarted, improved, "gate", {"score": 0}, pass_no=2)
    trial(restarted, improved, "bench", {"cycles": 8, "area": 12}, pass_no=2)
    data = notebook(restarted.records.store, restarted.records.campaign_id)
    assert len(data["ideas"]) == 2
    tested = next(i for i in data["ideas"] if i["id"] == ident)
    assert tested["status"] == "measured" and [r["pass"] for r in tested["evaluations"]] == [1, 2, 2]
    assert tested["evaluations"][-1]["metrics"] == {"cycles": 8, "area": 12}
    assert next(i for i in data["ideas"] if i["title"] == FUTURE["title"])["status"] == "proposed"
    assert "SECRET_IMPLEMENTATION" not in json.dumps(data)
    text = context(restarted, None)
    assert FUTURE["title"] in text and "cycles=8" in text and "incorrect corner case" in text
    assert "SECRET_IMPLEMENTATION" not in text


def test_pending_proposals_are_not_mislabeled_as_evaluated(tmp_path):
    st = state(tmp_path)
    cand = Candidate("a", "code")
    assert bind(st, cand, {"ideas": [IDEA]}) == []
    trial(st, cand, "bench", {"cycles": 4})
    idea, = notebook(st.records.store, st.records.campaign_id)["ideas"]
    assert idea["status"] == "proposed" and idea["evaluations"] == []


def test_tool_selection_is_part_scoped_and_invalid_notes_do_not_fail_the_design(tmp_path):
    st = state(tmp_path)
    tool = _ideas_tool("part", st)
    ident = tool.run({"action": "propose", "idea": IDEA})
    assert "Fewer stages" in tool.run({"action": "list"})
    assert bind(st, Candidate("a", "code", subgoal="part")) == []
    assert tool.run({"action": "select", "idea": {"id": ident}}) == ident
    assert bind(st, Candidate("a", "code", subgoal="part")) == [ident]
    assert bind(st, Candidate("b", "code", subgoal="other"), {"idea": {"id": ident}}) == []
    assert capture(st, "part", {"idea": {}, "ideas": [None, {}, FUTURE]}) == [ident]
    assert tool.run({"action": "evaluate", "idea": IDEA}).startswith("error:")


def test_prototype_links_survive_binding_and_reload(tmp_path):
    from flux_loop.prototype import _record_prototype

    st = state(tmp_path)
    ids = bind_code(st, None, "prototype source", {"idea": IDEA})
    assert bind_code(st, None, "bound prototype", {}, previous="prototype source") == ids
    _record_prototype(st, None, "bound prototype", Verdict(True, 0), ok=True)
    fresh = state(tmp_path)
    assert code_ids(fresh, "bound prototype") == ids
    idea, = notebook(fresh.records.store, fresh.records.campaign_id)["ideas"]
    assert idea["status"] == "checked" and idea["evaluations"][0]["stage"] == "prototype"


def test_intent_header_is_collected_automatically_without_code(tmp_path):
    st = state(tmp_path)
    cand = Candidate("a", "# INTENT: remove redundant stages\nSECRET_IMPLEMENTATION")
    assert bind(st, cand)
    assert "remove redundant stages" in context(st, None) and "SECRET_IMPLEMENTATION" not in context(st, None)


def test_identical_prototypes_in_different_parts_keep_separate_ideas(tmp_path):
    from flux_loop.prototype import _record_prototype

    st = state(tmp_path)
    a = bind_code(st, "a", "same prototype", {"idea": IDEA})
    b = bind_code(st, "b", "same prototype", {"idea": FUTURE})
    assert a != b
    for part in ("a", "b"):
        _record_prototype(st, part, "same prototype", Verdict(True, 0), ok=True)
    fresh = state(tmp_path)
    assert code_ids(fresh, "same prototype", "a") == a
    assert code_ids(fresh, "same prototype", "b") == b
    assert code_ids(fresh, "same prototype") == []
    assert FUTURE["title"] not in context(fresh, "a") and IDEA["title"] not in context(fresh, "b")


def test_a_patch_can_change_hypothesis_and_later_recording_keeps_that_choice(tmp_path):
    st = state(tmp_path)
    cand = Candidate("a", "code")
    first, = bind(st, cand, {"idea": IDEA})
    patch = cand.with_artifact("new code")
    second, = bind(st, patch, {"edits": [], "idea": FUTURE})
    assert second != first and bind(st, patch) == [second]
    trial(st, patch, "bench", {"cycles": 4})
    data = notebook(st.records.store, st.records.campaign_id)
    assert next(i for i in data["ideas"] if i["id"] == first)["evaluations"] == []
    assert next(i for i in data["ideas"] if i["id"] == second)["status"] == "measured"


def test_model_fast_check_failures_are_kept_before_the_draft_is_replaced(tmp_path, monkeypatch):
    from test_agent_sessions import DIGITS

    monkeypatch.setenv("FLUX_TRACE_ROOT", str(tmp_path / "traces"))
    d = json.loads(DIGITS.read_text())
    d["id"] = "ideas"
    d["flow"]["knowledge"] = "off"
    d["budget"].update(patching=False)
    task = TaskSpec.from_dict(d)
    req = request_for(task, db=str(tmp_path / "loop.db"))
    good = "\n".join(str(i) for i in range(10))
    replies = [json.dumps({"artifact": text, "idea": IDEA}) for text in (good.replace("3", "x"), good)]
    result = run_loop(PromptProblem(task), req, proposer=ScriptedProposer(replies), log=lambda _: None)
    assert result.decision is not None
    rec = CampaignStore(req.db)
    idea, = notebook(rec, task.id)["ideas"]
    assert idea["status"] == "checked"
    assert any(r["stage"] == "fast-check" and "FAIL line 4" in r["error"] for r in idea["evaluations"])
    rec.close()


def test_notes_file_is_optional_bounded_and_rejects_links(tmp_path):
    artifact = tmp_path / "design.py"
    notes = tmp_path / "design.py.ideas.json"
    assert sidecar(artifact) == {}
    notes.write_text(json.dumps({"idea": IDEA}))
    assert sidecar(artifact)["idea"] == IDEA
    notes.write_text("not json")
    assert sidecar(artifact) == {}
    notes.write_text(" " * 65537)
    assert sidecar(artifact) == {}
    notes.unlink()
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps({"idea": IDEA}))
    notes.symlink_to(outside)
    assert sidecar(artifact) == {}


def test_model_passes_receive_the_notebook_and_record_the_actual_numbers(tmp_path, monkeypatch):
    monkeypatch.setenv("FLUX_TRACE_ROOT", str(tmp_path / "traces"))
    task = TaskSpec.from_dict({"id": "ideas", "statement": "Make a shorter artifact.", "flow": {
        "knowledge": "off", "orchestrate": {"by": "rules", "dse": "variations"}, "test": {"check": "true"},
        "measure": {"bench": {"command": ["{python}", "-c", "print('length=3')"], "metrics": ["length"]}}},
        "budget": {"steps": 1, "prototype": False}, "objectives": [{"metric": "length", "direction": "minimize"}]})
    problem = PromptProblem(task)
    req = request_for(task, db=str(tmp_path / "loop.db"))
    first = ScriptedProposer([json.dumps({"artifact": "first", "idea": IDEA, "ideas": [FUTURE]})])
    run_loop(problem, req, proposer=first, log=lambda _: None)
    rec = CampaignStore(req.db)
    idea = next(i for i in notebook(rec, task.id)["ideas"] if i["title"] == IDEA["title"])
    assert idea["status"] == "measured"
    second = ScriptedProposer([json.dumps({"artifact": "second", "idea": {"id": idea["id"]}})])
    run_loop(problem, req, proposer=second, log=lambda _: None)
    assert "IDEAS NOTEBOOK" in second.prompts[0] and FUTURE["title"] in second.prompts[0] and "length=3" in second.prompts[0]
    data = notebook(rec, task.id)
    assert {r["design"] for i in data["ideas"] if i["id"] == idea["id"] for r in i["evaluations"]} == {"ideas#1", "ideas#2"}
    rec.close()


def test_agent_notes_follow_gate_repairs_and_keep_future_ideas_untested(tmp_path, monkeypatch):
    from test_agent_sessions import _digits, _fake, _turns

    monkeypatch.setenv("FLUX_TRACE_ROOT", str(tmp_path / "traces"))
    fake = _fake(tmp_path)
    fake.write_text(fake.read_text() + "\nartifact.with_name(artifact.name + '.ideas.json').write_text(" +
                    repr(json.dumps({"idea": IDEA, "ideas": [FUTURE]})) + ")\n")
    task = _digits(fake)  # The first draft fails; the same agent repairs it.
    d = task.to_dict()
    d["flow"]["measure"] = {"bench": {"command": ["{python}", "-c", "print('cycles=8')"], "metrics": ["cycles"]}}
    task = TaskSpec.from_dict(d)
    req = request_for(task, db=str(tmp_path / "loop.db"))
    result = run_loop(PromptProblem(task), req, proposer=None, log=lambda _: None)
    assert result.decision is not None
    rec = CampaignStore(req.db)
    data = notebook(rec, task.id)
    tested = next(i for i in data["ideas"] if i["title"] == IDEA["title"])
    assert tested["status"] == "measured"
    assert any(r["status"] == "refused" for r in tested["evaluations"])
    assert any(r["metrics"] for r in tested["evaluations"] if r["status"] == "ok")
    assert next(i for i in data["ideas"] if i["title"] == FUTURE["title"])["evaluations"] == []
    assert ".ideas.json" in _turns(tmp_path)[0]["text"]
    rec.close()


def test_agent_prototype_notes_follow_transpilation_and_measurement(tmp_path, monkeypatch):
    import yaml
    from flux_loop import load_task
    from test_golden_prototype import AGENT, _sq_doc

    monkeypatch.setenv("FLUX_TRACE_ROOT", str(tmp_path / "traces"))
    _sq_doc(tmp_path)
    doc = tmp_path / "p" / "sq" / "problem.yaml"
    fake = tmp_path / "agent.py"
    fake.write_text(AGENT + "\nopen(out + '.ideas.json', 'w').write(" +
                    repr(json.dumps({"idea": IDEA, "ideas": [FUTURE]})) + ")\n")
    d = yaml.safe_load(doc.read_text())
    d["flow"]["knowledge"] = "off"
    d["flow"]["generate"] = {"by": {"command": ["{python}", str(fake), "{prompt_file}", "{artifact}"]}}
    doc.write_text(yaml.safe_dump(d, sort_keys=False))
    task = load_task(doc)
    req = request_for(task, db=str(tmp_path / "loop.db"))
    result = run_loop(PromptProblem(task), req, proposer=ScriptedProposer([]), log=lambda _: None)
    assert result.decision is not None
    rec = CampaignStore(req.db)
    data = notebook(rec, task.id)
    tested = next(i for i in data["ideas"] if i["title"] == IDEA["title"])
    assert tested["status"] == "measured"
    assert {"prototype", "admit", "screen"} <= {r["stage"] for r in tested["evaluations"]}
    assert next(i for i in data["ideas"] if i["title"] == FUTURE["title"])["evaluations"] == []
    rec.close()


def test_cost_pass_keeps_the_seed_prototypes_idea_without_requiring_another_note(tmp_path, monkeypatch):
    from test_golden_prototype import CHEAP, COSTLY, _sq_doc

    monkeypatch.setenv("FLUX_TRACE_ROOT", str(tmp_path / "traces"))
    task, _ = _sq_doc(tmp_path, prototype_cost_max=120)
    req = request_for(task, db=str(tmp_path / "loop.db"))
    model = ScriptedProposer([json.dumps({**json.loads(COSTLY), "idea": IDEA}), CHEAP])
    result = run_loop(PromptProblem(task), req, proposer=model, log=lambda _: None)
    assert result.decision is not None
    rec = CampaignStore(req.db)
    idea, = notebook(rec, task.id)["ideas"]
    assert idea["title"] == IDEA["title"] and idea["status"] == "measured"
    assert sum(r["stage"] == "prototype" for r in idea["evaluations"]) >= 2
    assert any(r["stage"] == "screen" for r in idea["evaluations"])
    rec.close()
