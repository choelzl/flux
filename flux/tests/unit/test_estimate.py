"""Stage estimators (D665): before a stage runs its tool, an estimate of its metrics skips the
designs that would fail its cutoff or an objective's limit by more than the margin. Plus the
loop fixes of D666."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from flux_llm import ScriptedProposer
from flux_loop import PromptProblem, TaskSpec, request_for, run_loop
from flux_loop.document import TaskError, describe_flow
from flux_loop.estimate import predict_fit, predict_knn

GEN = "import sys; open(sys.argv[1], 'w').write('x=' + sys.argv[2])"
# the tool: cost 10x, and a line per run in tool.log beside it
TOOL = ("import sys, pathlib; x = int(open(sys.argv[1]).read().split('=')[1]); "
        "pathlib.Path(sys.argv[2]).open('a').write(f'{x}\\n'); print(f'cost={10 * x}')")


def _doc(tmp_path: Path, xs, estimate=None, **more) -> dict:
    (tmp_path / "gen.py").write_text(GEN)
    (tmp_path / "tool.py").write_text(TOOL)
    stage = {"command": f"{{python}} {tmp_path}/tool.py {{artifact}} {tmp_path}/tool.log",
             "metrics": ["cost"], "cutoff": {"metric": "cost", "below": 35}}
    if estimate is not None:
        stage["estimate"] = estimate
    return {"id": "est", "statement": "the cheapest x", "language": "text",
            "flow": {"orchestrate": {"policy": "sweep", "space": {"x": list(xs)}},
                     "generate": {"command": f"{{python}} {tmp_path}/gen.py {{artifact}} {{x}}"},
                     "test": {"test": ["true"]}, "measure": {"place": stage}},
            "objectives": [{"metric": "cost", "direction": "minimize"}], "budget": {"steps": 2, "batch": 100}, **more}


def _run(tmp_path: Path, doc: dict, proposer=None, db: str = "e.db"):
    task = TaskSpec.from_dict(doc, base=tmp_path)
    problem = PromptProblem(task)
    said: list[str] = []
    out = run_loop(problem, request_for(task, db=str(tmp_path / db)), proposer=proposer, log=said.append)
    return out, problem, said


def _tool_runs(tmp_path: Path) -> list[int]:
    log = tmp_path / "tool.log"
    return sorted(int(x) for x in log.read_text().split()) if log.exists() else []


def test_the_nearest_measured_designs_predict_a_new_one():
    rows = [({"width": 1, "kind": "a"}, {"f": 10.0}), ({"width": 3, "kind": "a"}, {"f": 30.0}),
            ({"width": 5, "kind": "b"}, {"f": 50.0})]
    assert predict_knn(rows, {"width": 3, "kind": "a"}, "f") == 30.0                  # an exact match is itself
    assert 10.0 < predict_knn(rows, {"width": 2, "kind": "a"}, "f", k=2) < 30.0        # between its neighbours
    assert predict_knn(rows, {"width": 2, "kind": "a"}, "nope") is None
    numeric = [({"width": w}, {"f": 120.0 * w}) for w in (1, 2, 3, 4)]
    assert abs(predict_fit(numeric, {"width": 9}, "f") - 1080.0) < 1e-6              # the plane extrapolates
    assert predict_fit(numeric, {"width": 9, "kind": "a"}, "f") is None              # a categorical knob: no plane
    assert predict_fit(numeric[:2], {"width": 9}, "f") is None                        # too few rows


def test_the_surrogate_estimates_nothing_until_the_record_has_rows_then_skips(tmp_path):
    est = {"kind": "surrogate", "margin": 0.05}
    out, _p, _s = _run(tmp_path, _doc(tmp_path, [1, 2, 3], est))
    assert _tool_runs(tmp_path) == [1, 2, 3], "no rows yet: no estimate, the tool runs on every design"
    assert out.provenance["estimates"] == {}
    # the same record, three more points: the fit over x=1..3 says 40, 50, 60 -- over 35 by more than 5%
    out, problem, said = _run(tmp_path, _doc(tmp_path, [1, 2, 3, 4, 5, 6], est))
    assert _tool_runs(tmp_path) == [1, 2, 3], "the three estimated to fail never reached the tool"
    assert out.provenance["estimates"] == {"place": {"skipped": 3, "measured": 0}}
    why = dict(out.refused)
    assert why["x=4"].startswith("place: estimated cost 40 fails cost <= 35 (the cutoff) by more than 5%"), why
    assert any("place: 3 estimated to fail, skipped" in m for m in said)
    from flux_loop.task import task_report_lines

    assert "  estimates: place: 3 estimated to fail, skipped; 0 measured" in task_report_lines(problem.task, out, problem)
    assert out.decision is not None and out.decision.candidate.knobs == {"x": 1}


def test_a_command_estimator_skips_past_the_margin_and_its_estimate_is_on_the_row(tmp_path):
    (tmp_path / "guess.py").write_text(
        "import sys; x = int(open(sys.argv[1]).read().split('=')[1]); print(f'cost={ {1: 10, 2: 20, 3: 38.5, 4: 38.6}[x] }')")
    est = {"kind": "command", "margin": 0.1, "command": f"{{python}} {tmp_path}/guess.py {{artifact}}"}
    out, problem, _said = _run(tmp_path, _doc(tmp_path, [1, 2, 3, 4], est))
    # 35 with a 10% margin is 38.5: an estimate on the boundary runs, one past it is skipped
    assert _tool_runs(tmp_path) == [1, 2, 3]
    assert out.provenance["estimates"] == {"place": {"skipped": 1, "measured": 3}}
    assert "estimated cost 38.6 fails cost <= 35 (the cutoff) by more than 10%" in dict(out.refused)["x=4"]
    rows = problem.open_records(request_for(problem.task, db=str(tmp_path / "e.db")), lambda _m: None).known_rows(stage="place")
    got = {r.candidate["x"]: (r.metrics["cost"], r.candidate["meta"]["provenance"]["estimate"]) for r in rows}
    assert got[1] == (10.0, {"cost": 10.0}) and got[3] == (30.0, {"cost": 38.5}), "measured beside its estimate"


def test_the_model_estimates_against_an_objective_limit_and_no_model_estimates_nothing(tmp_path):
    doc = _doc(tmp_path, [1, 2, 3], {"kind": "model", "margin": 0.0})
    doc["flow"]["measure"]["place"].pop("cutoff")
    doc["objectives"] = [{"metric": "cost", "direction": "minimize", "goal": 25}]
    reply = json.dumps({"estimates": [{"index": 0, "cost": 10}, {"index": 1, "cost": 20}, {"index": 2, "cost": 90}]})
    model = ScriptedProposer([reply])
    out, _p, _s = _run(tmp_path, doc, proposer=model)
    assert _tool_runs(tmp_path) == [1, 2]
    assert "fails cost <= 25 (the objective's limit)" in dict(out.refused)["x=3"]
    assert "ESTIMATE what the place stage will measure (cost)" in model.prompts[0]
    (tmp_path / "tool.log").unlink()
    out, _p, _s = _run(tmp_path, doc, proposer=None, db="none.db")
    assert _tool_runs(tmp_path) == [1, 2, 3] and out.provenance["estimates"] == {}, "no model: the tool runs"


@pytest.mark.parametrize("estimate, message", [
    ({"kind": "oracle"}, "kind is one of surrogate, command, model"),
    ({"kind": "surrogate", "margin": -0.1}, "margin is a number >= 0"),
    ({"kind": "command"}, "command is said for kind command, and only then"),
    ({"kind": "model", "command": "x"}, "command is said for kind command, and only then"),
    ({"kind": "surrogate", "wrong": 1}, "keys \\['wrong'\\] are not known"),
    ("surrogate", "estimate is \\{kind"),
    ({"kind": "command", "command": "{python} e.py {nope}"}, "estimate place says \\{nope\\}"),
])
def test_a_wrong_estimate_is_refused_at_load(tmp_path, estimate, message):
    with pytest.raises(TaskError, match=message):
        TaskSpec.from_dict(_doc(tmp_path, [1], estimate))


def test_the_flow_shows_each_stage_with_its_estimator_and_the_old_boxes_are_gone(tmp_path):
    task = TaskSpec.from_dict(_doc(tmp_path, [1], {"kind": "surrogate", "margin": 0.05}))
    lines = describe_flow(task)
    assert "stages: place" in lines
    assert ("stage place: its command; cutoff cost <= 35 -- estimate: surrogate (a fit over the record's rows on this "
            "stage, from 3 rows); skipped when it fails a cutoff or limit by more than 5%") in lines
    assert not any(line.startswith(("analytical:", "simulation:")) for line in lines)
    assert TaskSpec.from_dict(task.to_dict()) == task
    for box in ("analytical", "simulation", "records"):
        doc = _doc(tmp_path, [1])
        doc["flow"][box] = ["place"] if box != "records" else "on"
        with pytest.raises(TaskError, match="not a box of the drawing"):
            TaskSpec.from_dict(doc)


# ---------------------------------------------------------------- D666: the loop fixes
@pytest.mark.parametrize("dse", ["pareto", {"pareto": {"budget": 4}}, [{"policy": "sweep"}, {"policy": "pareto"}]])
def test_pareto_with_one_objective_is_refused_at_load(tmp_path, dse):
    doc = _doc(tmp_path, [1, 2])
    doc["flow"]["orchestrate"] = {**doc["flow"]["orchestrate"], "policy": dse}
    with pytest.raises(TaskError, match="pareto needs two objectives"):
        TaskSpec.from_dict(doc)
    doc["objectives"].append({"metric": "speed", "direction": "maximize"})
    TaskSpec.from_dict(doc)


def test_feedback_none_reloads_no_notes_and_hands_the_passes_no_channel(tmp_path, monkeypatch):
    import flux_feedback
    from flux_feedback import Note
    from flux_loop.passes import run_passes

    monkeypatch.setattr(flux_feedback, "reload_notes", lambda *_a, **_k: [Note(text="OLD", received_at=0.0, origin="earlier-run")])
    heard, _p, _s = _run(tmp_path, _doc(tmp_path, [1]))
    assert heard.notes == ["OLD"]
    doc = _doc(tmp_path, [1])
    doc["flow"]["feedback"] = "off"
    deaf, _p, _s = _run(tmp_path, doc, db="deaf.db")
    assert deaf.notes == []
    seen = []
    task = TaskSpec.from_dict(doc)
    run_passes(lambda req, feed: seen.append(feed) or heard, request_for(task), passes=1,
               feedback=flux_feedback.scripted_channel("wake up"), notes=False, say=lambda _m: None)
    assert seen == [None]


def test_a_coding_agent_plans_without_a_model(tmp_path):
    agent = tmp_path / "agent.py"
    agent.write_text("import sys, pathlib; pathlib.Path(sys.argv[1]).with_name('SEEN').write_text('x'); "
                     "open(sys.argv[2], 'w').write('{\"why\": \"PLANNED\"}')")
    doc = _doc(tmp_path, [1, 2])
    doc["flow"]["plan"] = {"by": {"command": ["{python}", str(agent), "{prompt_file}", "{artifact}"], "timeout_s": 60}}
    _out, _p, said = _run(tmp_path, doc, proposer=None)
    assert any("plan (the agent)" in m or "plan (the defaults + the agent)" in m for m in said), said


def test_finalists_apply_with_one_objective(tmp_path):
    doc = _doc(tmp_path, [1, 2, 3, 4])
    measure = doc["flow"]["measure"]
    measure["place"].pop("cutoff")
    measure["confirm"] = dict(measure["place"])
    doc["flow"]["select"] = {"finalists": 2}
    out, _p, _s = _run(tmp_path, doc)
    assert sorted(s.candidate.knobs["x"] for s in out.scored if s.stage == "confirm") == [1, 2]


def _box(lines, name):
    return next(line for line in lines if line.startswith(name + ":"))


def test_the_flow_says_what_the_defaults_and_the_agents_do(tmp_path):
    one = {"id": "d", "statement": "s", "flow": {"test": {"test": ["true"]}}}
    lines = describe_flow(TaskSpec.from_dict(one))
    assert _box(lines, "orchestrate") == (
        "orchestrate: default (one design, no part to pick; rules pick the kind of work: a design sent back is "
        "improved first, then the parts, then the search) -- or: rules, given, model, tools, an agent")
    parts = describe_flow(TaskSpec.from_dict({**one, "parts": ["a", "b"]}))
    assert _box(parts, "orchestrate").startswith(
        "orchestrate: default (the model picks the next part, the first one waiting without a model; rules pick")
    assert _box(lines, "knowledge") == "knowledge: library (on by default, its papers digested; `flow.knowledge: off` turns it off)"
    assert _box(lines, "lessons") == "lessons: off (nothing is mined from the record) -- or: mined, an agent"
    assert _box(lines, "records").startswith("records: always on")
    agents = describe_flow(TaskSpec.from_dict({**one, "flow": {**one.get("flow", {}), "knowledge": {"lessons": {"by": "opencode"}}, "orchestrate": {"by": "opencode"}}}))
    assert _box(agents, "lessons") == "lessons: agent opencode (lessons from the record's rows, each citing its rows)"
    assert _box(agents, "orchestrate").startswith("orchestrate: agent opencode (a coding agent picks the next part")
    model = describe_flow(TaskSpec.from_dict({**one, "flow": {**one.get("flow", {}), "orchestrate": "tools"}}))
    assert _box(model, "orchestrate").startswith("orchestrate: tools (the model with tools picks the next part")
    off = describe_flow(TaskSpec.from_dict({**one, "flow": {**one.get("flow", {}), "knowledge": "off", "feedback": "off"}}))
    assert _box(off, "knowledge") == "knowledge: off (the library is off)"
    assert _box(off, "feedback") == "feedback: off (no notes are read, reloaded or waited for)"
