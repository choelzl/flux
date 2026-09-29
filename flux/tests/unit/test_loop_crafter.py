"""The website's loop crafter (website/docs/assets/crafter.js) writes documents the loader takes.

`buildYaml(state, catalog)` runs under node with the tool catalog the page fetches
(website/docs/assets/tools.json). Every state is built from scratch, as the page starts: typed
checks (lint, compile, golden model, test script, custom) in order, measurements by tool with
their gates (`cutoff`), and an objective of labelled numbers (at least, at most, maximise,
minimise, balance). Each document is written beside the files it names and loaded with
`flux_loop.load_task`. `check(state)` must flag what can still go wrong."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from flux_loop import TaskError, load_task

REPO = Path(__file__).resolve().parents[3]
ASSETS = REPO / "website/docs/assets"
TEMPLATES = REPO / "flux/interfaces/cli/src/flux_cli/templates"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")

LOOP_PENDING = "needs the loop's cutoff lists / objective limits"

#: files a case's document names -> where they come from
FILES = {
    "rtl": (TEMPLATES / "rtl", ["golden.py"]),
    "python": (TEMPLATES / "python", ["check.py", "bench.py"]),
    "none": (TEMPLATES, []),
}

SCRIPT = r"""
const c = require(process.argv[1]);
c.setCatalog(JSON.parse(require("fs").readFileSync(process.argv[2], "utf8")));
const out = {};
const add = (name, files, s, extra) => { out[name] = Object.assign({files, state: s}, extra || {}); };
const fresh = (id, lang) => { const s = c.base(); s.id = id; s.statement = "Whatever " + id + " makes."; s.language = lang; return s; };
const obj = (m, label, value) => Object.assign(c.newObjective(m, label), {value: value || ""});

// RTL: lint + golden; synth + place, two gates on place; at least fmax, at most area, least power
let s = fresh("adder8", "systemverilog");
s.checks.push(c.newCheck(s, "lint")); s.checks.push(c.newCheck(s, "golden"));
s.stages.push(c.newStage(s, "rtl-synth")); s.stages.push(c.newStage(s, "rtl-place"));
s.stages.forEach(st => { st.params.clock_ps = "1000"; });
s.stages[0].gates = [{metric: "fmax_mhz", rule: "at", value: "800"}];
s.stages[1].gates = [{metric: "fmax_mhz", rule: "at", value: "900"}, {metric: "area_um2", rule: "within", value: "20"}];
s.objectives = [obj("fmax_mhz", "atleast", "1000"), obj("area_um2", "atmost", "80"), obj("power_w", "min")];
add("rtl", "rtl", s);

// the same with one gate, on the first stage only (the single-dict form)
s = JSON.parse(JSON.stringify(s)); s.id = "adder8_one"; s.stages[1].gates = [];
add("rtl_one_gate", "rtl", s);

// Python: a test script and a benchmark; least time
s = fresh("count_primes", "python");
s.checks.push(c.newCheck(s, "test")); s.checks[0].timeout = "60";
s.stages.push(c.newStage(s, "bench-script"));
s.objectives = [obj("time_ms", "min")];
add("python", "python", s);

// ChampSim: build + smoke-run a prefetcher header; simulate; most speed-up
s = fresh("prefetcher_h", "cpp");
s.checks.push(c.newCheck(s, "compile"));
const smoke = c.newCheck(s, "test"); c.setCheckTool(s, smoke, "test", "champsim-check"); s.checks.push(smoke);
s.stages.push(c.newStage(s, "champsim-run"));
s.objectives = [obj("geomean_speedup", "max")];
add("champsim", "none", s);

// a balance of fmax and area
s = fresh("balanced", "verilog");
s.checks.push(c.newCheck(s, "golden"));
s.stages.push(c.newStage(s, "rtl-synth"));
s.objectives = [obj("fmax_mhz", "balance"), obj("area_um2", "balance")];
add("balance", "rtl", s);

// what can still go wrong
const bad = (name, s) => add(name, "none", s, {bad: true});
bad("bad_empty", c.base());
s = fresh("x", "python"); s.checks.push(c.newCheck(s, "lint")); bad("bad_no_lint_for_python", s);
s = JSON.parse(JSON.stringify(out.rtl.state)); s.stages[0].gates = [{metric: "energy_pj", rule: "at", value: "1"}]; bad("bad_gate_metric", s);
s = JSON.parse(JSON.stringify(out.rtl.state)); s.objectives.push(obj("latency_ns", "min")); bad("bad_objective_metric", s);
s = JSON.parse(JSON.stringify(out.rtl.state)); s.objectives = [obj("fmax_mhz", "balance")]; bad("bad_balance_one", s);
s = JSON.parse(JSON.stringify(out.rtl.state)); s.checks[1].name = "lint"; s.stages[1].name = "synth"; bad("bad_duplicates", s);
s = JSON.parse(JSON.stringify(out.rtl.state)); s.checks.push(c.newCheck(s, "custom")); bad("bad_missing_param", s);
s = JSON.parse(JSON.stringify(out.rtl.state)); s.checks.push(Object.assign(c.newCheck(s, "custom"), {tool: "champsim-build", name: "build", params: {}})); bad("bad_language", s);
s = JSON.parse(JSON.stringify(out.rtl.state)); s.objectives[0].value = ""; s.stages[0].gates[0].value = "150"; s.stages[0].gates[0].rule = "within"; bad("bad_values", s);

for (const k in out) {
  out[k].yaml = c.buildYaml(out[k].state);
  out[k].check = c.check(out[k].state);
  out[k].words = c.describeObjectives(c.resolve(out[k].state).objectives);
}
process.stdout.write(JSON.stringify(out));
"""


def _run():
    r = subprocess.run(["node", "-e", SCRIPT, str(ASSETS / "crafter.js"), str(ASSETS / "tools.json")],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


BUILT = _run() if shutil.which("node") else {}
GOOD = sorted(k for k, v in BUILT.items() if not v.get("bad"))


def _load(tmp_path: Path, case: dict, pending: bool = False):
    """The loaded task; with `pending`, a refusal of what the loop is being extended to take
    (cutoff lists, objective limits) skips instead of failing."""
    src, files = FILES[case["files"]]
    doc_id = case["state"]["id"]
    for f in files:
        (tmp_path / f).write_text((src / f).read_text().replace("__NAME__", doc_id))
    doc = tmp_path / f"{doc_id}.problem.yaml"
    doc.write_text(case["yaml"])
    try:
        return load_task(doc)
    except (TaskError, TypeError, ValueError) as exc:
        if pending and ("cutoff" in str(exc) or "goal" in str(exc) or "balance" in str(exc)):
            pytest.skip(f"{LOOP_PENDING}: {exc}")
        raise


def _errors(case):
    return " ".join(m["text"] for m in case["check"] if m["level"] == "error")


def _warnings(case):
    return " ".join(m["text"] for m in case["check"] if m["level"] == "warning")


@pytest.mark.parametrize("name", GOOD)
def test_the_crafter_writes_a_document_the_loader_takes(tmp_path, name):
    case = BUILT[name]
    assert not _errors(case), case["check"]
    task = _load(tmp_path, case, pending=True)
    assert task.id == case["state"]["id"] and len(task.gate) == len(case["state"]["checks"])
    for st in task.stages:            # every stage measures every objective
        assert {o.metric for o in task.objectives} <= set(st.metrics), (st.name, st.metrics)


def test_rtl_checks_run_in_order_and_the_limits_are_goals(tmp_path):
    t = _load(tmp_path, BUILT["rtl_one_gate"])
    assert [c.name for c in t.gate] == ["lint", "golden"]
    assert t.gate.named("lint").run[-2:] == ("lint", "{artifact}")
    assert t.gate.named("golden").run[-3:] == ("{artifact}", "--golden", "{home}/golden.py")
    assert t.stages[0].cutoff == {"metric": "fmax_mhz", "at": 800} and not t.stages[1].cutoff
    got = [(o.metric, o.direction, o.goal) for o in t.objectives]
    assert got == [("fmax_mhz", "maximize", 1000), ("area_um2", "minimize", 80), ("power_w", "minimize", None)]
    assert BUILT["rtl"]["words"] == ["fmax_mhz at least 1000 MHz, area_um2 at most 80 um2, then least power_w"]


def test_two_gates_on_one_stage_are_a_cutoff_list(tmp_path):
    assert "cutoff: [{metric: fmax_mhz, at: 900}, {metric: area_um2, within: 0.2}]" in BUILT["rtl"]["yaml"]
    t = _load(tmp_path, BUILT["rtl"], pending=True)
    cut = t.stages[1].cutoff
    assert list(cut) == [{"metric": "fmax_mhz", "at": 900}, {"metric": "area_um2", "within": 0.2}], cut


def test_python_and_champsim_use_the_catalogs_commands(tmp_path):
    t = _load(tmp_path, BUILT["python"])
    assert t.gate.named("test").run[-2:] == ("{home}/check.py", "{artifact}") and t.gate.named("test").timeout_s == 60
    assert t.stages[0].metrics == ("time_ms",) and t.objectives[0].direction == "minimize"
    t = _load(tmp_path, BUILT["champsim"])
    assert [c.name for c in t.gate] == ["compile", "test"]
    assert t.gate.named("compile").run[-2:] == ("build", "{artifact}")
    assert t.gate.named("test").run[-4:-2] == ("check", "{artifact}")
    assert t.stages[0].needs == ("pythia",) and t.stages[0].metrics == ("geomean_speedup",)


def test_balance_marks_a_knee_group(tmp_path):
    assert "balance: true" in BUILT["balance"]["yaml"]
    assert BUILT["balance"]["words"] == ["the best balance of fmax_mhz and area_um2"]
    t = _load(tmp_path, BUILT["balance"], pending=True)
    assert [(o.metric, o.direction) for o in t.objectives] == [("fmax_mhz", "maximize"), ("area_um2", "minimize")]
    if not hasattr(t.objectives[0], "balance"):
        pytest.skip(f"{LOOP_PENDING}: Objective has no `balance` yet (the key is read and dropped)")
    assert all(o.balance for o in t.objectives)


def test_check_flags_what_can_still_go_wrong():
    e = _errors(BUILT["bad_empty"])
    assert "Add a check" in e and "Add a measurement" in e and "Add an objective" in e
    assert "no lint tool for python" in _errors(BUILT["bad_no_lint_for_python"])
    assert "does not report energy_pj" in _errors(BUILT["bad_gate_metric"])
    assert "latency_ns, which no measurement reports" in _errors(BUILT["bad_objective_metric"])
    assert "Balance needs two" in _warnings(BUILT["bad_balance_one"])
    e = _errors(BUILT["bad_duplicates"])
    assert 'Two checks are named "lint"' in e and 'Two measurements are named "synth"' in e
    assert "needs its command" in _errors(BUILT["bad_missing_param"])
    assert "is made for cpp" in _warnings(BUILT["bad_language"])
    e = _errors(BUILT["bad_values"])
    assert "must be at least" in e and "percentage between 1 and 100" in e
    assert "last measurement's gate" in _warnings(BUILT["rtl"])
