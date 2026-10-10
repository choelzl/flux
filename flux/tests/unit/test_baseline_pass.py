"""Pass 0 exercises unchanged inputs and real tools without consulting a model."""

from __future__ import annotations

import dataclasses
import json
import subprocess
import sys

import pytest

from flux_loop import LoopRequest, load_task, run_loop
from flux_loop.types import LoopState
from flux_loop.document import TaskSpec, request_for
from flux_loop.document.keys import TaskError
from flux_loop.document.library import confined
from flux_loop.journal import Journal, read_events
from flux_loop.passes import run_passes
from flux_loop.records import history
from flux_loop.task import PromptProblem, model_use
import flux_profile


class NoModel:
    def __getattr__(self, name):
        raise AssertionError(f"baseline asked a model for {name}")


@dataclasses.dataclass
class PassResult:
    at_rest: bool = False
    explorable: bool = True


def test_supplied_baseline_records_values_without_tools_models_or_a_decision(tmp_path):
    task = _task(tmp_path, {"only": True, "metrics": [{"metric": "cost", "value": 12},
                        {"metric": "cost", "value": 20, "stage": "coarse"}]})
    doc = task.to_dict()
    doc["flow"]["test"] = {"check": "missing-baseline-check"}
    for stage in doc["flow"]["measure"].values():
        stage["command"] = "missing-baseline-measure"
    task = TaskSpec.from_dict(doc, base=tmp_path)
    problem = PromptProblem(task)
    req = request_for(task, db=str(tmp_path / "out.db"))
    assert problem.tools_missing() == problem.baseline_tools_missing() == []
    assert model_use(task) == ""
    for reused in (False, True):
        out = run_loop(problem, req, proposer=NoModel(), log=lambda m: None)
        assert out.stopped == "baseline metrics recorded", out.refused
        assert out.decision is None and not out.admitted and not out.refused
        assert [(s.stage, s.metrics) for s in out.scored] == [("coarse", {"cost": 20}), ("fine", {"cost": 12})]
        assert out.provenance.get("baseline_reused", False) == reused
        assert out.provenance["measurements"] == 0
    records = problem.open_records(req, lambda m: None)
    rows = [t for t in records.store.trials(records.campaign_id) if t.stage in problem.stages()]
    assert len(rows) == 2
    assert all(t.candidate["meta"]["baseline_metrics"] and t.status == "ok" for t in rows)
    records.close("paused")
    assert not (tmp_path / "out/checks.txt").exists() and not (tmp_path / "out/tools.txt").exists()


def test_changed_supplied_metrics_get_a_new_reference_and_preserve_the_real_decision(tmp_path):
    task = _task(tmp_path, {"file": "design.py", "only": True})
    req = request_for(task, db=str(tmp_path / "out.db"))
    from flux_loop.measure import cached_measure
    from flux_loop.types import Candidate, Scored

    problem = PromptProblem(dataclasses.replace(task, baseline=None))
    records = problem.open_records(req, lambda m: None)
    seed = LoopState(request=dataclasses.replace(req, baseline=False), workdir=str(tmp_path / "out"),
                     say=lambda m: None, proposer=None, feedback=None, records=records)
    cand = Candidate("existing-design", artifact=(tmp_path / "design.py").read_text())
    for stage in problem.stages():
        metrics = cached_measure(problem, seed, cand, stage, record=True)
    records.conclude(problem.conclusion(Scored(cand, "fine", metrics, {}), "search pass"))
    records.close("paused")
    for value in (1, 2):  # an external reference is numerically better, but has no design to choose
        doc = task.to_dict()
        doc["baseline"] = {"only": True, "metrics": [{"metric": "cost", "value": value}]}
        supplied = TaskSpec.from_dict(doc, base=tmp_path)
        out = run_loop(PromptProblem(supplied), request_for(supplied, db=req.db), proposer=NoModel(), log=lambda m: None)
        assert not out.provenance.get("baseline_reused")
        assert out.decision.metrics == {"cost": 7}
        assert out.decision.candidate.artifact.strip().endswith("answer = 42")
        assert out.scored[0].metrics == {"cost": value}
    assert not (tmp_path / "out/checks.txt").exists()


@pytest.mark.parametrize("rows", [[], {}, [1], [{"metric": "cost"}], [{"metric": "", "value": 1}],
    [{"metric": "cost", "value": True}], [{"metric": "cost", "value": "1"}],
    [{"metric": "cost", "value": float("nan")}], [{"metric": "cost", "value": float("inf")}],
    [{"metric": "cost", "value": 1, "stage": "missing"}], [{"metric": "unknown", "value": 1}],
    [{"metric": "cost", "value": 1, "extra": True}],
    [{"metric": "cost", "value": 1}, {"metric": "cost", "value": 2, "stage": "fine"}]])
def test_invalid_supplied_baseline_values_are_refused(tmp_path, rows):
    with pytest.raises(TaskError, match="baseline.metrics"):
        _task(tmp_path, {"metrics": rows})


@pytest.mark.parametrize("source", ["file", "command"])
def test_supplied_values_cannot_be_combined_with_a_baseline_source(tmp_path, source):
    with pytest.raises(TaskError, match="baseline takes"):
        _task(tmp_path, {source: "true", "metrics": [{"metric": "cost", "value": 1}]})


def _task(tmp_path, baseline, *, gate_exit=0, measurement_exit=0):
    (tmp_path / "out").mkdir(exist_ok=True)
    (tmp_path / "design.py").write_text("# unchanged design\nanswer = 42\n")
    (tmp_path / "check.py").write_text(
        "import pathlib, sys\n"
        "home, artifact = map(pathlib.Path, sys.argv[1:])\n"
        "with (home / 'out' / 'checks.txt').open('a') as f: f.write('checked\\n')\n"
        f"sys.exit({gate_exit})\n")
    (tmp_path / "measure.py").write_text(
        "import pathlib, sys\n"
        "home = pathlib.Path(sys.argv[1])\n"
        "with (home / 'out' / 'tools.txt').open('a') as f: f.write(sys.argv[2] + '\\n')\n"
        "print('cost=7')\n"
        f"sys.exit({measurement_exit})\n")
    (tmp_path / "prepare.py").write_text(
        "import pathlib, sys\n"
        "pathlib.Path(sys.argv[1]).write_text('# unchanged design\\nanswer = 42\\n')\n"
        "print('debug output', file=sys.stderr)\n")
    return TaskSpec.from_dict({
        "id": "baseline", "statement": "Improve the project", "language": "python", "baseline": baseline,
        "flow": {"validate": "model", "plan": "model", "critique": "model", "select": "claude",
                 "generate": {"command": "absent-generator-must-not-run {artifact}"},
                 "test": {"check": {"run": "{python} {home}/check.py {home} {artifact}"}},
                 "measure": {name: {"command": f"{{python}} {{home}}/measure.py {{home}} {name}",
                                     "metrics": ["cost"], "estimate": {"kind": "model", "margin": 0.05},
                                     "cutoff": {"metric": "cost", "below": 1}}
                             for name in ("coarse", "fine")}},
        "objectives": [{"metric": "cost", "direction": "minimize"}],
    }, base=tmp_path)


@pytest.mark.parametrize("source", ["project", "file", "command"])
def test_baseline_runs_real_checks_and_every_stage_without_agents_or_edits(tmp_path, source):
    config = {"only": True}
    if source == "file":
        config["file"] = "design.py"
    if source == "command":
        config["command"] = "{python} {home}/prepare.py {artifact}"
    task = _task(tmp_path, config)
    original = (tmp_path / "design.py").read_bytes()
    problem = PromptProblem(task)
    req = request_for(task, db=str(tmp_path / "out.db"), screen_only=True, repair_attempts=100)
    assert problem.tools_missing() == []  # the normal generator is deliberately absent
    assert model_use(task) == ""
    for start in range(2):
        out = run_passes(lambda r, f: run_loop(problem, r, proposer=NoModel(), log=lambda m: None), req, say=lambda m: None)
        assert out.stopped == "baseline checked and measured", out.refused
        assert out.decision is None
        assert [s.stage for s in out.scored] == ["coarse", "fine"]
        assert out.provenance["cache_hits"] == 0
        assert out.provenance.get("baseline_reused", False) == bool(start)
        assert not out.admitted
        if source != "project":
            assert out.scored[0].candidate.artifact.encode() == original
    assert (tmp_path / "design.py").read_bytes() == original
    assert (tmp_path / "out/checks.txt").read_text().splitlines() == ["checked"]
    assert (tmp_path / "out/tools.txt").read_text().splitlines() == ["coarse", "fine"]
    if source == "project":
        state = LoopState(request=req, say=lambda m: None, proposer=None, feedback=None,
                          records=problem.open_records(req, lambda m: None))
        assert history(problem, state) == []
        assert all(t.status == "ok" for t in state.records.store.trials(state.records.campaign_id))


@pytest.mark.parametrize("gate_exit,measurement_exit,stages", [(1, 0, []), (3, 0, []), (0, 1, ["coarse"])])
def test_baseline_failure_is_recorded_and_retried_without_repairs(tmp_path, gate_exit, measurement_exit, stages):
    task = _task(tmp_path, {"only": True}, gate_exit=gate_exit, measurement_exit=measurement_exit)
    req = request_for(task, db=str(tmp_path / "out.db"))
    out = run_loop(PromptProblem(task), req, proposer=NoModel(), log=lambda m: None)
    assert out.stopped == "baseline failed" and out.refused
    assert out.decision is None
    tools = tmp_path / "out/tools.txt"
    assert (tools.read_text().splitlines() if tools.exists() else []) == stages
    assert (tmp_path / "out/checks.txt").read_text().splitlines() == ["checked"]
    retried = run_loop(PromptProblem(task), req, proposer=NoModel(), log=lambda m: None)
    assert not retried.provenance.get("baseline_reused")
    assert retried.stopped == out.stopped and retried.refused == out.refused
    assert retried.decision is None
    assert (tmp_path / "out/checks.txt").read_text().splitlines() == ["checked", "checked"]
    assert (tools.read_text().splitlines() if tools.exists() else []) == stages * 2


@pytest.mark.parametrize("failure,stages", [("check", []), ("coarse", ["coarse"]), ("fine", ["coarse", "fine"])])
def test_failed_baseline_recovers_with_unchanged_inputs_then_reuses_success(tmp_path, failure, stages):
    task = _task(tmp_path, {"file": "design.py", "only": True})
    # A tool becomes available again without changing source, configuration or executable.
    # The marker lives in generated output, which is deliberately outside the input fingerprint.
    if failure == "check":
        script, exit_code = tmp_path / "check.py", "1"
    else:
        script, exit_code = tmp_path / "measure.py", f"int(sys.argv[2] == {failure!r})"
    script.write_text(script.read_text().replace("sys.exit(0)",
        f"sys.exit(0 if (home / 'out' / 'tools-ready').exists() else {exit_code})"))
    problem = PromptProblem(task)
    req = request_for(task, db=str(tmp_path / "out.db"))
    failed = run_loop(problem, req, proposer=NoModel(), log=lambda m: None)
    assert failed.stopped == "baseline failed" and failed.refused
    assert [s.stage for s in failed.scored] == (["coarse"] if failure == "fine" else [])
    (tmp_path / "out/tools-ready").touch()
    recovered = run_loop(problem, req, proposer=NoModel(), log=lambda m: None)
    assert not recovered.provenance.get("baseline_reused")
    assert recovered.stopped == "baseline checked and measured" and not recovered.refused
    assert recovered.decision is None
    assert [s.stage for s in recovered.scored] == ["coarse", "fine"]
    records = problem.open_records(req, lambda m: None)
    snapshots = records.recall("baseline")
    records.close("paused")
    assert [s["ok"] for s in snapshots] == [False, True]
    assert snapshots[0]["fingerprint"] == snapshots[1]["fingerprint"]
    reused = run_loop(problem, req, proposer=NoModel(), log=lambda m: None)
    assert reused.provenance["baseline_reused"]
    assert reused.stopped == recovered.stopped and not reused.refused
    assert reused.decision is None
    assert [s.stage for s in reused.scored] == ["coarse", "fine"]
    assert (tmp_path / "out/checks.txt").read_text().splitlines() == ["checked", "checked"]
    assert (tmp_path / "out/tools.txt").read_text().splitlines() == stages + ["coarse", "fine"]
    assert (tmp_path / "design.py").read_text() == "# unchanged design\nanswer = 42\n"


@pytest.mark.parametrize("best_answer", [60, 20])
@pytest.mark.parametrize("fail_baseline", [False, True])
def test_late_baseline_decides_against_retained_designs_on_fresh_and_reused_runs(tmp_path, best_answer, fail_baseline):
    from flux_loop.measure import cached_measure
    from flux_loop.types import Candidate, Scored
    from flux_web.results import decision_doc

    task = _task(tmp_path, {"file": "design.py", "only": True})
    doc = task.to_dict()
    for stage in doc["flow"]["measure"].values():
        stage["command"].append("{artifact}")
    (tmp_path / "measure.py").write_text(
        "import pathlib, re, sys\n"
        "answer = int(re.search(r'answer = (\\d+)', pathlib.Path(sys.argv[3]).read_text()).group(1))\n"
        "print('cost=' + str(100 - answer))\n"
        "sys.exit(int(answer == 42 and sys.argv[2] == 'fine' and (pathlib.Path(sys.argv[1]) / 'out/fail-baseline').exists()))\n")
    problem = PromptProblem(TaskSpec.from_dict(doc, base=tmp_path))
    earlier_problem = PromptProblem(dataclasses.replace(problem.task, baseline=None))
    req = request_for(earlier_problem.task, db=str(tmp_path / "out.db"))
    records = earlier_problem.open_records(req, lambda m: None)
    seed = LoopState(request=dataclasses.replace(req, baseline=False), workdir=str(tmp_path / "out"),
                     say=lambda m: None, proposer=None, feedback=None, records=records)
    cand = Candidate("earlier-design", artifact=f"answer = {best_answer}\n")
    for stage in earlier_problem.stages():
        metrics = cached_measure(earlier_problem, seed, cand, stage, record=True)
    records.conclude(earlier_problem.conclusion(Scored(cand, "fine", metrics, {}), "earlier pass"))
    records.close("paused")
    req = request_for(problem.task, db=req.db)  # Enable pass 0 after earlier normal passes were measured.
    if fail_baseline:
        (tmp_path / "out/fail-baseline").touch()
    for reuse in (False, True):
        out = run_loop(problem, req, proposer=NoModel(), log=lambda m: None)
        assert bool(out.provenance.get("baseline_reused")) == (reuse and not fail_baseline)
        assert [s.metrics for s in out.scored] == [{"cost": 58.0}] * (1 if fail_baseline else 2)
        assert out.decision.metrics == {"cost": 100.0 - best_answer}
        assert out.decision.name == "earlier-design"
        assert decision_doc(req.db)["name"] == out.decision.name


@pytest.mark.parametrize("changed_inputs", [False, True])
def test_baseline_with_the_same_source_does_not_rename_the_retained_decision(tmp_path, changed_inputs):
    from flux_loop.measure import cached_measure
    from flux_loop.types import Candidate, Scored
    from flux_web.results import decision_doc

    task = _task(tmp_path, {"file": "design.py", "only": True})
    earlier = PromptProblem(dataclasses.replace(task, baseline=None))
    req = request_for(earlier.task, db=str(tmp_path / "out.db"))
    records = earlier.open_records(req, lambda m: None)
    seed = LoopState(request=req, workdir=str(tmp_path / "out"), say=lambda m: None,
                     proposer=None, feedback=None, records=records)
    cand = Candidate("existing-design", artifact=(tmp_path / "design.py").read_text())
    for stage in earlier.stages():
        metrics = cached_measure(earlier, seed, cand, stage, record=True)
    records.conclude(earlier.conclusion(Scored(cand, "fine", metrics, {}), "earlier pass"))
    records.close("paused")
    if changed_inputs:
        checker = tmp_path / "check.py"
        checker.write_text(checker.read_text() + "\n# updated checks invalidate older evidence\n")
    for _ in range(2):
        out = run_loop(PromptProblem(task), request_for(task, db=req.db), proposer=NoModel(), log=lambda m: None)
        if changed_inputs:
            assert out.decision is None  # stale search evidence cannot be newly selected
        else:
            assert out.decision is not None and out.decision.name == "existing-design"
        assert decision_doc(req.db)["name"] == "existing-design"


@pytest.mark.parametrize("change", ["source", "checker", "measurement", "params", "settings", "tools", "environment", "external"])
def test_baseline_reruns_when_evidence_inputs_change(tmp_path, monkeypatch, change):
    task = _task(tmp_path, {"file": "design.py", "only": True})
    if change == "external":
        external = tmp_path.parent / (tmp_path.name + "-baseline.py")
        external.write_text("answer = 42\n")
        task = dataclasses.replace(task, baseline={"file": str(external), "only": True})
    problem = PromptProblem(task)
    req = request_for(task, db=str(tmp_path / "out.db"))

    def run():
        return run_loop(problem, req, proposer=NoModel(), log=lambda m: None)

    run()
    assert run().provenance["baseline_reused"]
    if change in ("source", "checker", "measurement", "external"):
        path = external if change == "external" else tmp_path / {"source": "design.py", "checker": "check.py", "measurement": "measure.py"}[change]
        path.write_text(path.read_text() + "# input changed\n")
    elif change == "params":
        problem.task = dataclasses.replace(task, params={"size": 10})
    elif change == "settings":
        problem.task = dataclasses.replace(task, baseline={**task.baseline, "timeout_s": 10})
    elif change == "environment":
        monkeypatch.setenv("PYTHONPATH", "/new/sandbox/packages")
    else:
        monkeypatch.setattr("flux_loop.toolchain.toolchain_fingerprint", lambda tools: {"yosys": "new-build"})
    changed = run()
    assert not changed.provenance.get("baseline_reused")
    assert changed.decision is None
    assert (tmp_path / "out/checks.txt").read_text().splitlines() == ["checked", "checked"]
    assert run().provenance["baseline_reused"]


def test_generated_outputs_do_not_invalidate_baseline(tmp_path):
    task = _task(tmp_path, {"only": True})
    req = request_for(task, db=str(tmp_path / "out.db"))
    run_loop(PromptProblem(task), req, proposer=NoModel(), log=lambda m: None)
    for folder in ("out", "runs", "workbench", "library", ".cache"):
        path = tmp_path / folder
        path.mkdir(exist_ok=True)
        (path / "new.py").write_text("generated data\n")
    out = run_loop(PromptProblem(task), req, proposer=NoModel(), log=lambda m: None)
    assert out.provenance["baseline_reused"]
    assert out.provenance["measurements"] == 0


def test_baseline_without_a_record_runs_every_time(tmp_path):
    task = _task(tmp_path, {"only": True})
    for _ in range(2):
        out = run_loop(PromptProblem(task), request_for(task), proposer=NoModel(), log=lambda m: None)
        assert not out.provenance.get("baseline_reused")
    assert (tmp_path / "out/checks.txt").read_text().splitlines() == ["checked", "checked"]


@pytest.mark.parametrize("legacy", [True, False])
def test_old_or_incomplete_baseline_snapshot_runs_tools_again(tmp_path, legacy):
    task = _task(tmp_path, {"only": True})
    req = request_for(task, db=str(tmp_path / "out.db"))
    problem = PromptProblem(task)
    run_loop(problem, req, proposer=NoModel(), log=lambda m: None)
    records = problem.open_records(req, lambda m: None)
    saved = records.recall("baseline")[-1]
    saved.pop("fingerprint" if legacy else "stopped")
    records.remember("baseline", saved)
    records.close("paused")
    out = run_loop(problem, req, proposer=NoModel(), log=lambda m: None)
    assert not out.provenance.get("baseline_reused")
    assert len(out.scored) == 2  # an incomplete restore must not leave duplicate measurements
    assert (tmp_path / "out/checks.txt").read_text().splitlines() == ["checked", "checked"]


def test_input_change_during_baseline_is_not_blessed_as_unchanged(tmp_path):
    task = _task(tmp_path, {"only": True})
    check = tmp_path / "check.py"
    check.write_text(check.read_text().replace("sys.exit(0)",
        "source = home / 'design.py'\n"
        "if 'answer = 43' not in source.read_text(): source.write_text('# changed\\nanswer = 43\\n')\n"
        "sys.exit(0)"))
    req = request_for(task, db=str(tmp_path / "out.db"))
    for _ in range(2):
        out = run_loop(PromptProblem(task), req, proposer=NoModel(), log=lambda m: None)
        assert not out.provenance.get("baseline_reused")
    assert run_loop(PromptProblem(task), req, proposer=NoModel(), log=lambda m: None).provenance["baseline_reused"]
    assert (tmp_path / "out/checks.txt").read_text().splitlines() == ["checked", "checked"]


def test_baseline_precedes_parallel_work_without_spending_a_normal_pass(tmp_path, monkeypatch):
    monkeypatch.delenv("FLUX_PARALLEL_MAX", raising=False)
    monkeypatch.setattr("flux_loop.ops.stop_requested", lambda: None)
    seen = []
    journal = Journal(str(tmp_path / "events.jsonl"))
    flux_profile.add_listener(journal)
    try:
        def run(req, feed):
            seen.append((req.baseline, req.steps, flux_profile._tags().get("pass")))
            with flux_profile.phase("check: unchanged" if req.baseline else "generation"):
                return PassResult()
        run_passes(run, LoopRequest(baseline=True, parallel=2, steps=3), passes=2, say=lambda m: None)
    finally:
        flux_profile.remove_listener(journal)
    assert seen[0] == (True, 0, 0)
    assert sorted(seen[1:3]) == [(False, 3, 1), (False, 3, 2)]
    assert seen[-1][:2] == (False, 0)  # final decision over the parallel passes
    marks = [json.loads(e["why"]) for e in read_events(str(tmp_path / "events.jsonl"))[0]
             if e["ev"] == "mark" and e["name"] == "pass"]
    assert marks[0] == {"n": 0, "baseline": True}


@pytest.mark.parametrize("baseline_source", ["project", "failed", "provided"])
@pytest.mark.parametrize("answer", [20, 43])
def test_normal_generation_starts_after_baseline_even_when_baseline_fails(tmp_path, baseline_source, answer):
    from flux_llm import ScriptedProposer

    base = _task(tmp_path, {"file": "design.py"})
    doc = base.to_dict()
    doc["flow"] = {"test": doc["flow"]["test"], "measure": doc["flow"]["measure"],
                   "orchestrate": "rules", "generate": "model", "feedback": "off"}
    for stage in doc["flow"]["measure"].values():
        stage.pop("estimate")
        stage.pop("cutoff")
        stage["command"].append("{artifact}")
    (tmp_path / "measure.py").write_text(
        "import pathlib, re, sys\n"
        "answer = int(re.search(r'answer = (\\d+)', pathlib.Path(sys.argv[3]).read_text()).group(1))\n"
        "print('cost=' + str(100 - answer))\n")
    doc["budget"] = {"prototype": False, "steps": 1, "critique_rounds": 0}
    if baseline_source == "failed":
        doc["baseline"]["file"] = "absent.py"
    elif baseline_source == "provided":
        doc["baseline"] = {"metrics": [{"metric": "cost", "value": 0}]}
    task = TaskSpec.from_dict(doc, base=tmp_path)
    artifact = f"answer = {answer}\n"
    proposer = ScriptedProposer([json.dumps({"artifact": artifact, "why": "an alternative"})])
    out = run_passes(lambda r, f: run_loop(PromptProblem(task), r, proposer=proposer, log=lambda m: None),
                     request_for(task, db=str(tmp_path / "out.db")), passes=1, say=lambda m: None)
    assert proposer.prompts  # normal work follows pass 0, rather than ending with its result
    assert out.provenance["request"]["baseline"] is False
    assert any(s.candidate.artifact.strip() == artifact.strip() for s in out.scored), (out.refused, out.scored)
    assert out.decision.candidate.artifact.strip() == artifact.strip()
    assert not any(s.candidate.meta.get("baseline") for s in out.scored)
    resumed = run_loop(PromptProblem(task), dataclasses.replace(request_for(task, db=str(tmp_path / "out.db")),
                       baseline=False, baseline_only=False, steps=0), proposer=NoModel(), log=lambda m: None)
    assert resumed.decision.candidate.artifact.strip() == artifact.strip()
    assert not any(s.candidate.meta.get("baseline") for s in resumed.scored)
    assert (tmp_path / "design.py").read_text() == "# unchanged design\nanswer = 42\n"


@pytest.mark.parametrize("bad", ["design.py", 1, [], {"file": ""}, {"command": ""},
                                {"file": "x", "command": "true"}, {"only": "yes"},
                                {"timeout_s": 0}, {"timeout_s": float("inf")}, {"unknown": True},
                                {"command": "true {unknown}"}, {"command": "echo 'unterminated"}])
def test_invalid_baseline_settings_are_refused(bad):
    with pytest.raises(TaskError, match="baseline"):
        TaskSpec.from_dict({"id": "baseline", "statement": "check", "flow": {"test": "true"}, "baseline": bad})


def test_baseline_file_is_confined_when_web_reads_a_document(tmp_path):
    loop = tmp_path / "loop"
    loop.mkdir()
    outside = tmp_path / "secret.py"
    outside.write_text("secret")
    (loop / "link.py").symlink_to(outside)
    with confined(loop), pytest.raises(TaskError, match="outside"):
        TaskSpec.from_dict({"id": "loop", "statement": "check", "flow": {"test": "true"}, "baseline": {"file": "link.py"}}, base=loop)


@pytest.mark.parametrize("source,gate_exit", [("file", 0), ("project", 0), ("file", 1)])
def test_baseline_only_cli_runs_without_model_or_generator(tmp_path, source, gate_exit):
    task = _task(tmp_path, {"file": "design.py", "only": True} if source == "file" else {"only": True}, gate_exit=gate_exit)
    doc = tmp_path / "problem.yaml"
    import yaml

    written = task.to_dict()
    written.pop("id")
    doc.write_text(yaml.safe_dump(written))
    loaded = load_task(doc)
    assert loaded.baseline == task.baseline
    result = subprocess.run([sys.executable, "-m", "flux_cli.main", "task", "run", str(doc), "--no-sandbox"],
                            text=True, capture_output=True, timeout=60)
    assert result.returncode == gate_exit, result.stdout + result.stderr
    assert "pass 0: baseline" in result.stdout and "pass 1" not in result.stdout
    assert ("baseline failed" if gate_exit else "baseline checked and measured") in result.stdout
    if source == "project":
        assert not (tmp_path / "out" / f"{tmp_path.name}.py").exists()
