"""The website's loop crafter (website/docs/assets/crafter.js) writes documents the loader takes.

`buildYaml(state)` runs under node for every preset and for hand-made states (every box a
coding agent where one may answer, a search over a space; keep/above objectives; the model
with tools picking the work); each document is written beside the files it names (copied from
the template or application the preset follows) and loaded with `flux_loop.load_task`.
`check(state)` must flag what the loader or the run would refuse: an agent on the gate, a goal
no stage measures."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from flux_loop import load_task

REPO = Path(__file__).resolve().parents[3]
CRAFTER = REPO / "website/docs/assets/crafter.js"
TEMPLATES = REPO / "flux/interfaces/cli/src/flux_cli/templates"
APPS = REPO / "flux/applications"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")

#: preset -> (folder its files come from, the files)
FILES = {
    "rtl": (TEMPLATES / "rtl", ["golden.py"]),
    "rtl-sweep": (TEMPLATES / "rtl-sweep", ["golden.py", "gen.py"]),
    "tune": (TEMPLATES / "tune", ["check.py", "bench.py", "workload.py"]),
    "python": (TEMPLATES / "python", ["check.py", "bench.py"]),
    "config": (APPS / "prefetcher", ["bingo.py", "knobs.md", "bingo_default.ini"]),
}

SCRIPT = r"""
const c = require(process.argv[1]);
const out = {};
for (const p of c.PRESETS) out[p.key] = {preset: p.key, state: c.preset(p.key)};

// every box a coding agent where one may answer; a search policy over a space
let s = c.preset("rtl-sweep");
for (const b of c.DELEGABLE) s.flow[b] = "agent:" + c.AGENTS[c.DELEGABLE.indexOf(b) % 3];
s.flow.dse = "gradient";
out.all_agents = {preset: "rtl-sweep", state: s};

// keep/above: the smallest that holds 90% of the best speed-up
s = c.preset("python");
s.stages[0].metrics = "time_ms, speedup";
s.objectives = [{metric: "speedup", direction: "maximize", target: "keep", keep: "0.9", above: "1.0", goal: "", unit: ""},
                {metric: "time_ms", direction: "minimize", target: "goal", goal: "40", keep: "", above: "", unit: ""}];
out.keep_above = {preset: "python", state: s};

// the model with tools picks the work; a model critic, lessons mined, a surrogate, calibration off
s = c.preset("python");
Object.assign(s.flow, {orchestrate: "agent", critique: "llm", extract: "mined", analytical: "surrogate",
                       calibrate: "off", feedback: "none", knowledge: "digest", validate: "llm", plan: "llm"});
s.budget.prototype = "false"; s.budget.passes = "2";
out.model_boxes = {preset: "python", state: s};

// bad: an agent on the gate, a goal nothing measures
s = c.preset("rtl");
s.flow.test = "agent:claude";
out.bad_agent = {state: s, bad: true};
s = c.preset("rtl");
s.objectives.push({metric: "latency_ns", direction: "minimize", target: "none", goal: "", keep: "", above: "", unit: ""});
out.bad_metric = {state: s, bad: true};
s = c.preset("tune");
s.flow.dse = "none"; s.space = [];
out.bad_space = {state: s, bad: true};

for (const k in out) {
  out[k].yaml = c.buildYaml(out[k].state);
  out[k].check = c.check(out[k].state);
}
process.stdout.write(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def built():
    r = subprocess.run(["node", "-e", SCRIPT, str(CRAFTER)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def _load(tmp_path: Path, case: dict):
    src, files = FILES[case["preset"]]
    doc_id = case["state"]["id"]
    for f in files:
        text = (src / f).read_text().replace("__NAME__", doc_id)
        (tmp_path / f).write_text(text)
    doc = tmp_path / f"{doc_id}.problem.yaml"
    doc.write_text(case["yaml"])
    return load_task(doc)


@pytest.mark.parametrize("name", ["rtl", "rtl-sweep", "tune", "python", "config", "all_agents", "keep_above", "model_boxes"])
def test_the_crafter_writes_a_document_the_loader_takes(built, tmp_path, name):
    case = built[name]
    assert not [m for m in case["check"] if m["level"] == "error"], case["check"]
    task = _load(tmp_path, case)
    assert task.id == case["state"]["id"] and task.statement and task.gate.test
    measured = {m for st in task.stages for m in st.metrics}
    assert {o.metric for o in task.objectives} <= measured


def test_the_hand_made_states_say_what_they_chose(built, tmp_path):
    t = _load(tmp_path, built["all_agents"])
    assert t.flow["dse"] == "gradient" and "orchestrate" not in t.flow        # the policy leads
    assert t.generator == {"agent": t.flow["generate"]["agent"]}
    for box in ("validate", "plan", "critique", "extract", "select"):
        assert t.flow[box]["agent"] in ("opencode", "claude", "codex"), box
    assert "test" not in t.flow
    k = _load(tmp_path, built["keep_above"]).objectives[0]
    assert (k.keep, k.above, k.goal) == (0.9, 1.0, None)
    m = _load(tmp_path, built["model_boxes"])
    assert m.flow["extract"] == "mined" and m.flow["analytical"] == ["surrogate"] and m.budget["calibrate"] is False
    assert m.roles["orchestrator"] == "agent" and m.critique and "plan" in m.budget["agent"]


def test_a_preset_says_only_what_is_its_own(built):
    y = built["rtl"]["yaml"]
    assert "flow:" not in y and "validate" not in y and "records" not in y
    assert "gate: flux rtl test {artifact} --golden {home}/golden.py" in y


def test_check_flags_the_bad_states(built):
    def errors(name):
        return " ".join(m["text"] for m in built[name]["check"] if m["level"] == "error")
    assert "never handed to a coding agent" in errors("bad_agent")
    assert "test:" not in built["bad_agent"]["yaml"]                  # never written, even so
    assert "latency_ns" in errors("bad_metric")
    assert "{block}" in errors("bad_space")                          # a placeholder with no knob behind it
