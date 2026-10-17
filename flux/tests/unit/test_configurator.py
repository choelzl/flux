"""The web configurator reads a document back (D686): `fromDoc(raw, normal)` in crafter.js, then
`buildYaml`, then the server's merge of the kept keys. Every document of the repository, read
back and written again, loads to the same gate, stages, objectives, flow, budget, space and
parts as before."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from flux_loop import load_task
from flux_web.configure import merged, views

REPO = Path(__file__).resolve().parents[3]
ASSETS = REPO / "website/docs/assets"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")

DOCS = sorted([*REPO.glob("flux/applications/*/problem.yaml"), *REPO.glob("flux/applications/*/*.problem.yaml"),
               *REPO.glob("flux/tests/fixtures/loops/*/problem.yaml"),
               REPO / "flux/core/loop/examples/digits/problem.json"])
COMPARED = ("id", "language", "gate", "stages", "objectives", "flow", "budget", "space", "parts", "skills")

JS = r"""
const c = require(process.argv[1]);
c.setCatalog(JSON.parse(require("fs").readFileSync(process.argv[2], "utf8")));
const views = JSON.parse(require("fs").readFileSync(0, "utf8"));
const got = c.fromDoc(views.raw, views.normal);
process.stdout.write(JSON.stringify({yaml: c.buildYaml(got.state), kept: got.kept, notes: got.notes,
                                     checks: c.check(got.state)}));
"""


def _round(path: Path, tmp: Path) -> tuple[dict, dict, list[str]]:
    home = tmp / path.parent.name.replace("-", "_")          # D786: the folder's name is the id
    shutil.copytree(path.parent, home, ignore=shutil.ignore_patterns("out", "__pycache__", "*.db"))
    src = home / path.name
    v = views(src)
    assert v["normal"] is not None, v["error"]
    r = subprocess.run(["node", "-e", JS, str(ASSETS / "crafter-model.js"), str(ASSETS / "tools.json")],
                       input=json.dumps({"raw": v["raw"], "normal": v["normal"]}, default=str),
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    text = merged(out["yaml"], v["raw"], out["kept"])
    again = home / ("again" + (".task.json" if src.suffix == ".json" else ".problem.yaml"))
    again.write_text(text if src.suffix != ".json" else json.dumps(yaml.safe_load(text)))
    return v["normal"], load_task(str(again)).to_dict(), out["kept"]


@pytest.mark.parametrize("path", DOCS, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_a_document_read_back_and_written_again_is_the_same_loop(path, tmp_path):
    before, after, kept = _round(path, tmp_path)
    for key in COMPARED:
        assert after.get(key) == before.get(key), (key, kept)


@pytest.mark.parametrize("kind", ["rtl", "python", "sweep", "rtl-sweep"])
def test_a_template_is_editable_whole(kind, tmp_path):
    """Nothing of what `flux new` writes is kept aside: the configurator edits all of it."""
    path = REPO / "flux/tests/fixtures/loops" / kind / "problem.yaml"
    _before, _after, kept = _round(path, tmp_path)
    assert kept == [], kept


@pytest.mark.parametrize("baseline", [True, False, {"only": True}, {"file": "check.py"},
                                    {"file": "check.py", "only": True},
                                    {"command": "{python} {home}/check.py {artifact}", "timeout_s": 12},
                                    {"command": ["{python}", "{home}/check.py", "{artifact}"], "only": True}])
def test_baseline_settings_are_editable_and_round_trip(baseline, tmp_path):
    src = REPO / "flux/applications/primes"
    home = tmp_path / "baseline"
    shutil.copytree(src, home)
    doc = home / "problem.yaml"
    raw = yaml.safe_load(doc.read_text())
    raw["baseline"] = baseline
    doc.write_text(yaml.safe_dump(raw, sort_keys=False))
    round_dir = tmp_path / "round"
    round_dir.mkdir()
    before, after, kept = _round(doc, round_dir)
    assert "baseline" not in kept
    assert before.get("baseline") == after.get("baseline")


def test_an_agent_with_its_own_settings_is_an_agent_kept_as_written(tmp_path):
    """D728: `generate: {by: opencode, bin, args}` and a custom-command critic were not
    the configurator's choices, so the whole flow fell back to its defaults ("a model") and was
    kept aside. Now each is its agent -- drawn as one -- and written back as it was."""
    src = REPO / "flux/applications/primes"
    home = tmp_path / "p"
    shutil.copytree(src, home)
    doc = home / "problem.yaml"
    raw = yaml.safe_load(doc.read_text())
    raw["flow"] = {**(raw.get("flow") or {}),
                   "generate": {"by": "opencode", "bin": "oc-mod", "args": ["--agent", "flux"], "timeout_s": 900},
                   "select": {"critic": {"by": {"command": "my-critic {prompt_file}", "output": "text"}}}}
    doc.write_text(yaml.safe_dump(raw, sort_keys=False))
    v = views(doc)
    assert v["normal"] is not None, v["error"]
    js = JS.replace("process.stdout.write(JSON.stringify({yaml:", "process.stdout.write(JSON.stringify({flow: got.state.flow, half: ['generate', 'critique'].map(b => c.halfOf ? c.halfOf(got.state, b) : null), yaml:")
    r = subprocess.run(["node", "-e", js, str(ASSETS / "crafter-model.js"), str(ASSETS / "tools.json")],
                       input=json.dumps({"raw": v["raw"], "normal": v["normal"]}, default=str), capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["flow"]["generate"] == "agent:opencode" and out["flow"]["critique"] == "agent:*"
    assert not any("flow" in k for k in out["kept"]), out["kept"]
    again = home / "again.problem.yaml"
    again.write_text(merged(out["yaml"], v["raw"], out["kept"]))
    flow = yaml.safe_load(again.read_text())["flow"]
    assert flow["generate"] == raw["flow"]["generate"] and flow["select"] == raw["flow"]["select"]
    assert not any("critic" in (flow.get(b) or {}) for b in ("orchestrate", "test")), "only the box the document's critic judged (D1040)"
    assert load_task(str(again)).to_dict()["flow"] == v["normal"]["flow"]


# D910: an untouched read-back keeps a document's meaning -- typed choices, a comma inside one,
# an inline Workload IR, a `needs` of a catalog stage's own, a script path with a space
_BASE = {"statement": "Make valid source with the smallest time.", "language": "python",
         "flow": {"test": "{python} {home}/check.py {artifact}",
                  "measure": {"bench": {"command": "{python} {home}/bench.py {artifact}", "metrics": ["time_ms"]}}},
         "objectives": [{"metric": "time_ms", "direction": "minimize"}]}


def _variant(name: str) -> dict:
    d = json.loads(json.dumps(_BASE))
    if name == "scalar_choices":
        d["flow"]["orchestrate"] = {"policy": "sweep", "space": {"mode": ["01", "true", "fast,wide", 2, True, 0.5, 1e-07]}}
        d["flow"]["generate"] = {"command": "{python} {home}/gen.py {artifact} {mode}"}
    elif name == "needs_override":
        d["flow"]["measure"]["bench"]["needs"] = ["a-required-tool"]
    elif name == "spaced_script":
        d["flow"]["measure"]["bench"]["command"] = ["{python}", "{home}/my bench.py", "{artifact}"]
    elif name == "params_placeholder":
        d["params"] = {"n": 1}
        d["flow"]["test"] = "{python} {home}/check.py {artifact} {params}"
    # D1134: what a round trip lost before
    elif name == "kept_flow_knowledge":            # phases keep the whole flow; its knowledge went with it
        d["flow"]["orchestrate"] = {"policy": [{"policy": "sweep"}, {"policy": "gradient"}], "space": {"mode": ["a", "b"]}}
        d["flow"]["generate"] = {"command": "{python} {home}/gen.py {artifact} {mode}"}
        d["flow"]["knowledge"] = {"digest": "claude", "lessons": "mined"}
    elif name == "model_search_options":
        d["flow"]["orchestrate"] = {"by": "model", "rounds": 3, "batch_size": 2, "space": {"mode": ["a", "b"]}}
        d["flow"]["generate"] = {"command": "{python} {home}/gen.py {artifact} {mode}"}
    elif name == "policy_options":
        d["flow"]["orchestrate"] = {"policy": {"gradient": {"steps": 4, "patience": 2}}, "space": {"mode": [1, 2, 3]}}
        d["flow"]["generate"] = {"command": "{python} {home}/gen.py {artifact} {mode}"}
    elif name == "critic_options":
        d["flow"]["test"] = {"check": "{python} {home}/check.py {artifact}", "critic": {"by": "claude", "timeout_s": 99}}
    elif name == "lessons_options":
        d["flow"]["knowledge"] = {"lessons": {"by": "claude", "timeout_s": 99}}
    # D1136: the Library's text, method sheet and folders are the configurator's, not kept as written
    elif name == "library":
        d["flow"]["knowledge"] = {"files": ["rtl/", "check.py"], "sheet": "method.md", "text": "Widths are 8 bits.\nNo latches:  ever.",
                                  "digest": "claude", "lessons": "mined"}
    elif name == "library_kept_flow":            # a flow kept as written keeps its choices; what is read is still edited
        d["flow"]["orchestrate"] = {"policy": [{"policy": "sweep"}, {"policy": "gradient"}], "space": {"mode": ["a", "b"]}}
        d["flow"]["generate"] = {"command": "{python} {home}/gen.py {artifact} {mode}"}
        d["flow"]["knowledge"] = {"files": ["rtl"], "text": "Read the spec first.", "digest": "claude"}
    # D1142: an agent's own settings, edited now -- time limit, session, questions, Design's probe
    elif name == "agent_options":
        d["flow"]["test"] = {"test": "{python} {home}/check.py {artifact}", "lint": "{python} {home}/check.py {artifact}"}
        d["budget"] = {"revert_after": 2, "parallel_parts": 2}
        d["flow"]["generate"] = {"by": "claude", "timeout_s": 900, "questions": "operator", "max_questions": 1, "wait_s": 60,
                                 "probe": {"gate": {"runs": 5, "checks": ["lint"]}, "stages": 2, "bench": 1}}
        d["flow"]["select"] = {"by": "codex", "session": "pass", "finalists": 2}
    elif name == "probe_off":
        d["flow"]["generate"] = {"by": "claude", "probe": False}
    elif name == "probe_gate_off":
        d["flow"]["generate"] = {"by": "opencode", "bin": "oc-mod", "probe": {"gate": 0}}
    elif name == "critic_session":
        d["flow"]["select"] = {"critic": {"by": "claude", "session": "pass", "questions": "model"}}
    # D1152: the Knowledge box -- skills off, one by one, from a folder; the workbench's digest
    elif name == "skills_off":
        d["skills"] = "off"
    elif name == "skills_some_off":
        d["skills"] = {"off": ["flux-loop-commands", "s1"]}
    elif name == "skills_from":
        d["skills"] = ["skills"]
    elif name == "skills_from_some_off":
        d["skills"] = {"from": ["skills/s1"], "off": ["flux-loop-commands"]}
    elif name == "workbench_off":
        d["flow"]["knowledge"] = {"files": ["check.py"], "workbench": "off", "lessons": "mined"}
    elif name == "workbench_agent":
        d["flow"]["knowledge"] = {"digest": "codex", "workbench": {"by": "claude", "timeout_s": 99}}
    elif name == "library_off_workbench":
        d["flow"]["knowledge"] = {"off": True, "lessons": "claude", "workbench": "model"}
    # D1159: the paths hidden from the agents -- beside the library, with it off, with the flow kept as written
    elif name == "hidden":
        d["flow"]["knowledge"] = {"files": ["check.py"], "hidden": ["rtl/", "method.md"]}
    elif name == "hidden_library_off":
        d["flow"]["knowledge"] = {"off": True, "hidden": ["rtl/*.sv"], "lessons": "mined"}
    elif name == "hidden_kept_flow":
        d["flow"]["orchestrate"] = {"policy": [{"policy": "sweep"}, {"policy": "gradient"}], "space": {"mode": ["a", "b"]}}
        d["flow"]["generate"] = {"command": "{python} {home}/gen.py {artifact} {mode}"}
        d["flow"]["knowledge"] = {"digest": "claude", "hidden": ["rtl/"]}
    elif name == "hidden_agent":
        d["flow"]["generate"] = {"by": "claude", "hidden": ["method.md"]}
    return d


@pytest.mark.parametrize("name", ["scalar_choices", "needs_override", "spaced_script", "params_placeholder", "kept_flow_knowledge",
                                  "model_search_options", "policy_options", "critic_options", "lessons_options", "library", "library_kept_flow",
                                  "agent_options", "probe_off", "probe_gate_off", "critic_session", "skills_off", "skills_some_off",
                                  "skills_from", "skills_from_some_off", "workbench_off", "workbench_agent", "library_off_workbench",
                                  "hidden", "hidden_library_off", "hidden_kept_flow", "hidden_agent"])
def test_an_untouched_edit_keeps_the_meaning(name, tmp_path):
    src = tmp_path / "src" / name
    src.mkdir(parents=True)
    for f in ("check.py", "bench.py", "my bench.py", "gen.py", "method.md", "rtl/top.sv"):
        (src / f).parent.mkdir(parents=True, exist_ok=True)
        (src / f).write_text("print('time_ms=1')\n")
    (src / "skills" / "s1").mkdir(parents=True)
    (src / "skills" / "s1" / "SKILL.md").write_text("---\nname: s1\ndescription: a skill\n---\nuse it\n")
    (src / "problem.yaml").write_text(yaml.safe_dump(_variant(name), sort_keys=False))
    (tmp_path / "w").mkdir()
    before, after, kept = _round(src / "problem.yaml", tmp_path / "w")
    for key in COMPARED:
        assert after.get(key) == before.get(key), (key, kept)
    if name.startswith(("skills", "workbench")) or name == "library_off_workbench":
        assert not [k for k in kept if "skills" in k or "knowledge" in k or k == "flow.boxes"], kept
        assert after.get("skills") == _variant(name).get("skills")
    if name.startswith("hidden"):
        assert not [k for k in kept if "knowledge" in k], kept
        assert (after["flow"].get("knowledge") or {}).get("hidden") == (_variant(name)["flow"].get("knowledge") or {}).get("hidden")
    if name.startswith("library_kept") or name == "library":
        assert "flow.knowledge" not in kept and "FILE rtl/top.sv:" in after["flow"]["knowledge"]["text"], (kept, after["flow"])
    # a typed choice is the same value of the same type: "01" is not 1, "true" is not True
    space = (before.get("space") or {}).get("mode")
    if space:
        assert [type(x) for x in after["space"]["mode"]] == [type(x) for x in space]


SEARCH_JS = r"""
const c = require(process.argv[1]);
c.setCatalog(JSON.parse(require("fs").readFileSync(process.argv[2], "utf8")));
const out = {};
for (const [name, gen, cmd, test] of [["model", "model", "", "{python} {home}/check.py {artifact}"],
                                      ["agent", "agent:codex", "", "{python} {home}/check.py {artifact}"],
                                      ["script", "command", "{python} {home}/gen.py {artifact} {x}", "{python} {home}/check.py {artifact}"],
                                      ["tune", "model", "", "{python} {home}/check.py {artifact} {x}"]]) {
  const s = c.base();
  Object.assign(s, {id: "s", statement: "a sweep", language: "python", generateCommand: cmd, space: [{knob: "x", choices: "1, 2"}]});
  s.flow.dse = "sweep"; s.flow.generate = gen;
  s.checks = [{type: "custom", tool: "custom-check", name: "test", params: {command: test}, count_re: "", timeout: ""}];
  s.stages = [{tool: "custom-stage", name: "bench", params: {command: "{python} {home}/bench.py {artifact}"}, metrics: "t", needs: "", gates: []}];
  s.objectives = [c.newObjective("t", "min")];
  out[name] = {half: c.halfOf(s, "generate"), checks: c.check(s)};
}
process.stdout.write(JSON.stringify(out));
"""


def test_a_search_draws_and_checks_only_the_generator_that_runs():
    """D911: Flux makes a search's point into a design through `flow.generate: {command}` alone, so
    the configurator neither draws a model or an agent there nor lets the search go unsaid."""
    r = subprocess.run(["node", "-e", SEARCH_JS, str(ASSETS / "crafter-model.js"), str(ASSETS / "tools.json")],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    for name in ("model", "agent"):
        assert out[name]["half"] == "off"
        assert any(m["level"] == "error" and "only through a script" in m["text"] and m["step"] == 3 for m in out[name]["checks"]), out[name]   # D941: Graph
    assert out["script"]["half"] == "rules" and not [m for m in out["script"]["checks"] if m["level"] == "error"]
    tune = out["tune"]
    assert tune["half"] == "off" and not [m for m in tune["checks"] if m["level"] == "error"]
    assert any("parameter-only search" in m["text"] for m in tune["checks"])


def test_the_checklist_knows_the_loops_own_placeholders():
    """D912: the crafter's placeholders are the loader's -- `{params}` is no "unknown placeholder"."""
    from flux_loop.document import BUILTIN_SUBS

    r = subprocess.run(["node", "-e", "process.stdout.write(JSON.stringify(require(process.argv[1]).BUILTIN_SUBS))",
                        str(ASSETS / "crafter-model.js")], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout) == list(BUILTIN_SUBS)


def test_the_wizard_has_six_steps_and_old_steps_land_on_theirs():
    """D941: Prompt, Check & Measure, Objective, Graph, Extra, Save -- every message names one of them,
    and the seven steps' names and numbers (D826) land on the step that holds their fields now."""
    js = ("const c = require(process.argv[1]); process.stdout.write(JSON.stringify({ids: c.STEP_IDS, of: c.STEP_OF,"
          " old: [0, 1, 2, 3, 4, 5, 6].map(i => c.stepIndex(i, true)), steps: c.check(c.base()).map(m => m.step)}))")
    r = subprocess.run(["node", "-e", js, str(ASSETS / "crafter-model.js")], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["ids"] == ["prompt", "measure", "objective", "graph", "extra", "save"]
    assert out["old"] == [0, 1, 1, 2, 3, 4, 5]
    assert [out["of"][k] for k in ("problem", "checks", "measurements", "objectives", "flow", "more", "review")] == [0, 1, 1, 2, 3, 4, 5]
    assert set(out["steps"]) <= set(range(5)) and {0, 1, 2} <= set(out["steps"])


def test_an_added_agent_called_custom_is_one_choice_however_often_the_agents_are_set():
    """D946: the configurator's marker for an agent with its own settings was "agent:custom", spared
    when the choices were rebuilt -- an agent added as `custom` came back once more on every visit."""
    js = r"""
const c = require(process.argv[1]);
for (let i = 0; i < 5; i++) c.setAgents([{name: "custom", label: "Custom"}, {name: "nga", label: "NGA"}]);
process.stdout.write(JSON.stringify(Object.fromEntries(Object.entries(c.BOXES).map(([b, x]) =>
  [b, (x.choices || []).filter(o => String(o.value).startsWith("agent:")).map(o => o.value)]))));
"""
    r = subprocess.run(["node", "-e", js, str(ASSETS / "crafter-model.js")], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    agents = {b: v for b, v in json.loads(r.stdout).items() if v}
    assert agents and all(v == ["agent:opencode", "agent:claude", "agent:codex", "agent:custom", "agent:nga"] for v in agents.values()), agents


GRAPH_JS = r"""
const c = require(process.argv[1]);
c.setCatalog(JSON.parse(require("fs").readFileSync(process.argv[2], "utf8")));
const s = c.base(); s.id = "graphed"; s.statement = "Anything."; s.language = "python";
s.checks.push(c.newCheck(s, "custom")); s.checks[0].params.command = "{python} {home}/check.py {artifact}";
s.stages.push(c.newStage(s, "custom-stage")); s.stages[0].params.command = "{python} {home}/check.py {artifact}"; s.stages[0].metrics = "score";
s.objectives = [c.newObjective("score", "max")];
const which = process.argv[3];
if (which === "search") { s.flow.orchestrate = "command"; s.orchestrateCommand = "{python} {home}/check.py {history} {state}"; s.orchestrateTimeout = "60"; }
if (which === "catalog") { s.flow.generate = "catalog"; s.generateCatalog = "check.py"; }
c.setCritic(s, "test", "model"); c.setCritic(s, "select", "agent:claude");
s.params = [{key: "width", value: "8"}, {key: "mode", value: "fast"}];
Object.assign(s.budget, {explore_every: "5", explore_after: "1", cooldown_after: "2", regress_after: "3", agent_continues: "2", patching: "false", budget_s: "900"});
process.stdout.write(JSON.stringify({yaml: c.buildYaml(s), check: c.check(s), state: s}));
"""

READ_JS = r"""
const c = require(process.argv[1]);
c.setCatalog(JSON.parse(require("fs").readFileSync(process.argv[2], "utf8")));
const v = JSON.parse(require("fs").readFileSync(0, "utf8"));
const got = c.fromDoc(v.raw, v.normal);
process.stdout.write(JSON.stringify({yaml: c.buildYaml(got.state), kept: got.kept, state: got.state}));
"""


@pytest.mark.parametrize("which", ["search", "catalog"])
def test_the_graphs_settings_load_and_read_back(tmp_path, which):
    """D1118: what the configurator's Graph sets beside each box -- a search command (D799) or the designs one has
    (a catalog), a critic per box it judges (D1040), the loop's knobs, the document's parameters -- is a document
    the loader takes, and reads back into the same form."""
    r = subprocess.run(["node", "-e", GRAPH_JS, str(ASSETS / "crafter-model.js"), str(ASSETS / "tools.json"), which],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert not [m for m in out["check"] if m["level"] == "error"], out["check"]
    home = tmp_path / "graphed"
    home.mkdir()
    (home / "check.py").write_text("print('score=1')\n")
    (home / "problem.yaml").write_text(out["yaml"])
    task = load_task(str(home / "problem.yaml"))
    assert task.critics.get("test") == {"model": True} or "test" in task.critics, task.critics
    assert "select" in task.critics and "orchestrate" not in task.critics
    assert task.budget["explore_every"] == 5 and task.budget["patching"] is False and task.params == {"width": 8, "mode": "fast"}
    if which == "catalog":
        assert task.generator == {"catalog": ["check.py"]}
    v = views(home / "problem.yaml")
    back = subprocess.run(["node", "-e", READ_JS, str(ASSETS / "crafter-model.js"), str(ASSETS / "tools.json")],
                          input=json.dumps({"raw": v["raw"], "normal": v["normal"]}, default=str), capture_output=True, text=True, timeout=60)
    assert back.returncode == 0, back.stderr
    got = json.loads(back.stdout)
    assert not got["kept"], got["kept"]
    assert got["yaml"] == out["yaml"]
    assert got["state"]["critics"] == {"orchestrate": "off", "test": "model", "measure": "off", "select": "agent:claude"}


LIBRARY_JS = r"""
const c = require(process.argv[1]);
c.setCatalog(JSON.parse(require("fs").readFileSync(process.argv[2], "utf8")));
const out = {};
for (const [name, on, digest, lessons] of [["agents", "default", "agent:claude", "agent:codex"], ["model_mined", "default", "model", "mined"],
                                          ["off_lessons", "none", "model", "mined"], ["off", "none", "agent:claude", "off"]]) {
  const s = c.base();
  Object.assign(s, {id: "lib", statement: "a library", language: "python", knowledgeFiles: "rtl/, golden.py",
                    knowledgeText: "Widths are 8 bits.", knowledgeSheet: "method.md"});
  s.flow.knowledge = on; s.flow.digest = digest; s.flow.lessons = lessons;
  s.checks = [{type: "custom", tool: "custom-check", name: "test", params: {command: "{python} {home}/golden.py"}, count_re: "", timeout: ""}];
  s.stages = [{tool: "custom-stage", name: "bench", params: {command: "{python} {home}/golden.py"}, metrics: "t", needs: "", gates: []}];
  s.objectives = [c.newObjective("t", "min")];
  out[name] = {yaml: c.buildYaml(s), named: c.namedFiles(s)};
}
process.stdout.write(JSON.stringify(out));
"""


def test_the_librarys_choices_load_and_read_back(tmp_path):
    """D1136: the Library box -- files and folders read, a text, a method sheet, who sums up the papers, the lessons --
    writes a document the loader takes, read back into the same choices; off reads nothing but may still draw lessons."""
    r = subprocess.run(["node", "-e", LIBRARY_JS, str(ASSETS / "crafter-model.js"), str(ASSETS / "tools.json")],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    want = {"agents": {"digest": "claude", "lessons": "codex"}, "model_mined": {"lessons": "mined"},
            "off_lessons": {"off": True, "lessons": "mined"}, "off": False}          # YAML reads a bare `off` as false
    for name, got in out.items():
        home = tmp_path / name / "lib"                  # D786: the folder is the id
        (home / "rtl").mkdir(parents=True)
        (home / "rtl/top.sv").write_text("module top; endmodule\n")
        for f in ("golden.py", "method.md"):
            (home / f).write_text("print('t=1')\n")
        (home / "problem.yaml").write_text(got["yaml"])
        raw = yaml.safe_load(got["yaml"])["flow"]["knowledge"]
        said = {k: v for k, v in raw.items() if k not in ("files", "sheet", "text")} if isinstance(raw, dict) else raw
        assert said == want[name], (name, got["yaml"])
        assert "rtl/" in got["named"] and "method.md" in got["named"]
        task = load_task(str(home / "problem.yaml"))
        if want[name] is False or want[name].get("off"):
            assert not task.knowledge, name
            continue
        assert raw["files"] == ["rtl/", "golden.py"] and raw["sheet"] == "method.md" and raw["text"] == "Widths are 8 bits."
        assert "Widths are 8 bits." in task.knowledge and "FILE rtl/top.sv:" in task.knowledge and "FILE golden.py:" in task.knowledge
        v = views(home / "problem.yaml")
        back = subprocess.run(["node", "-e", READ_JS, str(ASSETS / "crafter-model.js"), str(ASSETS / "tools.json")],
                              input=json.dumps({"raw": v["raw"], "normal": v["normal"]}, default=str), capture_output=True, text=True, timeout=60)
        assert back.returncode == 0, back.stderr
        again = json.loads(back.stdout)
        assert not again["kept"], again["kept"]
        assert again["yaml"] == got["yaml"]
        st = again["state"]
        assert (st["knowledgeFiles"], st["knowledgeText"], st["knowledgeSheet"]) == ("rtl/, golden.py", "Widths are 8 bits.", "method.md")


AGENT_JS = r"""
const c = require(process.argv[1]);
c.setCatalog(JSON.parse(require("fs").readFileSync(process.argv[2], "utf8")));
const v = JSON.parse(require("fs").readFileSync(0, "utf8"));
const s = c.fromDoc(v.raw, v.normal).state, out = {};
s.flow.generate = "agent:claude"; s.flow.select = "agent:codex"; s.flow.lessons = "agent:claude";
c.setAgentOpt(s, "generate", "timeout_s", "900");
c.setProbe(s, {off: false, runs: "4", checks: ["lint"], stages: "2"});
c.setAgentOpt(s, "select", "session", "pass"); c.setAgentOpt(s, "select", "questions", "operator");
c.setCritic(s, "test", "agent:codex"); c.setAgentOpt(s, "critic:test", "timeout_s", "60");
c.setAgentOpt(s, "lessons", "timeout_s", "30");
out.set = {yaml: c.buildYaml(s), probe: c.probeOf(s), errors: c.check(s).filter(m => m.level === "error")};
c.renameAgent(s, "generate", "codex"); s.flow.generate = "agent:codex";           // another agent: its settings go with it
out.renamed = c.buildYaml(s);
c.setProbe(s, {off: false, runs: "", checks: null, stages: ""}); c.setAgentOpt(s, "generate", "timeout_s", "");
c.setAgentOpt(s, "select", "session", ""); c.setAgentOpt(s, "select", "questions", "");
out.cleared = c.buildYaml(s);
s.checks.find(x => x.name === "lint").name = "lint2"; c.setProbe(s, {off: false, runs: "", checks: ["lint"], stages: ""});
out.stale = c.check(s).filter(m => m.level === "error").map(m => m.text);
process.stdout.write(JSON.stringify(out));
"""


def test_an_agents_settings_are_edited_and_load(tmp_path):
    """D1142: the configurator sets an agent's time limit, session and questions on a box, a critic and the lessons, and
    Design's probe (D1137) -- a document the loader takes, read back the same; another agent keeps them, cleared they go,
    and a probe of a check the gate no longer has is said before the save."""
    home = tmp_path / "agents"
    home.mkdir()
    for f in ("check.py", "bench.py"):
        (home / f).write_text("print('time_ms=1')\n")
    doc = json.loads(json.dumps(_BASE))
    doc["flow"]["test"] = {"test": "{python} {home}/check.py {artifact}", "lint": "{python} {home}/check.py {artifact}"}
    (home / "problem.yaml").write_text(yaml.safe_dump(doc, sort_keys=False))
    v = views(home / "problem.yaml")
    r = subprocess.run(["node", "-e", AGENT_JS, str(ASSETS / "crafter-model.js"), str(ASSETS / "tools.json")],
                       input=json.dumps({"raw": v["raw"], "normal": v["normal"]}, default=str), capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert not out["set"]["errors"], out["set"]["errors"]
    flow = yaml.safe_load(out["set"]["yaml"])["flow"]
    assert flow["generate"] == {"by": "claude", "timeout_s": 900, "probe": {"gate": {"checks": ["lint"], "runs": 4}, "stages": 2}}, flow
    assert flow["select"] == {"by": "codex", "session": "pass", "questions": "operator"}, flow
    assert flow["test"]["critic"] == {"by": "codex", "timeout_s": 60} and flow["knowledge"]["lessons"] == {"by": "claude", "timeout_s": 30}, flow
    assert out["set"]["probe"] == {"off": False, "runs": "4", "checks": ["lint"], "stages": "2"}
    (home / "problem.yaml").write_text(out["set"]["yaml"])
    task = load_task(str(home / "problem.yaml"))
    from flux_loop.agent import agent_spec
    a = agent_spec(task.generator["agent"])
    assert (a.timeout_s, a.probe_checks, dict(a.probe)["gate"], dict(a.probe)["stages"]) == (900.0, ("lint",), 4, 2)
    v = views(home / "problem.yaml")
    back = subprocess.run(["node", "-e", READ_JS, str(ASSETS / "crafter-model.js"), str(ASSETS / "tools.json")],
                          input=json.dumps({"raw": v["raw"], "normal": v["normal"]}, default=str), capture_output=True, text=True, timeout=60)
    assert back.returncode == 0, back.stderr
    again = json.loads(back.stdout)
    assert not again["kept"] and again["yaml"] == out["set"]["yaml"], again["kept"]
    renamed = yaml.safe_load(out["renamed"])["flow"]["generate"]
    assert renamed == {"by": "codex", "timeout_s": 900, "probe": {"gate": {"checks": ["lint"], "runs": 4}, "stages": 2}}, renamed
    cleared = yaml.safe_load(out["cleared"])["flow"]
    assert cleared["generate"] == "codex" and cleared["select"] == "codex", cleared
    assert out["stale"] and "lint" in out["stale"][0], out["stale"]


KEPT_JS = r"""
const c = require(process.argv[1]);
const doc = {statement: "x", budget: {steps: 4, mystery: 1}, objectives: [{metric: "t", direction: "minimize", tie: 0.1}], params: {width: {a: 1}}};
const got = c.fromDoc(doc, doc);
process.stdout.write(JSON.stringify({kept: got.kept, text: Object.fromEntries(got.kept.map(k => [k, c.keptText(got.state, k)]))}));
"""


def test_a_part_kept_as_written_is_shown_as_written():
    """D1136: each part kept as written has a read-only view of what the document says, for the step that would edit it."""
    r = subprocess.run(["node", "-e", KEPT_JS, str(ASSETS / "crafter-model.js")], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert set(out["kept"]) == {"budget", "objectives", "params"}, out
    assert out["text"] == {"budget": "steps: 4\nmystery: 1", "objectives": "- {metric: t, direction: minimize, tie: 0.1}", "params": "width: {a: 1}"}, out


WORD_JS = r"""
const c = require(process.argv[1]);
c.setCatalog(JSON.parse(require("fs").readFileSync(process.argv[2], "utf8")));
const v = JSON.parse(require("fs").readFileSync(0, "utf8"));
const s = c.fromDoc(v.raw, v.normal).state;
process.stdout.write(JSON.stringify({stages: s.stages.map(x => [x.tool, c.toolWord(x)]), checks: s.checks.map(x => c.toolWord(x)),
  own: c.toolWord({tool: "custom-stage", params: {command: "make -C {home} bench"}}),
  other: c.toolWord({tool: "custom-stage", params: {command: "fluxpreset rtl newthing {artifact}"}})}));
"""


def test_a_preset_measurement_is_named_by_its_preset():
    """D1141: a measurement that runs a preset but lists fewer numbers than it reports is read as a command of one's own
    (so it round-trips), yet its row says the preset ("Synthesise and time"), not "command"; a command of one's own
    says "command"."""
    v = views(REPO / "flux/applications/adder16/problem.yaml")
    r = subprocess.run(["node", "-e", WORD_JS, str(ASSETS / "crafter-model.js"), str(ASSETS / "tools.json")],
                       input=json.dumps({"raw": v["raw"], "normal": v["normal"]}, default=str), capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["stages"] == [["custom-stage", "Synthesise and time (ASAP7)"], ["custom-stage", "Place and time (ASAP7)"]], out
    assert out["own"] == "command" and out["other"] == "rtl newthing", out


STANDING_JS = r"""
const c = require(process.argv[1]);
c.setCatalog(JSON.parse(require("fs").readFileSync(process.argv[2], "utf8")));
const s = c.base(); s.id = "x"; s.statement = "Anything."; s.language = "python";
s.checks.push(c.newCheck(s, "custom")); s.checks[0].params.command = "{python} {home}/check.py {artifact}";
s.stages.push(c.newStage(s, "custom-stage")); s.stages[0].params.command = "{python} {home}/bench.py {artifact}"; s.stages[0].metrics = "t";
s.objectives = [c.newObjective("t", "min")];
const msgs = c.check(s), named = c.namedFiles(s), all = {missing: [], later: []};
const out = {
  clean: c.standing(msgs, {named, files: all, checked: ["ok", "passed"]}),
  fresh: c.standing(msgs, {named, files: all, checked: ["note", "not checked yet"], isNew: true}),
  failed: c.standing(msgs, {named, files: all, checked: ["error", "failed on saved files"]}),
  missing: c.standing(msgs, {named, files: {missing: ["bench.py"], later: []}, checked: ["ok", "passed"]}),
  refused: c.standing(msgs, {named, files: all, checked: ["ok", "passed"], loadError: "flow.critique is a critic"}),
  errors: c.standing(c.check(Object.assign(c.base(), {id: "y"})), {named: [], files: all, checked: ["ok", "passed"]}),
};
process.stdout.write(JSON.stringify(out));
"""


def test_saves_checklist_and_the_rails_line_are_one_standing():
    """The rail's line under Save said "ready to save" while Save listed a failed check or a missing file. Both are
    M.standing now: every ✗ in the checklist is in the line; a document the loader refuses is the first line; Ready to
    run names what stands in the way, not always the check."""
    r = subprocess.run(["node", "-e", STANDING_JS, str(ASSETS / "crafter-model.js"), str(ASSETS / "tools.json")],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    rows = {k: {what: (kind, words) for kind, what, words in v["rows"]} for k, v in out.items()}
    assert out["clean"]["sum"] == "ready to save" and rows["clean"]["Ready to run"] == ("ok", "yes")
    assert out["fresh"]["sum"] == "ready to create" and rows["fresh"]["Ready to run"] == ("note", "after a Check passes")
    assert out["failed"]["sum"] == "check failed" and rows["failed"]["Checked"][0] == "error"
    assert out["missing"]["sum"] == "1 file missing" and rows["missing"]["Ready to run"] == ("note", "after 1 missing file is added")
    assert out["refused"]["rows"][0] == ["error", "Loads", "no: the loader refuses it as saved"], out["refused"]
    assert out["refused"]["sum"].startswith("does not load") and rows["refused"]["Ready to run"] == ("note", "after the document loads")
    n = out["errors"]["sum"].split(" ")[0]
    assert out["errors"]["sum"] == f"{n} to fix" and rows["errors"]["Ready to run"] == ("note", f"after {n} fixes"), out["errors"]
    for k, v in out.items():                                   # a ✗ in the checklist is never "ready"
        assert any(kind == "error" for kind, _w, _x in v["rows"]) == (not v["sum"].startswith("ready")), (k, v)


APP_JS = r"""
const c = require(process.argv[1]);
c.setCatalog(JSON.parse(require("fs").readFileSync(process.argv[2], "utf8")));
const v = JSON.parse(require("fs").readFileSync(0, "utf8"));
const s = c.fromDoc(v.raw, v.normal).state;
const gen = c.whoOptions("generate", s.flow.generate, "claude", c.paramOnly(s));
process.stdout.write(JSON.stringify({search: c.parameterSearch(s), knobs: c.knobNames(s), budget: s.budget, named: c.namedFiles(s),
  golden: c.goldenFiles(s), errors: c.check(s).filter(m => m.level === "error").map(m => m.text), yaml: c.buildYaml(s), kept: s.kept,
  generate: gen.map(o => o.k), on: c.whoOn(gen, s.flow.generate)}));
"""


def _app(name: str) -> dict:
    v = views(REPO / f"flux/applications/{name}/problem.yaml")
    r = subprocess.run(["node", "-e", APP_JS, str(ASSETS / "crafter-model.js"), str(ASSETS / "tools.json")],
                       input=json.dumps({"raw": v["raw"], "normal": v["normal"]}, default=str), capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_a_space_kept_as_written_is_searched_all_the_same():
    """macarray's space (a knob with `from:`) is kept as written; the configurator still read "no search" and refused
    {pipeline} as "neither a setting to search nor one of Flux's own". Its knobs are the search's settings."""
    out = _app("macarray")
    assert "flow.dse.space" in out["kept"] and out["search"] is True, out["kept"]
    assert {"multiplier", "reducer", "pipeline"} <= set(out["knobs"]), out["knobs"]
    assert not [e for e in out["errors"] if "neither a setting" in e], out["errors"]


def test_the_files_named_are_the_loops_own_checks():
    """D1157: what the configurator calls named (and missing) is what `flux task check` names -- not what the loop
    writes as it runs (macarray's `{home}/out/invented`), and a golden model apart (the loop's model writes it first)."""
    mac = _app("macarray")
    assert not [p for p in mac["named"] if p.startswith("out/")], mac["named"]
    nlu = _app("nlu")
    assert nlu["golden"] == ["golden.py"] and "golden.py" in nlu["named"], nlu["named"]


def test_a_budget_kept_as_written_shows_its_values_and_is_not_written():
    """nlu keeps its budget (it sets prototype_attempts...): the read-only knobs showed the defaults (Repairs 12 beside
    `repair_attempts: 20`). The state holds what the document says; the save still leaves the budget to the merge."""
    out = _app("nlu")
    assert "budget" in out["kept"] and out["budget"]["steps"] == "24" and out["budget"]["repair_attempts"] == "20", out["budget"]
    assert "\nbudget:" not in out["yaml"]


def test_design_under_a_parameter_only_search_is_off():
    """D911: a search with no script to make a point a design runs no Design; the Graph drew the box off while its
    panel had "model" chosen. The panel's choice is off, a script or files."""
    s = _app("primes")
    assert s["generate"][:2] == ["model", "agent"], s["generate"]
    js = r"""
const c = require(process.argv[1]);
const s = c.base(); s.flow.dse = "sweep"; s.space = [{knob: "block", choices: "16, 32"}];
const o = c.whoOptions("generate", s.flow.generate, "claude", c.paramOnly(s));
process.stdout.write(JSON.stringify({only: c.paramOnly(s), ks: o.map(x => x.k), on: c.whoOn(o, s.flow.generate), keeps: o[0].value()}));
"""
    r = subprocess.run(["node", "-e", js, str(ASSETS / "crafter-model.js")], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout) == {"only": True, "ks": ["off", "command", "catalog"], "on": "off", "keeps": "model"}


def test_the_starter_document_leaves_statement_and_contract_to_fill_in():
    """New loop › Create without files writes the baseline (D825): its statement and contract were instruction text,
    read back as real values. They are empty, the configurator's placeholders say what goes there; the loader says
    what is to fill in first (a baseline to fill in does not load, D825)."""
    from flux_cli.commands import baseline_files

    text = dict(baseline_files("blank"))["problem.yaml"]
    doc = yaml.safe_load(text)
    assert doc["statement"] == "" and doc["contract"] == ""
    # the starter as it came is said calmly -- what is left, not the loader's refusal -- told by its content alone
    js = r"""
const c = require(process.argv[1]);
const doc = JSON.parse(require("fs").readFileSync(0, "utf8"));
const s = c.fromDoc(doc, doc).state, msgs = c.check(s);
const calm = c.standing(msgs, {named: [], files: {missing: [], later: []}, checked: ["note", "not checked yet"], calm: true, starter: true});
process.stdout.write(JSON.stringify({starter: c.unfilled(doc), said: c.unfilled(Object.assign({}, doc, {statement: "x"})),
  checked: c.unfilled(Object.assign({}, doc, {flow: {test: "{python} {home}/check.py {artifact}"}})), calm}));
"""
    r = subprocess.run(["node", "-e", js, str(ASSETS / "crafter-model.js")], input=json.dumps(doc), capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["starter"] is True and out["said"] is False and out["checked"] is False, out
    assert out["calm"]["sum"] == "not filled in yet" and out["calm"]["rows"][0][:2] == ["todo", "Starter"], out["calm"]
    assert not [row for row in out["calm"]["rows"] if row[0] == "error"], out["calm"]


def test_the_files_a_parent_names_are_its_sub_loops(tmp_path, monkeypatch):
    """The configurator said "Named but missing: golden.py" for nlu, whose `{home}/golden.py` is each operator's
    (every ops/<op>/golden.py there); macarray's out/invented and a golden model the loop writes first were missing
    too. /document/named is the loop's own check (flux_loop.author.files_named): the pages say what it says."""
    from fastapi.testclient import TestClient

    from flux_web import create_app
    from flux_web.store import Store

    H = {"X-Flux": "1"}
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    Store(tmp_path / "data").add_user("bob", "another long secret")
    c = TestClient(create_app(tmp_path / "data", sandbox=False))
    assert c.post("/api/login", json={"name": "bob", "password": "another long secret"}, headers=H).status_code == 200
    for app in ("nlu", "macarray", "prefetcher"):
        src = REPO / f"flux/applications/{app}"
        files = [("files", (p.relative_to(src).as_posix(), p.read_bytes())) for p in sorted(src.rglob("*"))
                 if p.is_file() and not ({"out", "workbench", "traces", ".nix-bin"} & set(p.relative_to(src).parts)) and not p.name.startswith("demo-")]
        assert c.post("/api/apps", data={"name": app}, files=files, headers=H).status_code == 200, app
    text = lambda app: (REPO / f"flux/applications/{app}/problem.yaml").read_text()
    nlu = c.post("/api/apps/nlu/document/named", json={"text": text("nlu")}, headers=H).json()
    assert "golden.py" in nlu["named"] and nlu["missing"] == [] and nlu["later"] == [], nlu
    mac = c.post("/api/apps/macarray/document/named", json={"text": text("macarray")}, headers=H).json()
    assert not [p for p in mac["named"] + mac["missing"] if p.startswith("out/")], mac
    pre = c.post("/api/apps/prefetcher/document/named", json={"text": text("prefetcher")}, headers=H).json()
    assert pre["missing"] == ["traces"], "the traces are not in git: really missing, as the check says"
    gone = c.post("/api/apps/nlu/document/named", json={"text": "flow:\n  test: \"fluxpreset golden {artifact} --golden {home}/other.py\"\n"}, headers=H).json()
    assert gone["missing"] == [] and gone["later"] == ["other.py"], "a golden model the folder lacks: the loop's model writes it first (D604)"
    assert c.post("/api/apps/nlu/document/named", json={"text": "- not a mapping"}, headers=H).json() == {"named": [], "missing": [], "later": []}


WORDS_JS = r"""
const c = require(process.argv[1]);
const s = c.base(); s.flow.generate = "agent:codex"; s.flow.plan = "agent:claude";
const opts = c.whoOptions("generate", s.flow.generate, "claude"), plan = c.whoOptions("plan", "off", "opencode");
const two = c.base(); two.flow.validate = "agent:claude"; two.flow.generate = "agent:codex";
const one = c.base(); one.flow.generate = "agent:codex";
const errs = st => c.check(st).map(m => m.text);
process.stdout.write(JSON.stringify({
  agent: opts.find(o => o.k === "agent"), works: c.whoWorks("generate", s.flow.generate, "claude"), off: plan.find(o => o.k === "agent"),
  feedback: c.whoOptions("feedback", "human", "claude").map(o => o.label), words: c.WORDS, yaml: c.buildYaml(c.base()),
  two: errs(two).filter(t => /must be installed/.test(t)), one: errs(one).filter(t => /must be installed/.test(t)),
  unnamed: errs(c.base()).filter(t => /name/i.test(t)), all: errs(Object.assign(c.base(), {flow: Object.assign(c.base().flow, {dse: "explore"})})),
}));
"""


def _words() -> dict:
    r = subprocess.run(["node", "-e", WORDS_JS, str(ASSETS / "crafter-model.js")], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_the_agent_is_one_choice_and_the_menu_says_which():
    """configurator-10: the kinds' last choice was the agent's own name ("codex") with a menu beside it saying "codex"
    again. The choice says "agent"; the drawing's word (and a kept flow's tag) is its `name`."""
    out = _words()
    assert out["agent"]["label"] == "agent" and out["agent"]["name"] == "codex", out["agent"]
    assert out["works"]["name"] == "codex" and out["off"]["label"] == "agent" and out["off"]["name"] == "opencode"
    assert out["feedback"] == ["on", "off"], "Notes' switch is on/off like the others, not its own title again (configurator-28)"


def test_the_prototype_hint_says_the_choice_shown():
    """configurator-6: under "Prototype first" the hint read "default: with a golden model" whatever was chosen. Every
    choice has its own words."""
    g = _words()["words"]["graph"]
    assert "prototypeHint" not in g
    assert {v for v, _ in g["prototypes"]} == set(g["prototypeHints"]), g["prototypeHints"]
    assert len(set(g["prototypeHints"].values())) == len(g["prototypeHints"])


def test_one_name_one_placeholder_and_plain_checklist_words():
    """configurator-18, -20: "Loop name"/my_loop on New loop, "Name"/my_design in a loop's settings, my_problem in the
    preview and "Give the problem a name" in Save. One label and placeholder (the model's), the preview the same, the
    checklist says "Name the loop"; agents listed as words, no "DSE", no "--"."""
    out = _words()
    p = out["words"]["prompt"]
    assert p["name"] == "Name" and p["namePlaceholder"] == "my_loop"
    assert out["yaml"].startswith("# my_loop/problem.yaml"), out["yaml"][:60]
    assert out["unnamed"] == ["Name the loop (letters, digits, - and _)."], out["unnamed"]
    assert out["two"] == ["Agents claude and codex must be installed where the loop runs."], out["two"]
    assert out["one"] == ["Agent codex must be installed where the loop runs."], out["one"]
    assert not [t for t in out["all"] if "DSE" in t or " -- " in t], out["all"]
    web = REPO / "flux/interfaces/web/src/flux_web/static/configure"
    for f in (web / "Configurator.js", web / "Configure.js", web / "PromptStep.js", ASSETS / "crafter-app.js"):
        text = f.read_text()
        assert "my_problem" not in text and "my_design" not in text and "Loop name" not in text and "your loop" not in text, f.name


HL_JS = r"""
const fs = require('fs'), vm = require('vm');
(async () => {
  const mod = new vm.SourceTextModule(fs.readFileSync(process.argv[1], 'utf8'), { context: vm.createContext({}) });
  await mod.link(() => { throw new Error('the highlighter imports nothing'); }); await mod.evaluate();
  const y = fs.readFileSync(0, 'utf8');
  process.stdout.write(JSON.stringify({ lines: mod.namespace.tokenLines(y, 'yaml'), whole: mod.namespace.tokens(y, 'yaml').map(t => t[1]).join('') === y }));
})().catch(e => { console.error(e); process.exitCode = 1; });
"""


def test_a_block_scalars_lines_are_prose_to_the_highlighter():
    """configurator-14: in `statement: >-` the words "on", "no", "false" were booleans, "SystemVerilog:" a key. A block
    scalar's lines are plain text; the keys and values around it are still coloured, and the text is kept whole."""
    doc = ("statement: >-\n  A fast on no false 32-bit SystemVerilog: thing\n\n  have: x\ncontract: |\n  - on\n"
           "flag: on\nflow:\n  test:\n    - run: >\n        prose: on\n    - b: 3\n")
    r = subprocess.run(["node", "--experimental-vm-modules", "-e", HL_JS, str(REPO / "flux/interfaces/web/src/flux_web/static/highlight.js")],
                       input=doc, capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    lines = out["lines"]
    plain = lambda i: all(c == "" for c, _ in lines[i])
    assert plain(1) and plain(3) and plain(5) and plain(10), lines
    assert ["kw", "on"] in lines[6] and lines[0][0] == ["key", "statement"] and lines[4][0] == ["key", "contract"], lines
    assert ["num", "3"] in lines[11], "after the block, a value is coloured again"
    assert out["whole"]


def test_the_loaders_refusal_names_the_loops_files_and_no_decisions(tmp_path):
    """configurator-22: the banner printed the server's path (/tmp/.../users/bob/apps/x/problem.yaml) and "(D1040; ...)".
    The web says the file inside the loop and drops decision numbers; the refusal for an RTL preset in a Python loop
    reads as a sentence ("Check them with for a Python design" was garbled)."""
    from flux_web.configure import loader_says, views

    loop = tmp_path / "users/bob/apps/x"
    loop.mkdir(parents=True)
    said = loader_says(f"{loop}/problem.yaml: flow.critique is a critic (D1040; `flux migrate` moves it), see (D12, D13).", loop)
    assert said == "problem.yaml: flow.critique is a critic (`flux migrate` moves it), see.", said
    (loop / "problem.yaml").write_text("statement: x\nlanguage: python\nflow:\n  test: fluxpreset golden {artifact} --golden {home}/golden.py\n")
    err = views(loop / "problem.yaml")["error"]
    assert str(tmp_path) not in err and "Check a Python design with `fluxpreset cases" in err and "Check them with" not in err, err


def test_a_checks_critic_stays_at_the_gates_level(tmp_path):
    """Admin › Loops showed "flow.test.correctness: keys ['critic'] are not a check's": the capture's invalid_doc (a
    critic written inside a check on purpose, with an old `flow.critique`), not the configurator. Read back and written
    again, the critic on passing designs stays `flow.test.critic`, the check's own map untouched; set from the Graph's
    Check box it lands there too."""
    src = tmp_path / "src" / "crit_loop"
    src.mkdir(parents=True)
    (src / "check.py").write_text("print('0 failing')\n")
    (src / "problem.yaml").write_text("statement: x\nlanguage: python\nflow:\n  test:\n    build: \"{python} -m py_compile {artifact}\"\n"
                                      "    correctness: {run: \"{python} {home}/check.py {artifact}\", timeout_s: 60}\n    critic: {by: opencode}\n")
    before, after, _ = _round(src / "problem.yaml", tmp_path / "out")
    assert after["flow"] == before["flow"], (before["flow"], after["flow"])
    v = views(src / "problem.yaml")
    js = r"""
const c = require(process.argv[1]); c.setCatalog(JSON.parse(require("fs").readFileSync(process.argv[2], "utf8")));
const v = JSON.parse(require("fs").readFileSync(0, "utf8")), g = c.fromDoc(v.raw, v.normal);
c.setCritic(g.state, "test", "agent:claude"); process.stdout.write(c.buildYaml(g.state));
"""
    r = subprocess.run(["node", "-e", js, str(ASSETS / "crafter-model.js"), str(ASSETS / "tools.json")],
                       input=json.dumps({"raw": v["raw"], "normal": v["normal"]}, default=str), capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    test = yaml.safe_load(r.stdout)["flow"]["test"]
    assert test["critic"] == "claude" and "critic" not in test["correctness"], test
