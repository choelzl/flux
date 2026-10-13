"""Sparse dictionary measurements reach objectives, records, caches and the web without zero fill. D1028: a stage
names its metrics; a dictionary's type, aggregate, direction and unit are its objective's."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from flux_loop import Candidate, LoopRequest, LoopState, PromptProblem, TaskError, TaskSpec, request_for, run_loop
from flux_loop.document.stages import _stage
from flux_loop.measure import cached_measure, measure_many
from flux_loop.metrics import numeric_metrics
from flux_loop.task_measure import _metrics_in
from flux_records import Records
from flux_web.results import designs


METRICS = ["area", "timings"]


def document():
    return {"id": "sparse", "statement": "Improve a named timing test", "language": "text",
            "flow": {"test": "true", "measure": {"bench": {"command": "true", "metrics": list(METRICS)}}},
            "objectives": [{"metric": "timings.fast", "direction": "minimize", "unit": "ms"}]}


def ranked_by(aggregate):
    """The document ranking the timings' parent by `aggregate`, as its objective says."""
    doc = document()
    doc["objectives"] = [{"metric": "timings", "type": "dict", "aggregate": aggregate, "direction": "minimize", "unit": "ms"}]
    return doc


@pytest.mark.parametrize("output", [
    'area=4 timings={"fast": 0, "slow": 12.5, "missing": null, "empty": "", "nan": NaN, "inf": Infinity, "bool": false}',
    '{"area": 4, "timings": {"fast": 0, "slow": 12.5, "missing": null, "empty": "", "nan": NaN, "inf": Infinity, "bool": false}}',
    json.dumps({"area": 4, "timings": {"fast": 0, "slow": 12.5, "missing": None, "empty": "", "nan": float("nan"), "inf": float("inf"), "bool": False}}, indent=2),
    'DEBUG before\n' + json.dumps({"area": 4, "timings": {"fast": 0, "slow": 12.5, "missing": None, "empty": "", "nan": float("nan"), "inf": float("inf"), "bool": False}}, indent=2) + '\nDEBUG after',
])
def test_json_dictionaries_skip_unavailable_values_and_keep_test_names(output):
    spec = _stage(0, {"name": "bench", "command": "true", "metrics": METRICS})
    got = _metrics_in(spec, output)
    assert numeric_metrics(got) == {"area": 4, "timings.fast": 0, "timings.slow": 12.5}
    assert set(got["_metric_groups"]["timings"]) == {"fast", "slow", "missing", "empty", "nan", "inf", "bool"}


def test_custom_dictionary_regex_and_legacy_scalar_specs():
    spec = _stage(0, {"name": "bench", "command": "true", "metrics": METRICS,
                      "metrics_re": {"timings": r"BENCH (\{.*\})", "area": r"area:(\d+)"}})
    assert _metrics_in(spec, 'BENCH {"a.b / test": 8} area:2')["timings.a.b / test"] == 8
    assert _metrics_in(SimpleNamespace(metrics_re={"score": r"score=(\d+)"}), "score=2") == {"score": 2}


def test_dictionary_schema_roundtrips_inherits_metadata_and_allows_child_baselines():
    doc = document()
    doc["baseline"] = {"metrics": [{"metric": "timings.fast", "value": 10}]}
    task = TaskSpec.from_dict(doc)
    assert task.objectives[0].direction == "minimize" and task.objectives[0].unit == "ms"
    assert PromptProblem(task).validate(LoopRequest()) == []
    assert task.to_dict()["flow"]["measure"]["bench"]["metrics"] == ["area", "timings"], "names only"
    again = TaskSpec.from_dict(task.to_dict())
    assert again.stages[0].metric_specs == task.stages[0].metric_specs
    assert again.objectives == task.objectives
    assert again.digest == task.digest
    doc["objectives"] = [{"metric": "not_reported.test"}]
    assert "no stage" in PromptProblem(TaskSpec.from_dict(doc)).validate(LoopRequest())[0]


@pytest.mark.parametrize("objective, said", [
    ({"metric": "t", "type": "array"}, "type is number or dict"), ({"metric": "t", "type": "dict", "direction": "sideways"}, "direction"),
    ({"metric": "t", "type": "dict", "aggregate": "p90"}, "aggregate"), ({"metric": "t", "type": "number", "aggregate": "mean"}, "aggregate"),
    ({"metric": "t", "type": "dict", "aggregate": "none"}, "ranks nothing"),
])
def test_invalid_dictionary_objectives_are_refused(objective, said):
    doc = document()
    doc["objectives"] = [objective]
    with pytest.raises(TaskError, match=said):
        TaskSpec.from_dict(doc)


@pytest.mark.parametrize("metric", [{"name": "t", "type": "dict"}, {"name": "t"}, "", "_metric_groups"])
def test_a_stage_names_its_metrics_only(metric):
    with pytest.raises(TaskError, match="objective's|non-empty"):
        _stage(0, {"name": "bench", "command": "true", "metrics": [metric]})


def test_a_dictionary_and_one_of_its_tests_printed_apart_are_ambiguous():
    with pytest.raises(ValueError, match="ambiguous"):
        numeric_metrics({"timings.fast": 5, "timings": {"fast": 4}})


def test_a_dictionary_no_objective_ranks_has_its_tests_and_no_parent_number():
    stage = _stage(0, {"name": "bench", "command": "true", "metrics": ["timings"]})
    got = _metrics_in(stage, 'timings={"a": 0, "b": 10}')
    assert numeric_metrics(got) == {"timings.a": 0, "timings.b": 10} and got["_metric_groups"] == {"timings": ["a", "b"]}


@pytest.mark.parametrize("aggregate, expected", [("mean", 5), ("median", 3), ("min", 0), ("max", 12), ("sum", 15)])
def test_parent_aggregates_ignore_missing_values_and_can_be_objectives(aggregate, expected):
    doc = ranked_by(aggregate)
    doc["baseline"] = {"metrics": [{"metric": "timings", "value": 10}]}
    task = TaskSpec.from_dict(doc)
    assert PromptProblem(task).validate(LoopRequest()) == []
    assert task.objectives[0].direction == "minimize" and task.objectives[0].unit == "ms"
    got = _metrics_in(task.stages[0], 'timings={"a": 0, "b": 3, "c": 12, "missing": null, "nan": NaN}')
    assert got["timings"] == expected
    assert "timings" not in _metrics_in(task.stages[0], 'timings={"missing": null, "nan": NaN}')


def test_mean_is_the_default_parent_aggregate():
    doc = document()
    doc["objectives"] = [{"metric": "timings", "type": "dict"}]
    task = TaskSpec.from_dict(doc)
    assert task.stages[0].metric_specs["timings"]["aggregate"] == "mean" and task.objectives[0].aggregate == "mean"
    assert _metrics_in(task.stages[0], 'timings={"a": 0, "b": 10}')["timings"] == 5
    assert task.to_dict()["objectives"] == [{"metric": "timings", "direction": "maximize", "type": "dict", "aggregate": "mean"}]


def test_nested_measurements_record_sparse_groups_on_both_recording_paths(tmp_path):
    task = TaskSpec.from_dict(ranked_by("min"))
    problem = PromptProblem(task)
    problem.measure = lambda *_: {"area": 4, "timings": {"fast": 7, "never": None, "invalid": float("nan")}}
    record = Records(str(tmp_path / "r.db"), objective={"study": "sparse"}, name="sparse")
    record.remember("objectives", {"objectives": [{"metric": "timings.fast", "direction": "minimize"}]})
    state = LoopState(LoopRequest(), say=lambda _: None, proposer=None, feedback=None, records=record)
    first, second = Candidate("sparse#1", "first"), Candidate("sparse#2", "second")
    scored = measure_many(problem, state, [first], "bench")
    assert scored[0].metrics == {"area": 4, "timings.fast": 7, "timings": 7}
    cached_measure(problem, state, second, "bench", record=True)
    record.close("paused")
    spec = {"name": "timings", "type": "dict", "aggregate": "min", "direction": "minimize", "unit": "ms"}   # as the web gives it (D1028)
    got = designs(str(tmp_path / "r.db"), [{"name": "bench", "metrics": ["area", spec]}])
    assert len(got["designs"]) == 2
    assert got["metric_groups"]["timings"]["metrics"] == ["timings", "timings.fast", "timings.invalid", "timings.never"]
    assert got["metric_info"]["timings.fast"] == {"direction": "minimize", "unit": "ms", "aggregate": "min"}
    assert all(d["numbers"] == {"area": 4, "timings.fast": 7, "timings": 7} for d in got["designs"])
    historical = designs(str(tmp_path / "r.db"), [{"name": "bench"}], campaign=record.campaign_id)
    assert historical["metric_info"]["timings.fast"] == {"direction": "minimize", "unit": "ms", "aggregate": "min"}, "from the record"


@pytest.mark.parametrize("objective", ["timings.fast", "timings"])
def test_a_real_sparse_timing_loop_decides_by_a_named_test_and_retains_its_results(tmp_path, objective):
    (tmp_path / "gen.py").write_text("import sys; open(sys.argv[1], 'w').write(sys.argv[2])")
    (tmp_path / "bench.py").write_text("import json,sys; x=int(open(sys.argv[1]).read()); print(json.dumps({'area': 4, 'timings': {'fast': x, 'slow': 8 if x==2 else None, 'never': None}}))")
    doc = document()
    doc["flow"].update({"orchestrate": {"policy": "sweep", "space": {"x": [1, 2]}},
                       "generate": {"command": "{python} {home}/gen.py {artifact} {x}"}, "test": {"test": "true"}})
    doc["flow"]["measure"]["bench"]["command"] = "{python} {home}/bench.py {artifact}"
    doc["budget"] = {"steps": 2, "batch": 10}
    if objective == "timings":
        doc["objectives"] = [{"metric": objective, "type": "dict", "aggregate": "mean", "direction": "minimize"}]
    task = TaskSpec.from_dict(doc, base=tmp_path)
    db = str(tmp_path / "r.db")
    outcome = run_loop(PromptProblem(task), request_for(task, db=db), proposer=None, log=lambda _: None)
    assert outcome.decision.metrics["timings.fast"] == 1
    assert outcome.decision.metrics[objective] == 1
    got = designs(db, [{"name": "bench", "metrics": [{"name": m, **task.stages[0].metric_specs[m]} if m in task.stages[0].metric_specs
                                                     else m for m in task.stages[0].metrics]}])
    if objective == "timings":
        assert "timings" in got["metric_groups"]["timings"]["metrics"]
        assert got["metric_groups"]["timings"]["aggregate"] == "mean"
    assert len(got["designs"]) == 2 and "timings.never" in got["metrics"]
    assert any("timings.slow" not in d["numbers"] for d in got["designs"])


def test_many_dynamic_tests_are_not_truncated_by_results_and_aggregation_changes_cache_identity(tmp_path):
    doc = ranked_by("mean")
    task = TaskSpec.from_dict(doc, base=tmp_path)
    problem = PromptProblem(task)
    tests = {f"test_{i}": i for i in range(12)}
    problem.measure = lambda *_: {"timings": tests}
    record = Records(str(tmp_path / "r.db"), objective={"study": "sparse"}, name="sparse")
    state = LoopState(LoopRequest(), say=lambda _: None, proposer=None, feedback=None, records=record)
    cand = Candidate("sparse#1", "first")
    mean_key = problem.cache_key(cand, "bench", state)
    scored = measure_many(problem, state, [cand], "bench")
    assert scored[0].metrics["timings"] == 5.5
    record.close("paused")
    got = designs(str(tmp_path / "r.db"), [{"name": "bench", "metrics": task.stages[0].metric_doc()}])
    assert len(got["metrics"]) >= 13
    assert len(got["metric_groups"]["timings"]["metrics"]) == 13
    task.stages[0].metric_specs["timings"]["aggregate"] = "max"
    assert problem.cache_key(cand, "bench", state) != mean_key


def test_an_older_document_says_its_dictionary_on_the_objective(tmp_path):
    """D1028's migration: a stage's metric settings move onto the objective that ranks it; one no objective
    ranks is its name alone."""
    from flux_loop.migrate import migrate

    old = {"statement": "s", "flow": {"test": "true", "measure": {"bench": {"command": "true", "metrics": [
        "area", {"name": "timings", "type": "dict", "direction": "minimize", "unit": "ms", "aggregate": "sum"},
        {"name": "other", "type": "dict", "aggregate": "none"}]}}}, "objectives": ["timings"]}
    new, said, manual = migrate(old)
    assert new["flow"]["measure"]["bench"]["metrics"] == ["area", "timings", "other"]
    assert new["objectives"] == [{"metric": "timings", "type": "dict", "direction": "minimize", "unit": "ms", "aggregate": "sum"}]
    assert any("other by name" in x for x in said) and not manual
    task = TaskSpec.from_dict({"id": "x", **new})
    assert task.stages[0].metric_specs["timings"]["aggregate"] == "sum"
