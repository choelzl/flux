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
        assert out.decision.metrics == {"cost": 7.0}
        assert [s.stage for s in out.scored] == ["coarse", "fine"]
        assert out.provenance["cache_hits"] == 0
        assert out.provenance.get("baseline_reused", False) == bool(start)
        assert bool(out.admitted) == (source != "project")
        if source != "project":
            assert out.decision.candidate.artifact.encode() == original
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
    assert recovered.decision.metrics == {"cost": 7.0}
    assert [s.stage for s in recovered.scored] == ["coarse", "fine"]
    records = problem.open_records(req, lambda m: None)
    snapshots = records.recall("baseline")
    records.close("paused")
    assert [s["ok"] for s in snapshots] == [False, True]
    assert snapshots[0]["fingerprint"] == snapshots[1]["fingerprint"]
    reused = run_loop(problem, req, proposer=NoModel(), log=lambda m: None)
    assert reused.provenance["baseline_reused"]
    assert reused.stopped == recovered.stopped and not reused.refused
    assert reused.decision.metrics == recovered.decision.metrics
    assert [s.stage for s in reused.scored] == ["coarse", "fine"]
    assert (tmp_path / "out/checks.txt").read_text().splitlines() == ["checked", "checked"]
    assert (tmp_path / "out/tools.txt").read_text().splitlines() == stages + ["coarse", "fine"]
    assert (tmp_path / "design.py").read_text() == "# unchanged design\nanswer = 42\n"


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
        monkeypatch.setattr("flux_evaluator_abi.toolchain_fingerprint", lambda tools: {"yosys": "new-build"})
    changed = run()
    assert not changed.provenance.get("baseline_reused")
    assert changed.decision.metrics == {"cost": 7.0}
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


@pytest.mark.parametrize("baseline_fails", [False, True])
def test_normal_generation_starts_after_baseline_even_when_baseline_fails(tmp_path, baseline_fails):
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
    if baseline_fails:
        doc["baseline"]["file"] = "absent.py"
    task = TaskSpec.from_dict(doc, base=tmp_path)
    proposer = ScriptedProposer([json.dumps({"artifact": "answer = 43\n", "why": "a useful improvement"})])
    out = run_passes(lambda r, f: run_loop(PromptProblem(task), r, proposer=proposer, log=lambda m: None),
                     request_for(task, db=str(tmp_path / "out.db")), passes=1, say=lambda m: None)
    assert proposer.prompts  # normal work follows pass 0, rather than ending with its result
    assert out.provenance["request"]["baseline"] is False
    assert any(s.candidate.artifact.strip() == "answer = 43" for s in out.scored), (out.refused, out.scored)
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
