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
    assert out["flow"]["generate"] == "agent:opencode" and out["flow"]["critique"] == "agent:custom"
    assert not any("flow" in k for k in out["kept"]), out["kept"]
    again = home / "again.problem.yaml"
    again.write_text(merged(out["yaml"], v["raw"], out["kept"]))
    flow = yaml.safe_load(again.read_text())["flow"]
    assert flow["generate"] == raw["flow"]["generate"] and flow["critique"] == raw["flow"]["critique"]
    assert load_task(str(again)).to_dict()["flow"] == v["normal"]["flow"]
