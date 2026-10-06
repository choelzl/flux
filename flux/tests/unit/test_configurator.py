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
               *REPO.glob("flux/interfaces/cli/src/flux_cli/examples/*/problem.yaml"),
               REPO / "flux/core/loop/examples/digits/problem.json"])
COMPARED = ("id", "language", "gate", "stages", "objectives", "flow", "budget", "space", "parts", "workload")

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
    r = subprocess.run(["node", "-e", JS, str(ASSETS / "crafter.js"), str(ASSETS / "tools.json")],
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
    path = REPO / "flux/interfaces/cli/src/flux_cli/examples" / kind / "problem.yaml"
    _before, _after, kept = _round(path, tmp_path)
    assert kept == [], kept


def test_an_agent_with_its_own_settings_is_an_agent_kept_as_written(tmp_path):
    """D728: `generate: {by: opencode, bin, args}` and a custom-command critic were not
    the configurator's choices, so the whole flow fell back to its defaults ("a model") and was
    kept aside. Now each is its agent -- drawn as one -- and written back as it was."""
    src = REPO / "flux/interfaces/cli/src/flux_cli/examples/python"
    home = tmp_path / "p"
    shutil.copytree(src, home)
    doc = home / "problem.yaml"
    raw = yaml.safe_load(doc.read_text())
    raw["flow"] = {**(raw.get("flow") or {}),
                   "generate": {"by": "opencode", "bin": "oc-mod", "args": ["--agent", "flux"], "timeout_s": 900},
                   "critique": {"by": {"command": "my-critic {prompt_file}", "output": "text"}}}
    doc.write_text(yaml.safe_dump(raw, sort_keys=False))
    v = views(doc)
    assert v["normal"] is not None, v["error"]
    js = JS.replace("process.stdout.write(JSON.stringify({yaml:", "process.stdout.write(JSON.stringify({flow: got.state.flow, half: ['generate', 'critique'].map(b => c.halfOf ? c.halfOf(got.state, b) : null), yaml:")
    r = subprocess.run(["node", "-e", js, str(ASSETS / "crafter.js"), str(ASSETS / "tools.json")],
                       input=json.dumps({"raw": v["raw"], "normal": v["normal"]}, default=str), capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["flow"]["generate"] == "agent:opencode" and out["flow"]["critique"] == "agent:*"
    assert not any("flow" in k for k in out["kept"]), out["kept"]
    again = home / "again.problem.yaml"
    again.write_text(merged(out["yaml"], v["raw"], out["kept"]))
    flow = yaml.safe_load(again.read_text())["flow"]
    assert flow["generate"] == raw["flow"]["generate"] and flow["critique"] == raw["flow"]["critique"]
    assert load_task(str(again)).to_dict()["flow"] == v["normal"]["flow"]


# D910: an untouched read-back keeps a document's meaning -- typed choices, a comma inside one,
# an inline Workload IR, a `needs` of a catalog stage's own, a script path with a space
_BASE = {"statement": "Make valid source with the smallest time.", "language": "python",
         "flow": {"test": "{python} {home}/check.py {artifact}",
                  "measure": {"bench": {"command": "{python} {home}/bench.py {artifact}", "metrics": ["time_ms"]}}},
         "objectives": [{"metric": "time_ms", "direction": "minimize"}]}
_WORKLOAD = {"id": "tiny", "ops": [{"id": "mm", "kind": "einsum", "expr": "b c, c k -> b k", "bounds": {"b": 2, "c": 8, "k": 16}}]}


def _variant(name: str) -> dict:
    d = json.loads(json.dumps(_BASE))
    if name == "scalar_choices":
        d["flow"]["orchestrate"] = {"policy": "sweep", "space": {"mode": ["01", "true", "fast,wide", 2, True, 0.5, 1e-07]}}
        d["flow"]["generate"] = {"command": "{python} {home}/gen.py {artifact} {mode}"}
    elif name == "inline_workload":
        d.update(language="yaml", workload=_WORKLOAD, objectives=[{"metric": "latency_cycles", "direction": "minimize"}])
        d["flow"]["measure"] = {"cost": {"evaluator": "zigzag", "metrics": ["latency_cycles", "energy_pj"]}}
    elif name == "needs_override":
        d["flow"]["measure"]["bench"]["needs"] = ["a-required-tool"]
    elif name == "spaced_script":
        d["flow"]["measure"]["bench"]["command"] = ["{python}", "{home}/my bench.py", "{artifact}"]
    elif name == "params_placeholder":
        d["params"] = {"n": 1}
        d["flow"]["test"] = "{python} {home}/check.py {artifact} {params}"
    return d


@pytest.mark.parametrize("name", ["scalar_choices", "inline_workload", "needs_override", "spaced_script", "params_placeholder"])
def test_an_untouched_edit_keeps_the_meaning(name, tmp_path):
    src = tmp_path / "src" / name
    src.mkdir(parents=True)
    for f in ("check.py", "bench.py", "my bench.py", "gen.py"):
        (src / f).write_text("print('time_ms=1')\n")
    (src / "problem.yaml").write_text(yaml.safe_dump(_variant(name), sort_keys=False))
    (tmp_path / "w").mkdir()
    before, after, kept = _round(src / "problem.yaml", tmp_path / "w")
    for key in COMPARED:
        assert after.get(key) == before.get(key), (key, kept)
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
    r = subprocess.run(["node", "-e", SEARCH_JS, str(ASSETS / "crafter.js"), str(ASSETS / "tools.json")],
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
                        str(ASSETS / "crafter.js")], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout) == list(BUILTIN_SUBS)


def test_the_wizard_has_six_steps_and_old_steps_land_on_theirs():
    """D941: Prompt, Check & Measure, Objective, Graph, Extra, Save -- every message names one of them,
    and the seven steps' names and numbers (D826) land on the step that holds their fields now."""
    js = ("const c = require(process.argv[1]); process.stdout.write(JSON.stringify({ids: c.STEP_IDS, of: c.STEP_OF,"
          " old: [0, 1, 2, 3, 4, 5, 6].map(i => c.stepIndex(i, true)), steps: c.check(c.base()).map(m => m.step)}))")
    r = subprocess.run(["node", "-e", js, str(ASSETS / "crafter.js")], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["ids"] == ["prompt", "measure", "objective", "graph", "extra", "save"]
    assert out["old"] == [0, 1, 1, 2, 3, 4, 5]
    assert [out["of"][k] for k in ("problem", "checks", "measurements", "objectives", "flow", "more", "review")] == [0, 1, 1, 2, 3, 4, 5]
    assert set(out["steps"]) <= set(range(5)) and {0, 1, 2} <= set(out["steps"])


def test_the_crafters_docs_tokens_are_the_apps():
    """D941: on the docs page the crafter wears the web app's colours -- crafter.css's token set for
    Material's light and dark schemes is flux.css's light and dark :root, value for value."""
    import re

    def tokens(block: str) -> dict[str, str]:
        return {k: v.strip() for k, v in re.findall(r"(--[a-z-]+):\s*([^;]+);", block)}

    flux = (REPO / "flux/interfaces/web/src/flux_web/static/flux.css").read_text()
    crafter = (ASSETS / "crafter.css").read_text()
    app_light = tokens(re.search(r":root \{(.*?)\}", flux, re.S).group(1))
    app_dark = tokens(re.search(r':root\[data-theme="dark"\] \{(.*?)\}', flux, re.S).group(1))
    docs_light = tokens(re.search(r"\[data-md-color-scheme\] \.flux-crafter \{(.*?)\}", crafter, re.S).group(1))
    docs_dark = tokens(re.search(r'\[data-md-color-scheme="slate"\] \.flux-crafter \{(.*?)\}', crafter, re.S).group(1))
    assert {"--ink", "--muted", "--line", "--panel", "--bg", "--soft", "--accent", "--accent-ink", "--ok", "--bad", "--warn"} <= set(docs_light) & set(docs_dark)
    for app, docs in ((app_light, docs_light), (app_dark, docs_dark)):
        assert {k: v for k, v in docs.items() if k in app} == {k: app[k] for k in docs if k in app}


def test_an_added_agent_called_custom_is_one_choice_however_often_the_agents_are_set():
    """D946: the configurator's marker for an agent with its own settings was "agent:custom", spared
    when the choices were rebuilt -- an agent added as `custom` came back once more on every visit."""
    js = r"""
const c = require(process.argv[1]);
for (let i = 0; i < 5; i++) c.setAgents([{name: "custom", label: "Custom"}, {name: "nga", label: "NGA"}]);
process.stdout.write(JSON.stringify(Object.fromEntries(Object.entries(c.BOXES).map(([b, x]) =>
  [b, (x.choices || []).filter(o => String(o.value).startsWith("agent:")).map(o => o.value)]))));
"""
    r = subprocess.run(["node", "-e", js, str(ASSETS / "crafter.js")], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    agents = {b: v for b, v in json.loads(r.stdout).items() if v}
    assert agents and all(v == ["agent:opencode", "agent:claude", "agent:codex", "agent:custom", "agent:nga"] for v in agents.values()), agents
