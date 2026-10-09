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
import(process.argv[2]).then(({taskScope, currentTask, taskPass, campaignForStart}) => {
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
