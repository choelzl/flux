"""D824: a loop is cloned into a new loop of one's own -- its problem (documents, the files they name,
library/, sub-loops), never its runs' (out/, runs/); its workbench when asked; from any loop one can
see."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from flux_web import create_app
from flux_web.store import Store

H = {"X-Flux": "1"}


def test_a_loop_is_cloned_with_its_problem_and_none_of_its_runs(tmp_path):
    store = Store(tmp_path / "data")
    store.add_user("bob", "another long secret")
    store.add_user("cy", "cy has a long secret")
    app = create_app(tmp_path / "data", sandbox=False)
    bob, cy = TestClient(app), TestClient(app)
    bob.post("/api/login", json={"name": "bob", "password": "another long secret"}, headers=H)
    cy.post("/api/login", json={"name": "cy", "password": "cy has a long secret"}, headers=H)
    files = [("files", ("problem.yaml", b"statement: s\n")), ("files", ("check.py", b"print(0)\n")),
             ("files", ("library/paper.md", b"a paper\n")), ("files", ("ops/a/problem.yaml", b"statement: a\n"))]
    assert bob.post("/api/apps", data={"name": "x"}, files=files, headers=H).status_code == 200
    src = tmp_path / "data/users/bob/apps/x"
    for rel, text in (("out/x.db", "record"), ("runs/loop.log", "log"), ("workbench/notes/n.md", "a note"), ("problem.yaml.orig", "old")):
        (src / rel).parent.mkdir(parents=True, exist_ok=True)
        (src / rel).write_text(text)
    got = bob.post("/api/apps/x/clone", json={"to": "y"}, headers=H).json()
    assert got["name"] == "y" and got["cloned_from"] == "bob/x" and got["document"] == "problem.yaml"
    y = tmp_path / "data/users/bob/apps/y"
    assert (y / "check.py").is_file() and (y / "library/paper.md").is_file() and (y / "ops/a/problem.yaml").is_file()
    assert not (y / "out").exists() and not (y / "runs").exists() and not (y / "workbench").exists()
    assert not (y / "problem.yaml.orig").exists()
    assert json.loads((y / ".flux-app.json").read_text())["id"] == "y"
    assert bob.post("/api/apps/x/clone", json={"to": "y"}, headers=H).status_code == 400, "a name taken"
    assert bob.post("/api/apps/x/clone", json={"to": "z", "workbench": True}, headers=H).status_code == 200
    assert (tmp_path / "data/users/bob/apps/z/workbench/notes/n.md").read_text() == "a note"
    assert cy.post("/api/apps/x/clone", params={"owner": "bob"}, json={"to": "mine"}, headers=H).status_code == 403
    bob.put("/api/apps/x/shares", json={"user": "cy", "perm": "watch"}, headers=H)
    assert cy.post("/api/apps/x/clone", params={"owner": "bob"}, json={"to": "mine"}, headers=H).status_code == 200, \
        "a watcher clones into their own"
    assert (tmp_path / "data/users/cy/apps/mine/check.py").is_file()
    assert any(a["name"] == "mine" for a in cy.get("/api/apps").json())


def test_an_empty_loop_is_the_baseline_to_fill_in(tmp_path):
    """D825: New loop › Empty loop -- the baseline folder, opened in the configurator."""
    store = Store(tmp_path / "data")
    store.add_user("bob", "another long secret")
    app = create_app(tmp_path / "data", sandbox=False)
    bob = TestClient(app)
    bob.post("/api/login", json={"name": "bob", "password": "another long secret"}, headers=H)
    got = bob.post("/api/apps/new-empty", json={"name": "blank"}, headers=H).json()
    assert got["name"] == "blank" and got["document"] == "problem.yaml"
    d = tmp_path / "data/users/bob/apps/blank"
    assert (d / "README.md").is_file() and (d / "library").is_dir() and "statement:" in (d / "problem.yaml").read_text()
    v = bob.get("/api/apps/blank/document").json()
    assert v["raw"]["statement"] and v["error"], "the configurator reads it, and says what is missing"
    assert bob.post("/api/apps/new-empty", json={"name": "blank"}, headers=H).status_code == 400
