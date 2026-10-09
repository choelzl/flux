"""LiveAlt scopes sparse/parallel passes and resumed starts without mixing their tasks."""

import base64
import json
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).parents[2] / "interfaces/web/src/flux_web/static"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")


def evaluate(events, expression):
    uri = "data:text/javascript;base64," + base64.b64encode((STATIC / "live_alt_model.js").read_bytes()).decode()
    script = """const LT = require(process.argv[1]);
const events = JSON.parse(require('fs').readFileSync(0, 'utf8')), model=LT.model();
for (const event of events) LT.apply(model, event);
import(process.argv[2]).then(({taskScope, currentTask, taskPass, campaignForStart, filterTasks, taskState, taskWindow}) => {
  process.stdout.write(JSON.stringify(EXPRESSION));
});""".replace("EXPRESSION", expression)
    result = subprocess.run(["node", "-e", script, str(STATIC / "looptree.js"), uri],
                            input=json.dumps(events), capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def start(id, t, parent=None, name="generation: draft", **params):
    return {"ev": "start", "id": id, "t": t, "parent": parent, "name": name, "params": params}


def mark(n, t):
    return {"ev": "mark", "name": "pass", "t": t, "why": json.dumps({"n": n})}


def end(id, t):
    return {"ev": "end", "id": id, "t": t, "seconds": 1, "output": {}}


def test_baseline_sparse_passes_and_children_keep_the_root_pass():
    events = [start(1, 1, name="gate: tools"), end(1, 2), mark(0, 3), start(2, 4, name="test: baseline"),
              end(2, 5), mark(7, 6), start(3, 7), mark(8, 8), start(4, 9, parent=3, name="agent: claude"),
              start(5, 10, name="generation: next", **{"pass": 8})]
    got = evaluate(events, "(() => { const zero=taskScope(model,'0'), seven=taskScope(model,'7'); return {passes:zero.passes, baseline:zero.rows.map(r=>r.node.id), children:seven.rows.map(r=>[r.node.id,r.depth]), current:taskScope(model).current}; })()")
    assert got == {"passes": [0, 7, 8], "baseline": [2], "children": [[3, 0], [4, 1]], "current": [7, 8]}


def test_parallel_tasks_stay_in_their_explicit_pass_and_current_prefers_agent():
    events = [mark(1, 1), start(1, 2, **{"pass": 1}), mark(2, 3), start(2, 4, **{"pass": 2}),
              start(3, 5, parent=1, name="agent: codex"), start(4, 6, parent=2, name="tool:python3")]
    got = evaluate(events, "({rows:taskScope(model).rows.map(r=>r.node.id), selected:currentTask(taskScope(model).rows).id, pass2:taskScope(model,'2').rows.map(r=>r.node.id)})")
    assert got == {"rows": [1, 3, 2, 4], "selected": 3, "pass2": [2, 4]}


def test_completed_passes_follow_last_mark_and_setup_is_available_in_all():
    events = [start(1, 1, name="gate: tools"), end(1, 2), mark(0, 3), start(2, 4), end(2, 5),
              mark(4, 6), start(3, 7), end(3, 8)]
    got = evaluate(events, "({current:taskScope(model).rows.map(r=>r.node.id), all:taskScope(model,'all').rows.map(r=>r.node.id), missing:taskScope(model,'99').rows.length, selected:currentTask(taskScope(model).rows).id})")
    assert got == {"current": [3], "all": [1, 2, 3], "missing": 0, "selected": 3}
    assert evaluate([], "({rows:taskScope(model).rows, selected:currentTask([])})") == {"rows": [], "selected": None}


def test_resumed_start_does_not_select_a_future_or_other_records_campaign():
    history = {"starts": [{"id": 1, "record_id": 1, "started": 100, "ended": 150},
                          {"id": 2, "record_id": 1, "started": 200, "ended": 250}],
               "campaigns": [{"run_id": 1, "campaign_id": "old", "created_at": "1970-01-01T00:01:40Z"},
                             {"run_id": 1, "campaign_id": "future", "created_at": "1970-01-01T00:05:00Z"},
                             {"run_id": 3, "campaign_id": "other", "created_at": "1970-01-01T00:01:40Z"}]}
    literal = json.dumps(history)
    assert evaluate([], f"campaignForStart({literal}, {literal}.starts[1]).campaign_id") == "old"
    history["campaigns"] = history["campaigns"][1:]
    literal = json.dumps(history)
    assert evaluate([], f"campaignForStart({literal}, {literal}.starts[0])") is None
    history["campaigns"].append({"run_id": 1, "campaign_id": "slow-setup", "created_at": "1970-01-01T00:02:10Z"})
    literal = json.dumps(history)
    assert evaluate([], f"campaignForStart({literal}, {literal}.starts[0]).campaign_id") == "slow-setup"


def test_task_filters_keep_context_and_match_commands_without_searching_large_prompts():
    events = [start(1, 1), start(2, 2, parent=1, name="agent: claude", prompt="secret unrelated prompt"),
              start(3, 3, parent=2, name="tool:python3", command="python3 check.py"), end(3, 4),
              start(4, 5, name="test: other")]
    got = evaluate(events, "filterTasks(taskScope(model,'all').rows, 'PYTHON3 check.py', 'done').map(r=>[r.node.id,r.depth,r.context])")
    assert got == [[1, 0, True], [2, 1, True], [3, 2, False]]
    assert evaluate(events, "filterTasks(taskScope(model,'all').rows,'unrelated').length") == 0
    assert evaluate(events, "filterTasks(taskScope(model,'all').rows,'python3','failed').length") == 0


def test_interrupted_is_a_separate_filter_from_failed_and_stderr_does_not_fail_a_tool():
    events = [start(1, 1, name="tool: good"), {**end(1, 2), "output": {"exit": 0, "stderr": "DEBUG diagnostic"}},
              start(2, 3, name="tool: bad"), {**end(2, 4), "output": {"exit": 1}},
              start(3, 5, name="tool: interrupted")]
    got = evaluate(events, "(() => { LT.settle(model,6); const rows=taskScope(model,'all').rows; return {states:rows.map(r=>taskState(r.node)),failed:filterTasks(rows,'','failed').map(r=>r.node.id),interrupted:filterTasks(rows,'','interrupted').map(r=>r.node.id)}; })()")
    assert got == {"states": ["done", "failed", "interrupted"], "failed": [2], "interrupted": [3]}


def test_recent_task_window_retains_ancestors_and_an_older_pinned_task():
    events = [start(1, 1), start(2, 2, parent=1), start(3, 3, parent=2)]
    events += [start(i, i, parent=1, name="tool: test") for i in range(4, 250)]
    got = evaluate(events, "(() => { const rows=taskScope(model,'all').rows; return {recent:taskWindow(rows,2).map(r=>r.node.id),pinned:taskWindow(rows,2,3).map(r=>r.node.id),all:taskWindow(rows,300,3).length}; })()")
    assert got == {"recent": [1, 248, 249], "pinned": [1, 2, 3, 248, 249], "all": 249}


def test_parallel_branches_use_time_rather_than_tree_order_for_recent_and_current_tasks():
    events = [start(1, 1), start(2, 2, name="tool: old"), end(2, 3),
              start(3, 10, parent=1, name="agent: older"), start(4, 12, parent=1, name="agent: latest")]
    got = evaluate(events, "(() => { const rows=taskScope(model,'all').rows; return {recent:taskWindow(rows,2).map(r=>r.node.id),current:currentTask(rows).id}; })()")
    assert got == {"recent": [1, 3, 4], "current": 4}
    events += [end(4, 15), end(3, 20), end(1, 21)]
    assert evaluate(events, "currentTask(taskScope(model,'all').rows).id") == 3
