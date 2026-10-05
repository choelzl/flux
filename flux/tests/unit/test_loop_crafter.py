"""The website's loop crafter (website/docs/assets/crafter.js) writes documents the loader takes.

`buildYaml(state, catalog)` runs under node with the tool catalog the page fetches
(website/docs/assets/tools.json). Every state is built from scratch, as the page starts: typed
checks (lint, compile, golden model, test script, custom) in order, measurements by tool with
their gates (`cutoff`), and an objective of labelled numbers (at least, at most, maximise,
minimise, balance). Each document is written beside the files it names and loaded with
`flux_loop.load_task`. `check(state)` must flag what can still go wrong."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from flux_loop import TaskError, load_task

REPO = Path(__file__).resolve().parents[3]
ASSETS = REPO / "website/docs/assets"
TEMPLATES = REPO / "flux/interfaces/cli/src/flux_cli/examples"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")

LOOP_PENDING = "needs the loop's cutoff lists / objective limits"

#: files a case's document names -> where they come from
FILES = {
    "rtl": (TEMPLATES / "rtl", ["golden.py"]),
    "python": (TEMPLATES / "python", ["check.py", "bench.py"]),
    "none": (TEMPLATES, []),
    "zigzag": (REPO / "flux/applications/npu_gemm", ["check.py", "workload.yaml"]),
}

SCRIPT = r"""
const c = require(process.argv[1]);
const catalog = JSON.parse(require("fs").readFileSync(process.argv[2], "utf8"));
// a stage the catalog may list without `run`: its stage shape is written instead of a command
catalog.push({id: "test-evaluator", role: "stage", title: "An evaluator stage", what: "", stage: {evaluator: "zigzag"},
              params: {}, metrics: {latency_cycles: "cycles", energy_pj: "pJ"}, needs: [], languages: []});
c.setCatalog(catalog);
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

// five checks and five measurements with long names (the diagram abbreviates them)
s = fresh("many", "systemverilog");
["lint", "golden", "test", "custom", "custom"].forEach(t => s.checks.push(c.newCheck(s, t)));
s.checks[3].name = "a_rather_long_custom_check"; s.checks[3].params.command = "{python} {home}/golden.py";
s.checks[4].name = "another_long_custom_check"; s.checks[4].params.command = "{python} {home}/golden.py";
["rtl-synth", "rtl-place", "rtl-route", "custom-stage", "custom-stage"].forEach(t => s.stages.push(c.newStage(s, t)));
s.stages[3].name = "post_route_power_estimate"; s.stages[3].params.command = "{python} {home}/golden.py {artifact}";
s.stages[3].metrics = "fmax_mhz, area_um2";
s.stages[4].name = "gate_level_simulation_run"; s.stages[4].params.command = "{python} {home}/golden.py {artifact}";
s.stages[4].metrics = "fmax_mhz, area_um2";
s.objectives = [obj("fmax_mhz", "atleast", "1250"), obj("area_um2", "min")];
add("many", "rtl", s);
out.abbrev = {checks: c.abbreviate(s.checks.map(x => x.name), 24), stages: c.abbreviate(s.stages.map(x => x.name), 24)};

// the clock: untyped, it follows an "at least" on fmax; typed, it is kept
s = JSON.parse(JSON.stringify(out.rtl.state)); s.id = "typed_clock"; s.stages[0].params.clock_ps = "400";
add("typed_clock", "rtl", s);

// maximise fmax with a fixed clock: a warning; searching the clock: none
s = fresh("fastest", "systemverilog"); s.checks.push(c.newCheck(s, "golden")); s.stages.push(c.newStage(s, "rtl-synth"));
s.objectives = [obj("fmax_mhz", "max")];
add("fastest", "rtl", s);
s = JSON.parse(JSON.stringify(s)); s.id = "fastest_searched"; s.stages[0].params.clock_ps = "{clock_ps}";
s.space = [{knob: "clock_ps", choices: "250, 333, 500"}]; s.flow.dse = "sweep";
add("fastest_searched", "rtl", s);

// an evaluator stage from the catalog
s = fresh("evaluated", "yaml"); s.checks.push(c.newCheck(s, "custom")); s.checks[0].params.command = "{python} {home}/check.py {artifact}";
s.stages.push(c.newStage(s, "test-evaluator")); s.objectives = [obj("latency_cycles", "min")];
add("evaluated", "python", s);

// the real catalog's newer tools: an evaluator stage, a program timer, a Yosys-only area step
s = fresh("zigzag_eval", "yaml"); s.checks.push(c.newCheck(s, "test"));
s.stages.push(c.newStage(s, "zigzag-eval")); s.objectives = [obj("latency_cycles", "min"), obj("energy_pj", "min")];
add("zigzag_eval", "zigzag", s);
s = fresh("prog_timed", "cpp"); s.checks.push(c.newCheck(s, "test"));
s.stages.push(c.newStage(s, "prog-time")); s.objectives = [obj("time_ms", "min")];
add("prog_timed", "python", s);
s = fresh("stat_then_synth", "systemverilog"); s.checks.push(c.newCheck(s, "golden"));
s.stages.push(c.newStage(s, "rtl-stat")); s.stages.push(c.newStage(s, "rtl-synth"));
s.objectives = [obj("area_um2", "min")];
add("stat_then_synth", "rtl", s);

// each measurement may estimate first: off (unsaid), a surrogate, a command, the model
for (const kind of ["surrogate", "command", "model"]) {
  s = JSON.parse(JSON.stringify(out.rtl_one_gate.state)); s.id = "estimate_" + kind;
  s.stages[1].estimate = {kind, margin: "5", command: kind === "command" ? "{python} {home}/golden.py {artifact}" : ""};
  add("estimate_" + kind, "rtl", s, {pendingEstimate: true});
}

// every box that can be a coding agent, and the library off
s = JSON.parse(JSON.stringify(out.rtl_one_gate.state)); s.id = "agents_everywhere";
for (const b of c.DELEGABLE) if (b !== "dse") s.flow[b] = "agent:claude";
s.flow.knowledge = "none"; s.flow.critique = "model";
add("agents_everywhere", "rtl", s);
s = JSON.parse(JSON.stringify(out.rtl_one_gate.state)); s.id = "papers_by_agent"; s.flow.digest = "agent:opencode";   // D773
add("papers_by_agent", "rtl", s);
out.readback = {papers: c.fromDoc({id: "r", statement: "s", flow: {knowledge: {files: ["a.md"], agent: "opencode"}}}, null)};
out.boxes = Object.fromEntries(Object.keys(c.BOXES).map(b => [b, {title: c.BOXES[b].title, says: c.BOXES[b].says,
                                                                 flow: c.FLOW_BOXES.includes(b), values: c.BOXES[b].choices.map(x => x.value)}]));
out.fixed = Object.fromEntries(["test", "measure", "records", "select", "critique", "calibrate"].map(b => [b, c.isFixed(b)]));
out.defaults = {orchestrate: c.BOXES.orchestrate.choices[0].label, knowledge: c.BOXES.knowledge.choices[0].label,
                lessons: c.BOXES.lessons.choices[0].value, flowBoxes: c.FLOW_BOXES};

// the defaults, as `flux task check` says them: an otherwise empty problem with one check
const BOXES_EXPLAINED = ["validate", "orchestrate", "plan", "generate", "critique", "calibrate", "feedback", "knowledge", "lessons", "records"];
const explained = st => Object.fromEntries(BOXES_EXPLAINED.map(b => [b, c.explain(b, st)]));
s = c.base(); s.id = "defaults"; s.statement = "Anything."; s.checks.push(c.newCheck(s, "custom"));
s.checks[0].params.command = "{python} {home}/golden.py";
out.explained = {};
add("empty_problem", "rtl", s, {partial: true}); out.explained.empty_problem = explained(s);
s = JSON.parse(JSON.stringify(s)); s.id = "empty_parts"; s.partsMode = "decompose";
add("empty_parts", "rtl", s, {partial: true}); out.explained.empty_parts = explained(s);
s = JSON.parse(JSON.stringify(out.empty_problem.state)); s.id = "empty_off"; s.flow.feedback = "off"; s.flow.knowledge = "none";
add("empty_off", "rtl", s, {partial: true}); out.explained.empty_off = explained(s);
s = JSON.parse(JSON.stringify(out.rtl_one_gate.state)); s.id = "estimates_said";
s.stages[0].estimate = {kind: "surrogate", margin: "5", command: ""};
s.stages.push(c.newStage(s, "rtl-route")); s.stages[2].estimate = {kind: "model", margin: "12", command: ""};
s.stages.push(c.newStage(s, "custom-stage")); s.stages[3].params.command = "{python} {home}/golden.py {artifact}";
s.stages[3].metrics = "fmax_mhz, area_um2, power_w"; s.stages[3].estimate = {kind: "command", margin: "5", command: "{python} {home}/golden.py {artifact}"};
add("estimates_said", "rtl", s); out.explained.estimates = s.stages.map(st => c.explainEstimate(st.estimate));

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

s = JSON.parse(JSON.stringify(out.stat_then_synth.state)); s.objectives.unshift(obj("fmax_mhz", "atleast", "1000"));
bad("bad_partly_reported", s);
s = JSON.parse(JSON.stringify(out.zigzag_eval.state)); s.stages.push(c.newStage(s, "timeloop-eval"));
s.stages[1].params.workload = "other.yaml"; bad("bad_two_workloads", s);

s = JSON.parse(JSON.stringify(out.python.state)); s.flow.dse = "pareto"; s.space = [{knob: "n", choices: "1, 2"}];
bad("bad_pareto_one_objective", s);
s = JSON.parse(JSON.stringify(out.rtl_one_gate.state)); s.stages[0].estimate = {kind: "command", margin: "150", command: ""};
bad("bad_estimate", s);

for (const k in out) {
  if (!out[k].state) continue;
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
GOOD = sorted(k for k, v in BUILT.items() if "state" in v and not v.get("bad") and not v.get("partial"))


def _load(tmp_path: Path, case: dict, pending: bool = False):
    """The loaded task; with `pending`, a refusal of what the loop is being extended to take
    (cutoff lists, objective limits) skips instead of failing."""
    src, files = FILES[case["files"]]
    doc_id = case["state"]["id"]
    home = tmp_path / doc_id                                # D786: the folder is the id
    home.mkdir(exist_ok=True)
    for f in files:
        (home / f).write_text((src / f).read_text().replace("__NAME__", doc_id))
    doc = home / "problem.yaml"
    doc.write_text(case["yaml"])
    (home / "library").mkdir(exist_ok=True)                 # D791: the loop's library
    try:
        return load_task(doc)
    except (TaskError, TypeError, ValueError) as exc:
        if pending and ("cutoff" in str(exc) or "goal" in str(exc) or "balance" in str(exc)):
            pytest.skip(f"{LOOP_PENDING}: {exc}")
        if case.get("pendingEstimate") and "estimate" in str(exc):
            pytest.skip(f"needs the loop's per-stage `estimate`: {exc}")
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


def test_five_checks_and_five_measurements_load_in_order_and_abbreviate(tmp_path):
    t = _load(tmp_path, BUILT["many"])
    assert [c.name for c in t.gate] == ["lint", "golden", "test", "a_rather_long_custom_check", "another_long_custom_check"]
    assert [s.name for s in t.stages] == ["synth", "place", "route", "post_route_power_estimate", "gate_level_simulation_run"]
    for text in BUILT["abbrev"].values():         # the diagram's second line never overflows its box
        assert len(text) <= 24 and "+" in text, text
    assert BUILT["abbrev"]["checks"].startswith("lint \u2192 golden")


def test_the_clock_follows_an_fmax_limit_unless_typed(tmp_path):
    t = _load(tmp_path, BUILT["many"])                       # at least 1250 MHz, no clock typed
    assert all(s.command[-1] == "800" for s in t.stages[:3]), [s.command for s in t.stages]
    t = _load(tmp_path, BUILT["typed_clock"])                # the rtl state asks 1000 MHz; one clock typed
    assert t.stages[0].command[-1] == "400" and t.stages[1].command[-1] == "1000"


def test_maximising_fmax_at_a_fixed_clock_warns_unless_the_clock_is_searched(tmp_path):
    assert "A fixed clock biases" in _warnings(BUILT["fastest"])
    assert "fixed clock" not in _warnings(BUILT["fastest_searched"]) and not _errors(BUILT["fastest_searched"])
    t = _load(tmp_path, BUILT["fastest_searched"])
    assert t.stages[0].command[-1] == "{clock_ps}" and t.space["clock_ps"] == [250, 333, 500]


def test_a_catalog_stage_without_run_writes_its_stage_shape(tmp_path):
    y = BUILT["evaluated"]["yaml"]
    assert "evaluator: zigzag" in y and "command:" not in y.split("measure:")[1]
    t = _load(tmp_path, BUILT["evaluated"])
    assert t.stages[0].evaluator == "zigzag" and t.stages[0].command is None
    assert set(t.stages[0].metrics) == {"latency_cycles", "energy_pj"}


def test_the_real_catalogs_newer_tools_load(tmp_path):
    case = BUILT["zigzag_eval"]
    assert 'workload: "{home}/workload.yaml"' in case["yaml"] and "evaluator: zigzag" in case["yaml"]
    t = _load(tmp_path, case)
    assert t.stages[0].evaluator == "zigzag" and t.workload == "{home}/workload.yaml"
    t = _load(tmp_path, BUILT["prog_timed"])
    assert t.stages[0].command[-9:-6] == ("time", "--build", "c++ -O2 -o {out} {artifact}")
    t = _load(tmp_path, BUILT["stat_then_synth"])
    assert [s.command[-3] for s in t.stages] == ["stat", "synth"] and t.stages[0].needs == ("yosys",)


def test_an_objective_every_measurement_does_not_report_is_an_error():
    e = _errors(BUILT["bad_partly_reported"])
    assert "fmax_mhz, which stat does not report" in e and "every measurement must report every objective" in e
    assert "one workload per document" in _errors(BUILT["bad_two_workloads"])


@pytest.mark.parametrize("kind", ["surrogate", "command", "model"])
def test_a_measurement_may_estimate_first(tmp_path, kind):
    case = BUILT[f"estimate_{kind}"]
    want = {"surrogate": "estimate: {kind: surrogate, margin: 0.05}",
            "command": 'estimate: {kind: command, margin: 0.05, command: "{python} {home}/golden.py {artifact}"}',
            "model": "estimate: {kind: model, margin: 0.05}"}[kind]
    assert want in case["yaml"] and case["yaml"].count("estimate:") == 1
    assert "estimate:" not in BUILT["rtl_one_gate"]["yaml"]                  # off: unsaid
    t = _load(tmp_path, case)
    est = getattr(t.stages[1], "estimate", None)
    if est is None:
        pytest.skip("needs the loop's per-stage `estimate` (the key loads but is not kept yet)")
    assert est.get("kind") == kind if isinstance(est, dict) else est.kind == kind


def test_the_drawing_writes_no_removed_box_and_fixes_single_choice_boxes(tmp_path):
    for k, v in BUILT.items():
        if "yaml" in v:
            assert "analytical:" not in v["yaml"] and "simulation:" not in v["yaml"] and "records:" not in v["yaml"], k
    assert BUILT["fixed"] == {"test": True, "measure": True, "records": True, "select": False, "critique": False, "calibrate": False}
    assert "analytical" not in BUILT["defaults"]["flowBoxes"] and "simulation" not in BUILT["defaults"]["flowBoxes"]
    assert "the model picks the next part, rules pick the kind of work" in BUILT["defaults"]["orchestrate"]
    assert "library" in BUILT["defaults"]["knowledge"] and BUILT["defaults"]["lessons"] == "off"
    y = BUILT["agents_everywhere"]["yaml"]
    assert "knowledge: {\"off\": true, lessons: claude}" in y and "critique: model" in y and "test: {agent" not in y
    t = _load(tmp_path, BUILT["agents_everywhere"])
    assert t.flow["knowledge"] == ["none"] and t.flow["select"] == {"agent": "claude"}
    p = BUILT["papers_by_agent"]
    assert "knowledge: {digest: opencode}" in p["yaml"] and not _errors(p), p["yaml"]
    assert _load(tmp_path, p).digest_by == "opencode", "D773: the configurator's Background reading by an agent loads as one"
    assert "digest" not in BUILT["rtl_one_gate"]["yaml"], "D791: the model digests unsaid"
    r = BUILT["readback"]["papers"]
    assert (r["state"]["knowledgeFiles"], r["state"]["flow"]["digest"]) == ("a.md", "agent:opencode")
    assert not r["kept"], "D781, D791: the files and who digests are the configurator's own"


def test_pareto_needs_two_objectives_and_an_estimate_its_margin_and_command():
    assert "pareto) needs two objectives" in _errors(BUILT["bad_pareto_one_objective"])
    e = _errors(BUILT["bad_estimate"])
    assert "margin is a percentage" in e and "command that estimates it" in e


def _paren(line: str) -> str | None:
    """The parenthesis of a describe_flow line, before any ` -- or: ...` tail; None when it has none."""
    head = line.split(" -- ")[0]
    m = re.match(r"^\w+: [^(]*?\((.*)\)$", head)
    return m.group(1) if m else None


@pytest.mark.parametrize("case", ["empty_problem", "empty_parts", "empty_off"])
def test_the_popovers_say_what_task_check_says(tmp_path, case):
    from flux_loop.document import describe_flow

    t = _load(tmp_path, BUILT[case])
    lines = {ln.split(":", 1)[0]: ln for ln in describe_flow(t)}
    assert _paren(lines["orchestrate"]).startswith("the model picks" if case == "empty_parts" else "one design")
    for box, text in BUILT["explained"][case].items():
        assert text == _paren(lines[box]), (box, text, lines[box])


def test_the_estimates_say_what_task_check_says(tmp_path):
    from flux_loop.document import describe_stage

    t = _load(tmp_path, BUILT["estimates_said"])
    said = [describe_stage(st).split(" -- estimate: ", 1)[1] for st in t.stages]
    assert said == BUILT["explained"]["estimates"]


def test_the_loop_page_lists_the_crafters_boxes():
    """guide/loop-shape.md shows the crafter's drawing and explains it: its table has one row per
    box of the drawing, under the drawing's title, with the box's `flow:` key and every word the
    crafter can write for it."""
    page = (REPO / "website/docs/guide/loop-shape.md").read_text()
    assert 'id="flux-loop-drawing"' in page and "assets/crafter.js" in page
    rows = {}
    for line in page.split("## The boxes", 1)[1].split("### ", 1)[0].splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if line.startswith("|") and len(cells) == 4 and not set(cells[0]) <= set("-"):
            rows[cells[0]] = cells
    rows.pop("box")
    boxes = BUILT["boxes"]
    assert sorted(rows) == sorted(b["title"] for b in boxes.values())
    for key, box in boxes.items():
        _, flow_key, does, choices = rows[box["title"]]
        written = {"dse": "orchestrate"}.get(key, key)        # D797: the search is said as `orchestrate`
        assert flow_key == (f"`{written}`" if box["flow"] else ""), key
        assert does.startswith(box["says"]), (key, does)
        said = set(re.findall(r"`\{?(\w+)", choices))
        for value in box["values"]:
            if value == "default" or value.startswith("agent:") or len(box["values"]) == 1:
                continue                                # unsaid, `{agent: ...}` (below the table), fixed
            assert value in said, (key, value, choices)
        if any(v.startswith("agent:") for v in box["values"]):
            assert "coding agent" in choices, key
        if len(box["values"]) == 1:
            assert "**fixed**" in choices, key

