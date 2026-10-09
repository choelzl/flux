"""Loop summaries expose every metric with references consistent with the results UI."""

import json
import shutil
import time

import pytest

from flux_records import Records
from flux_web.results import designs, measurement_summary
from flux_web.runs import loop_files
from test_web_admin import _client, _loop, server  # noqa: F401 -- shared fixture
from test_web_charts import run


def _design(name, value, *, eligible=True, baseline=False, group="whole", stage="bench", last="2026-10-01T10:00:00Z"):
    return {"name": name, "group": group, "shown": stage, "eligible": eligible, "baseline": baseline,
            "last": last, "stages": {stage: {"score": value}}, "meets": {"score": True}}


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")
@pytest.mark.parametrize("direction", ["minimize", "maximize"])
@pytest.mark.parametrize("baseline", [None, 0, 20, -10])
def test_summary_reference_matches_browser_comparison(tmp_path, direction, baseline):
    decision = _design("winner", 8)
    rows = [decision, _design("other", 12), _design("bad", 1e9, eligible=False),
            _design("another part", 1e9, group="part"), _design("another stage", 1e9, stage="screen")]
    if baseline is not None:
        rows += [_design("initial", baseline, eligible=False, baseline=True),
                 _design("older", 1e9, baseline=True, last="2026-09-01T10:00:00Z")]
    result = {"metrics": ["score", "missing"], "designs": rows, "limits": [{"metric": "score", "direction": direction}]}
    got = measurement_summary(result, decision)
    browser = run(tmp_path, f"""
const result = {json.dumps(result)};
const compare = M.measurementComparison(result.designs, result.limits);
console.log(JSON.stringify(Object.fromEntries(result.metrics.map(metric => {{
  const value = compare(result.designs[0], metric);
  return [metric, {{value:value.value ?? null, percent:value.percent, reference:value.reference ?? null}}];
}}))));
""")
    for metric, expected in browser.items():
        assert got[metric]["value"] == expected["value"]
        assert got[metric]["reference"] == expected["reference"]
        if expected["percent"] is None:
            assert got[metric]["percent"] is None
        else:
            assert got[metric]["percent"] == pytest.approx(expected["percent"])


def test_nonfinite_and_boolean_measurements_are_unavailable():
    decision = _design("winner", float("nan"))
    result = {"metrics": ["score"], "designs": [decision, _design("boolean", True), _design("infinite", float("inf"))]}
    assert measurement_summary(result, decision)["score"] == {"value": None, "percent": None, "reference": None, "meets": True}


def test_metric_definition_controls_percentile_direction():
    decision = _design("winner", 10)
    result = {"metrics": ["score"], "designs": [decision, _design("other", 20)], "metric_info": {"score": {"direction": "minimize"}}}
    assert measurement_summary(result, decision)["score"]["reference"] == {"kind": "P10", "count": 2, "value": 11}


def test_baseline_with_equal_timestamp_matches_browser_order():
    decision = _design("winner", 8)
    result = {"metrics": ["score"], "designs": [decision, _design("first", 10, baseline=True), _design("last", 20, baseline=True)]}
    got = measurement_summary(result, decision)["score"]
    assert got["reference"]["name"] == "last" and got["percent"] == -60


def _record(directory, objectives=None):
    path = directory / "out/measurements.db"
    path.parent.mkdir(exist_ok=True)
    record = Records(str(path), objective={"study": "summaries"}, name="summaries")
    record.remember("objectives", {"objectives": objectives if objectives is not None else [
        {"metric": "latency", "direction": "minimize"}, {"metric": "score", "direction": "maximize"}]})
    for name, numbers, baseline in (("initial", {"latency": 10, "score": 100}, True),
                                    ("winner", {"latency": 8, "score": 120}, False)):
        record.trial({"name": name, "artifact": name, "meta": {"baseline": baseline, "baseline_metrics": baseline}},
                     name, stage="bench", strategy="baseline" if baseline else "loop", metrics=numbers, evaluator="bench")
    record.close("paused")
    record.store.close()
    return path


def test_summary_references_include_baselines_outside_results_page(tmp_path):
    path = _record(tmp_path)
    result = designs(str(path), [{"name": "bench"}], decision="winner", limit=1)
    assert len(result["designs"]) == 1 and result["designs"][0]["name"] == "winner"
    assert result["decision_measurements"]["latency"]["percent"] == -20
    assert result["decision_measurements"]["score"]["percent"] == 20
    assert result["decision_measurements"]["score"]["reference"]["kind"] == "baseline"


def test_loop_and_admin_lists_expose_all_decision_measurements(server):  # noqa: F811
    app, _tmp = server
    bob = _client(app, "bob", "another long secret")
    ada = _client(app, "ada", "correct horse battery")
    _loop(bob, "summaries")
    _loop(ada, "summaries")
    directory = app.state.store.data / "users/bob/apps/summaries"
    path = _record(directory)
    files = loop_files(directory)
    files["answer"].parent.mkdir(exist_ok=True)
    files["answer"].write_text(json.dumps({"decision": {"name": "winner"}}))
    ident = app.state.store.add_run(app.state.store.user(name="bob"), "summaries", str(path), str(files["log"]), ["flux"], {})
    app.state.store.set_run(ident, ended=time.time(), rc=0)
    summaries = [bob.get("/api/apps").json()[0]["summary"],
                 next(row["summary"] for row in ada.get("/api/admin/apps").json() if row["owner"] == "bob")]
    for summary in summaries:
        assert summary["metrics"] == ["latency", "score"]
        assert summary["best"]["design"] == "winner" and summary["best"]["stage"] == "bench"
        assert summary["best"]["measurements"]["latency"]["percent"] == -20
        assert summary["best"]["measurements"]["score"]["value"] == 120
        assert summary["best"]["measurements"]["score"]["percent"] == 20


@pytest.mark.parametrize("objectives, primary", [
    ([{"metric": "score", "direction": "maximize", "goal": 100},
      {"metric": "latency", "direction": "minimize"}], "latency"),
    ([{"metric": "latency", "direction": "maximize", "goal": 0},
      {"metric": "score", "direction": "maximize"}], "score"),
    ([{"metric": "latency", "direction": "minimize", "goal": 10},
      {"metric": "score", "direction": "maximize", "goal": 100}], "latency"),
    ([], "latency"),
])
def test_default_main_metric_is_the_ranking_objective_not_a_goal(server, objectives, primary):  # noqa: F811
    app, _tmp = server
    bob = _client(app, "bob", "another long secret")
    ada = _client(app, "ada", "correct horse battery")
    _loop(bob, "summaries")
    directory = app.state.store.data / "users/bob/apps/summaries"
    path = _record(directory, objectives)
    files = loop_files(directory)
    files["answer"].parent.mkdir(exist_ok=True)
    files["answer"].write_text(json.dumps({"decision": {"name": "winner"}}))
    ident = app.state.store.add_run(app.state.store.user(name="bob"), "summaries", str(path), str(files["log"]), ["flux"], {})
    app.state.store.set_run(ident, ended=time.time(), rc=0)
    result = designs(str(path), [{"name": "bench"}], decision="winner")
    assert result["main_metrics"] == [primary]
    assert result["metrics"][0] == (objectives[0]["metric"] if objectives else "latency")
    for summary in (bob.get("/api/apps").json()[0]["summary"], ada.get("/api/admin/apps").json()[0]["summary"]):
        assert summary["main_metrics"] == [primary]
        assert summary["best"]["metric"] == primary
        assert summary["best"]["value"] == (8 if primary == "latency" else 120)
        assert set(summary["best"]["measurements"]) == {"latency", "score"}
