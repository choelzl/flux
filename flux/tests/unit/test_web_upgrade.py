"""D775 on the web: a loop whose document is of the earlier layout is said so, refused a start
with what to do, and upgraded by its owner -- or every such loop at once by an admin."""

from __future__ import annotations

import yaml
from fastapi.testclient import TestClient

from flux_web import create_app
from flux_web.store import Store

H = {"X-Flux": "1"}
OLD = (b"id: x\nstatement: s\nlanguage: python\ngate: {test: ['true']}\n"
       b"stages: [{name: b, command: 'echo t=1', metrics: [t]}]\nobjectives: [{metric: t, direction: minimize}]\n")


def _client(app, name, pw):
    c = TestClient(app)
    assert c.post("/api/login", json={"name": name, "password": pw}, headers=H).status_code == 200
    return c


def test_an_old_document_is_said_refused_and_upgraded(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    store = Store(tmp_path / "data")
    store.add_user("ada", "correct horse battery", "admin")
    store.add_user("bob", "another long secret")
    app = create_app(tmp_path / "data", sandbox=False)
    ada, bob = _client(app, "ada", "correct horse battery"), _client(app, "bob", "another long secret")
    for n in ("x", "y"):
        assert bob.post("/api/apps", data={"name": n}, files=[("files", ("x.problem.yaml", OLD))], headers=H).status_code == 200
    assert bob.get("/api/apps/x").json()["old_layout"] is True
    r = bob.post("/api/apps/x/start", json={"passes": 1}, headers=H)
    assert r.status_code == 409 and "earlier layout" in r.json()["detail"] and "Upgrade the document" in r.json()["detail"]
    r = bob.post("/api/apps/x/document/upgrade", headers=H)
    assert r.status_code == 200 and r.json()["status"] == "upgraded" and ".orig" in r.json()["ok"]
    assert bob.get("/api/apps/x").json()["old_layout"] is False
    doc = yaml.safe_load(bob.get("/api/apps/x/file?path=x.problem.yaml").text)
    assert doc["flow"]["measure"] == {"b": {"command": "echo t=1", "metrics": ["t"]}} and "gate" not in doc
    assert bob.post("/api/apps/x/document/upgrade", headers=H).json()["status"] == "current"
    assert bob.get("/api/admin/documents").status_code == 403
    got = ada.get("/api/admin/documents").json()
    assert got["old"] == [{"user": "bob", "app": "y", "document": "x.problem.yaml"}] and got["all"] == 2
    done = ada.post("/api/admin/documents/upgrade", headers=H).json()
    assert done["upgraded"] == 1 and done["failed"] == 0 and ada.get("/api/admin/documents").json()["old"] == []
