"""Loop names agree across folder loading, web operations, CLI creation and the crafter."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from flux_cli.commands import _write_folder, example_files
from flux_loop import load_task
from flux_web.workspace import WorkspaceError, check_name
from test_web import H, _client, server  # noqa: F401 -- shared fixture

VALID = ["Loop-Az09_test", "9Loop-Az_test", "_Loop-Az09", "-Loop_Az09", "_", "-", "a" * 60]
INVALID = ["", ".", "..", "loop.name", "bad/name", "bad\\name", "bad name", "bad\nname", "name\n", "café", "a" * 61]
DOCUMENT = "statement: Valid name\nlanguage: text\nflow: {test: 'true'}\n"


@pytest.mark.parametrize("name", VALID)
def test_allowed_names_can_be_created_loaded_cloned_and_renamed(server, monkeypatch, name):  # noqa: F811
    app, tmp = server
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp / "cache"))
    assert check_name(name) == name
    bob = _client(app, "bob", "another long secret")
    response = bob.post("/api/apps/from-text", json={"name": name, "text": DOCUMENT}, headers=H)
    assert response.status_code == 200, response.text
    root = app.state.store.data / "users/bob/apps"
    assert load_task(root / name / "problem.yaml").id == name
    clone = "-Clone_09"
    response = bob.post(f"/api/apps/{name}/clone", json={"to": clone}, headers=H)
    assert response.status_code == 200, response.text
    assert load_task(root / clone / "problem.yaml").id == clone
    renamed = "_Renamed-Az09"
    response = bob.post(f"/api/apps/{name}/rename", json={"to": renamed}, headers=H)
    assert response.status_code == 200, response.text
    assert load_task(root / renamed / "problem.yaml").id == renamed
    cli = _write_folder(name, str(tmp / "cli"), [("problem.yaml", DOCUMENT)], "new")
    assert cli is not None and load_task(cli / "problem.yaml").id == name


@pytest.mark.parametrize("name", INVALID)
def test_web_names_still_reject_other_characters_traversal_and_overlength(name):
    with pytest.raises(WorkspaceError):
        check_name(name)


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")
def test_shared_crafter_accepts_the_same_name_characters():
    crafter = Path(__file__).resolve().parents[3] / "website/docs/assets/crafter.js"
    script = """const c = require(process.argv[1]), cases = JSON.parse(process.argv[2]);
      console.log(JSON.stringify({valid: cases.map(id => {const s = c.base(); s.id = id;
        return !c.check(s).some(m => m.field === 'id' && m.level === 'error');}),
        hyphenYaml: c.buildYaml(Object.assign(c.base(), {id: '-Loop'}))}));"""
    # Leading/trailing whitespace is trimmed by the form; a newline inside a name is rejected.
    invalid = [n for n in INVALID if n != "name\n"]
    result = subprocess.run(["node", "-e", script, str(crafter), json.dumps(VALID + invalid)],
                            capture_output=True, text=True, check=True)
    got = json.loads(result.stdout)
    assert got["valid"] == [True] * len(VALID) + [False] * len(invalid)
    assert "flux task check ./-Loop" in got["hyphenYaml"]


@pytest.mark.parametrize("name", ["9Loop-test", "-Loop_09", "_Loop-09"])
@pytest.mark.parametrize("kind", ["rtl", "rtl-sweep"])
def test_rtl_examples_use_valid_module_identifiers_separately_from_loop_names(tmp_path, name, kind):
    folder = _write_folder(name, str(tmp_path), example_files(name, kind), "example")
    task = load_task(folder / "problem.yaml")
    module = re.search(r"module (?:named exactly )?`([^`]+)`", task.statement)[1]
    assert re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", module)
    assert task.id == name
    if kind == "rtl-sweep":
        artifact = folder / "candidate.sv"
        subprocess.run([sys.executable, str(folder / "gen.py"), str(artifact), "behavioral", "2"], check=True)
        assert artifact.read_text().startswith(f"module {module}(")
