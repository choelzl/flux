"""The website's loop crafter (website/docs/assets/crafter.js) writes documents the loader takes.

`buildYaml(state, catalog)` runs under node with the tool catalog the page fetches
(website/docs/assets/tools.json): every preset, every kind of problem (kit) with each goal
sentence its measurements allow, a hand-made gate of three checks in order and three stages
with cutoffs, and hand-made flows (every box a coding agent where one may answer; the model
halves). Each document is written beside the files it names (copied from the template or
application it follows) and loaded with `flux_loop.load_task`. `check(state)` must flag what can
still go wrong."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from flux_loop import load_task

REPO = Path(__file__).resolve().parents[3]
ASSETS = REPO / "website/docs/assets"
TEMPLATES = REPO / "flux/interfaces/cli/src/flux_cli/templates"
APPS = REPO / "flux/applications"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")

#: files a case's document names -> where they come from
FILES = {
    "rtl": (TEMPLATES / "rtl", ["golden.py"]),
    "rtl-sweep": (TEMPLATES / "rtl-sweep", ["golden.py", "gen.py"]),
    "tune": (TEMPLATES / "tune", ["check.py", "bench.py", "workload.py"]),
    "python": (TEMPLATES / "python", ["check.py", "bench.py"]),
    "program": (TEMPLATES / "python", ["check.py", "bench.py"]),
    "config": (APPS / "prefetcher", ["bingo.py", "knobs.md", "bingo_default.ini"]),
    "zigzag": (APPS / "npu_gemm", ["check.py", "measure.py", "render.py", "workload.yaml"]),
    "champsim": (APPS, []),
    "own": (APPS, []),
}

SCRIPT = r"""
const c = require(process.argv[1]);
c.setCatalog(JSON.parse(require("fs").readFileSync(process.argv[2], "utf8")));
const out = {};
const add = (name, files, s, extra) => { out[name] = Object.assign({files, state: s}, extra || {}); };
for (const p of c.PRESETS) add("preset_" + p.key, p.key, c.preset(p.key));

// every kit x every goal sentence its measurements allow
for (const kit of c.KIT_ORDER) {
  const s0 = c.base();
  s0.id = "kit_" + kit; s0.statement = "Whatever the " + kit + " kit makes.";
  c.setKit(s0, kit);
  if (kit === "own") {
    s0.checks[0].params.command = "{python} {home}/check.py {artifact}";
    s0.stages[0].params.command = "{python} {home}/score.py {artifact}";
    s0.stages[0].metrics = "score, size_kb";
    s0.objectives = [c.objective("score", "maximize", {target: "goal", goal: "10"}), c.objective("size_kb", "minimize")];
  }
  for (const g of c.goalsFor(s0)) {
    if (g.key === "custom" && kit !== "own") continue;
    const s = JSON.parse(JSON.stringify(s0));
    s.goal = {sentence: g.key, number: g.number === "percent" ? "85" :
              g.number === "target" ? (kit === "rtl" ? "" : kit === "zigzag" ? "400" : "1.1") : ""};
    add("kit_" + kit + "__" + g.key, kit, s, {sentence: g.key});
  }
}

// a gate of three checks, in order, and three stages with cutoffs
let s = c.preset("rtl");
s.id = "sequence";
s.checks.push(c.checkRow("custom-check", "extra", {command: "{python} {home}/golden.py"}));
s.checks[2].count_re = "(\\d+) bad"; s.checks[2].timeout = "30";
s.stages.push(c.stageRow("rtl-route", "signoff", {clock_ps: 500}));
s.stages[0].cutoff = {metric: "fmax_mhz", rule: "at", value: "1500"};
s.stages[1].cutoff = {metric: "path_ps", rule: "within", value: "10"};
s.stages[2].cutoff = {metric: "area_um2", rule: "below", value: "80"};
add("sequence", "rtl", s);

// every box a coding agent where one may answer; a search policy over a space
s = c.preset("rtl-sweep");
for (const b of c.DELEGABLE) s.flow[b] = "agent:" + c.AGENTS[c.DELEGABLE.indexOf(b) % 3];
s.flow.dse = "gradient";
add("all_agents", "rtl-sweep", s);

// the model with tools picks the work; a model critic, lessons mined, a surrogate, calibration off
s = c.preset("python");
Object.assign(s.flow, {orchestrate: "agent", critique: "llm", extract: "mined", analytical: "surrogate",
                       calibrate: "off", feedback: "none", knowledge: "digest", validate: "llm", plan: "llm"});
s.budget.prototype = "false"; s.budget.passes = "2";
add("model_boxes", "python", s);

// what can still go wrong
const bad = (name, s) => add(name, "own", s, {bad: true});
s = c.preset("rtl"); s.flow.test = "agent:claude"; bad("bad_agent", s);
s = c.preset("rtl"); s.stages[0].cutoff = {metric: "energy_pj", rule: "at", value: "1"}; bad("bad_cutoff_metric", s);
s = c.preset("rtl"); s.stages[0].cutoff = {metric: "fmax_mhz", rule: "within", value: "150"}; bad("bad_within", s);
s = c.preset("rtl"); s.goal = {sentence: "custom", number: ""};
s.objectives = [c.objective("latency_ns", "minimize")]; bad("bad_metric", s);
s = c.preset("rtl"); s.checks.push(c.checkRow("champsim-build", "golden")); bad("bad_duplicate_and_language", s);
s = c.preset("rtl"); s.checks.push(c.checkRow("custom-check", "mine")); bad("bad_missing_param", s);
s = c.preset("zigzag"); s.goal.number = ""; bad("bad_reach", s);
s = c.preset("config"); s.goal.number = "150"; bad("bad_keep", s);
s = c.preset("tune"); s.flow.dse = "none"; s.space = []; bad("bad_settings", s);

for (const k in out) {
  out[k].yaml = c.buildYaml(out[k].state);
  out[k].check = c.check(out[k].state);
  out[k].objectives = c.resolve(out[k].state).objectives;
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


@pytest.fixture(scope="module")
def built():
    return BUILT


def _load(tmp_path: Path, case: dict):
    src, files = FILES[case["files"]]
    doc_id = case["state"]["id"]
    for f in files:
        (tmp_path / f).write_text((src / f).read_text().replace("__NAME__", doc_id))
    doc = tmp_path / f"{doc_id}.problem.yaml"
    doc.write_text(case["yaml"])
    return load_task(doc)


def _errors(case):
    return " ".join(m["text"] for m in case["check"] if m["level"] == "error")


def _warnings(case):
    return " ".join(m["text"] for m in case["check"] if m["level"] == "warning")


def test_every_kit_offers_the_sentences_its_measurements_allow(built):
    offered: dict[str, set] = {}
    for k in built:
        if "__" in k:
            offered.setdefault(k.split("__")[0], set()).add(k.split("__")[1])
    assert set(offered) == {f"kit_{k}" for k in ("rtl", "program", "python", "champsim", "zigzag", "own")}
    assert offered["kit_rtl"] == {"fastest", "smallest", "power", "reach", "keep", "knee"}
    assert offered["kit_zigzag"] == {"fastest", "smallest", "power", "reach", "knee"}     # no keep on a number to minimise
    assert offered["kit_champsim"] == offered["kit_python"] == offered["kit_program"] == {"fastest"}
    assert offered["kit_own"] == {"custom"}


@pytest.mark.parametrize("name", GOOD)
def test_the_crafter_writes_a_document_the_loader_takes(built, tmp_path, name):
    case = built[name]
    assert not _errors(case), case["check"]
    task = _load(tmp_path, case)
    assert task.id == case["state"]["id"] and task.statement and len(task.gate) >= 1
    for st in task.stages:            # every stage measures every goal
        assert {o.metric for o in task.objectives} <= set(st.metrics), (st.name, st.metrics)
    got = [o.to_doc() for o in task.objectives]
    assert [o["metric"] for o in got] == [o["metric"] for o in case["objectives"]]
    sentence = case.get("sentence")
    if sentence == "reach":
        assert got[0].get("goal") is not None and len(got) == 2
    if sentence == "keep":
        assert got[0]["keep"] == 0.85 and len(got) == 2
    if sentence == "knee":
        assert len(got) == 2 and "goal" not in got[0] and "keep" not in got[0]


def test_a_gate_is_the_checks_in_order_and_each_stage_has_its_cutoff(built, tmp_path):
    t = _load(tmp_path, built["sequence"])
    assert [c.name for c in t.gate] == ["lint", "golden", "extra"]
    assert t.gate.named("lint").run[-2:] == ("lint", "{artifact}")
    assert t.gate.named("golden").run[-3:] == ("{artifact}", "--golden", "{home}/golden.py")
    extra = t.gate.named("extra")
    assert extra.count_re == r"(\d+) bad" and extra.timeout_s == 30
    assert [s.cutoff for s in t.stages] == [{"metric": "fmax_mhz", "at": 1500}, {"metric": "path_ps", "within": 0.1},
                                            {"metric": "area_um2", "below": 80}]
    assert "path_ps" in t.stages[1].metrics and [s.name for s in t.stages] == ["screen", "confirm", "signoff"]
    assert "last measurement's gate" in _warnings(built["sequence"])


def test_the_kits_say_what_the_shipped_documents_say(built, tmp_path):
    t = _load(tmp_path, built["kit_rtl__reach"])
    assert [c.name for c in t.gate] == ["lint", "golden"] and t.objectives[0].goal == 1000    # the clock's speed
    assert t.stages[1].needs == ("yosys", "openroad")
    t = _load(tmp_path, built["kit_champsim__fastest"])
    assert [c.name for c in t.gate] == ["build", "smoke"] and t.language == "cpp"
    assert t.stages[0].needs == ("pythia",) and t.stages[1].command[-4:] == ("--warmup", "100000000", "--sim", "150000000")
    t = _load(tmp_path, built["kit_program__fastest"])
    assert t.budget["workers"] == 1 and t.gate.named("test").timeout_s == 60
    t = _load(tmp_path, built["preset_config"])
    assert (t.objectives[0].keep, t.objectives[0].above, t.objectives[1].unit) == (0.9, 1.0, "B")
    t = _load(tmp_path, built["preset_tune"])
    assert t.gate.named("test").run[-2:] == ("{block}", "{order}")
    t = _load(tmp_path, built["preset_zigzag"])
    assert t.stages[0].metrics == ("latency_cycles", "energy_pj", "area_mm2") and t.objectives[0].goal == 500


def test_the_hand_made_flows_say_what_they_chose(built, tmp_path):
    t = _load(tmp_path, built["all_agents"])
    assert t.flow["dse"] == "gradient" and "orchestrate" not in t.flow        # the policy leads
    assert t.generator == {"agent": t.flow["generate"]["agent"]}
    for box in ("validate", "plan", "critique", "extract", "select"):
        assert t.flow[box]["agent"] in ("opencode", "claude", "codex"), box
    assert "test" not in t.flow
    m = _load(tmp_path, built["model_boxes"])
    assert m.flow["extract"] == "mined" and m.flow["analytical"] == ["surrogate"] and m.budget["calibrate"] is False
    assert m.roles["orchestrator"] == "agent" and m.critique and "plan" in m.budget["agent"]


def test_a_preset_says_only_what_is_its_own(built):
    y = built["preset_rtl-sweep"]["yaml"]
    assert "gate: flux rtl test {artifact} --golden {home}/golden.py" in y        # one check: the short form
    assert "validate" not in y and "records" not in y and "metrics:" not in y


def test_check_flags_what_can_still_go_wrong(built):
    assert "never handed to a coding agent" in _errors(built["bad_agent"])
    assert "test:" not in built["bad_agent"]["yaml"]                  # never written, even so
    assert "does not report energy_pj" in _errors(built["bad_cutoff_metric"])
    assert "percentage between 1 and 100" in _errors(built["bad_within"])
    assert "latency_ns" in _errors(built["bad_metric"])
    assert 'Two checks are named "golden"' in _errors(built["bad_duplicate_and_language"])
    assert "is for cpp" in _warnings(built["bad_duplicate_and_language"])
    assert "needs its command" in _errors(built["bad_missing_param"])
    assert "number to reach" in _errors(built["bad_reach"])
    assert "percentage between 1 and 100" in _errors(built["bad_keep"])
    assert "{block}" in _errors(built["bad_settings"])
