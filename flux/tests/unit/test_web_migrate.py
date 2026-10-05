"""D811: Admin › Documents -- every loop's documents of an earlier form listed with what would
change; a start of one said to need it; the migration, by an admin only, written when it loads,
followed by the loop and its runs."""

from __future__ import annotations

import json
import time

import yaml
from fastapi.testclient import TestClient

from flux_web import create_app
from flux_web.store import Store
from flux_web.workspace import Workspace

H = {"X-Flux": "1"}
OLD = {"id": "adder", "statement": "add", "language": "python",
       "gate": {"test": ["python", "check.py", "{artifact}"], "count_re": r"(\d+) failing"},
       "stages": [{"name": "bench", "command": "python bench.py {artifact}", "metrics": ["t"]}],
       "objectives": [{"metric": "t", "direction": "minimize"}], "flow": {"critique": "none"}}


def test_an_admin_migrates_a_loops_document_and_the_loop_follows(tmp_path):
    store = Store(tmp_path / "data")
    store.add_user("ada", "correct horse battery", "admin")
    store.add_user("bob", "another long secret")
    w = Workspace(store.data, "bob")
    d = w.create_empty("sum")
    (d / "check.py").write_text("print('0 failing')\n")
    (d / "bench.py").write_text("print('t=1')\n")
    (d / "adder.problem.yaml").write_text(yaml.safe_dump(OLD, sort_keys=False))
    w.set_meta("sum", document="adder.problem.yaml")
    w.create_empty("never")                                     # a loop never run, no document: listed as nothing to do
    (d / "out").mkdir()
    (d / "out" / "adder.db").write_text("")
    bob_u = store.user(name="bob")
    rid = store.add_run(bob_u, "sum", str(d / "out" / "adder.db"), str(d / "runs" / "loop.log"),
                        ["flux", "task", "run", str(d / "adder.problem.yaml")], {})
    store.set_run(rid, ended=time.time(), rc=0)
    app = create_app(tmp_path / "data", sandbox=False)
    ada, bob = TestClient(app), TestClient(app)
    ada.post("/api/login", json={"name": "ada", "password": "correct horse battery"}, headers=H)
    bob.post("/api/login", json={"name": "bob", "password": "another long secret"}, headers=H)
    assert bob.get("/api/admin/documents").status_code == 403
    assert bob.post("/api/admin/documents/migrate", json={}, headers=H).status_code == 403
    listed = ada.get("/api/admin/documents").json()
    (loop,) = listed["loops"]
    (doc,) = loop["documents"]
    assert (loop["user"], loop["app"], doc["file"], doc["to"], doc["status"]) == ("bob", "sum", "adder.problem.yaml", "problem.yaml", "would migrate")
    assert any(s.startswith("D775") for s in doc["said"]) and "flow:" in doc["text"]
    r = bob.post("/api/apps/sum/start", json={"passes": 1}, headers=H)
    assert r.status_code == 409 and "Admin › Documents" in r.json()["detail"], r.text
    got = ada.post("/api/admin/documents/migrate", json={"user": "bob", "app": "sum"}, headers=H).json()
    assert got["migrated"] == 1, got
    assert w.meta("sum")["document"] == "problem.yaml" and (d / "problem.yaml").is_file() and (d / "adder.problem.yaml.orig").is_file()
    assert (d / "out" / "sum.db").is_file(), "the record named after the folder"
    run = store.runs(bob_u, "sum")[0]
    assert run["db"] == str(d / "out" / "sum.db") and str(d / "problem.yaml") in json.loads(run["argv"]), "the runs follow"
    assert ada.get("/api/admin/documents").json()["loops"] == [], "nothing left"
    assert ada.post("/api/admin/documents/migrate", json={"user": "bob", "app": "sum"}, headers=H).status_code == 404
