"""D701: a loop shared with another user, to watch (its runs and outputs) or to edit (also change,
start and stop it); owners and admins share it; a start by an editor is the owner's loop."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from flux_web import create_app
from flux_web.store import Store

H = {"X-Flux": "1"}


@pytest.fixture()
def server(tmp_path):
    store = Store(tmp_path / "data")
    for n in ("ada", "bob", "cy", "dee"):
        store.add_user(n, f"{n} has a long secret", "admin" if n == "ada" else "user")
    return create_app(tmp_path / "data", sandbox=False), tmp_path


def _c(app, n):
    c = TestClient(app)
    assert c.post("/api/login", json={"name": n, "password": f"{n} has a long secret"}, headers=H).status_code == 200
    return c


def test_watch_sees_edit_changes_and_shared_users_cannot_share(server, monkeypatch):
    app, tmp = server
    bob, cy, dee, ada = _c(app, "bob"), _c(app, "cy"), _c(app, "dee"), _c(app, "ada")
    files = [("files", ("x.problem.yaml", b"statement: s\n")), ("files", ("check.py", b"print(1)\n"))]
    assert bob.post("/api/apps", data={"name": "x"}, files=files, headers=H).status_code == 200
    O = {"owner": "bob"}                                            # noqa: N806
    assert cy.get("/api/apps/x", params=O).status_code == 403, "not shared: not seen"
    assert bob.put("/api/apps/x/shares", json={"user": "cy", "perm": "watch"}, headers=H).status_code == 200
    assert bob.put("/api/apps/x/shares", json={"user": "dee", "perm": "edit"}, headers=H).status_code == 200
    assert bob.put("/api/apps/x/shares", json={"user": "cy", "perm": "admin"}, headers=H).status_code == 400
    assert bob.put("/api/apps/x/shares", json={"user": "bob", "perm": "edit"}, headers=H).status_code == 400
    # watch: everything to see, nothing to change
    info = cy.get("/api/apps/x", params=O).json()
    assert info["perm"] == "watch" and info["owner"] == "bob"
    for path in ("/api/apps/x/state", "/api/apps/x/results", "/api/apps/x/files", "/api/apps/x/notes", "/api/apps/x/env", "/api/apps/x/shares"):
        assert cy.get(path, params=O).status_code == 200, path
    assert cy.get("/api/apps/x/file", params={**O, "path": "check.py"}).text == "print(1)\n"
    assert cy.put("/api/apps/x/file", params={**O, "path": "check.py"}, json={"text": "x"}, headers=H).status_code == 403
    assert cy.post("/api/apps/x/start", params=O, json={"passes": 1}, headers=H).status_code == 403
    assert cy.post("/api/apps/x/stop", params=O, json={"now": True}, headers=H).status_code == 403
    assert cy.post("/api/apps/x/notes", params=O, json={"text": "hi"}, headers=H).status_code == 403
    assert cy.put("/api/apps/x/env", params=O, json={"name": "A", "value": "1"}, headers=H).status_code == 403
    assert cy.put("/api/apps/x/shares", json={"user": "cy", "perm": "edit"}, headers=H).status_code == 404, "cy has no x of their own"
    assert cy.get("/api/apps/x/preflight", params=O).status_code == 403
    for method, path, kw in (("post", "/api/apps/x/files", {"files": [("files", ("y.txt", b"1"))]}),
                             ("put", "/api/apps/x/part", {"params": {**O, "path": "y.bin", "offset": 0, "final": True}, "content": b"1"}),
                             ("put", "/api/apps/x/document", {"json": {"text": "statement: t\n"}}),
                             ("post", "/api/apps/x/document/preview", {"json": {"text": "statement: t\n"}}),
                             ("post", "/api/apps/x/check", {}),
                             ("delete", "/api/apps/x/file", {"params": {**O, "path": "check.py"}})):
        kw = {"params": O, **kw}
        assert getattr(cy, method)(path, headers=H, **kw).status_code == 403, (method, path)
    # edit: changes it; its variables are the owner's loop's
    assert dee.put("/api/apps/x/file", params={**O, "path": "check.py"}, json={"text": "print(2)\n"}, headers=H).status_code == 200
    assert (tmp / "data/users/bob/apps/x/check.py").read_text() == "print(2)\n"
    assert dee.put("/api/apps/x/env", params=O, json={"name": "SEED", "value": "3"}, headers=H).status_code == 200
    assert app.state.store.env("loop:bob:x")["SEED"]["value"] == "3"
    assert dee.get("/api/apps/x/preflight", params=O).status_code == 200
    assert dee.delete("/api/apps/x", headers=H).status_code in (400, 404), "deleting is the owner's: dee has no x"
    started = {}
    monkeypatch.setattr(app.state.runs, "start", lambda user, name, *a, by=None, **k: started.update(user=user.name, by=by.name))
    assert dee.post("/api/apps/x/start", params=O, json={"passes": 1}, headers=H).status_code == 200
    assert started == {"user": "bob", "by": "dee"}, "the owner's loop, started by the editor"
    shared = {(s["owner"], s["name"], s["perm"]) for s in cy.get("/api/shared").json()}
    assert shared == {("bob", "x", "watch")}
    # an admin needs no share, and changes it too (D812)
    assert ada.get("/api/apps/x", params=O).json()["perm"] == "admin"
    assert ada.put("/api/apps/x/file", params={**O, "path": "check.py"}, json={"text": "x"}, headers=H).status_code == 200
    # unshare, and delete clears the shares
    assert bob.put("/api/apps/x/shares", json={"user": "cy", "perm": None}, headers=H).status_code == 200
    assert cy.get("/api/apps/x", params=O).status_code == 403
    assert bob.delete("/api/apps/x", headers=H).status_code == 200
    assert app.state.store.shares("bob", "x") == {} and dee.get("/api/shared").json() == []


def test_admin_manages_other_owners_sharing_without_touching_same_named_loop(server):
    app, _tmp = server
    bob, cy, dee, ada = (_c(app, n) for n in ("bob", "cy", "dee", "ada"))
    for client in (bob, ada):
        assert client.post("/api/apps/new-empty", json={"name": "x"}, headers=H).status_code == 200
    params = {"owner": "bob"}
    store = app.state.store
    preview = ada.get("/api/apps/x/shares", params=params).json()
    assert preview["can_share"] is True and "bob" not in preview["users"]
    # The acting admin can also be a recipient; the owner cannot.
    assert "ada" in preview["users"]
    for target in ("bob", "missing"):
        assert ada.put("/api/apps/x/shares", params=params, json={"user": target, "perm": "watch"}, headers=H).status_code == 400
    assert ada.put("/api/apps/x/shares", params=params, json={"user": "cy", "perm": "admin"}, headers=H).status_code == 400
    assert not store.shares("bob", "x")

    for permission in ("watch", "edit"):
        response = ada.put("/api/apps/x/shares", params=params, json={"user": "cy", "perm": permission}, headers=H)
        assert response.status_code == 200, response.text
        assert response.json()["shares"] == [{"user": "cy", "perm": permission}]
        assert cy.get("/api/apps/x", params=params).json()["perm"] == permission
        assert cy.get("/api/apps/x/shares", params=params).json()["can_share"] is False
        assert cy.put("/api/apps/x/shares", params=params, json={"user": "dee", "perm": "edit"}, headers=H).status_code == 403
        notice = store.take_notices("cy")[-1]
        assert notice["text"] == f"ada shared bob's x with you to {permission}"
        assert notice["href"] == "#/u/bob/app/x"
        audit = store.audit_log()[0]
        assert audit["user"] == "ada" and audit["action"] == "share"
        assert audit["detail"] == f"bob/x with cy: {permission}"
    assert not store.shares("ada", "x")
    assert ada.put("/api/apps/x/shares", params=params, json={"user": "ada", "perm": "watch"}, headers=H).status_code == 200
    # An explicit watch share must not remove the admin's ability to manage sharing.
    assert ada.get("/api/apps/x/shares", params=params).json()["can_share"] is True
    assert ada.put("/api/apps/x/shares", params=params, json={"user": "cy", "perm": None}, headers=H).status_code == 200
    assert store.shares("bob", "x") == {"ada": "watch"} and not store.shares("ada", "x")
    assert cy.get("/api/apps/x", params=params).status_code == 403
    assert dee.put("/api/apps/x/shares", params=params, json={"user": "dee", "perm": "edit"}, headers=H).status_code == 403
    notice = store.take_notices("cy")[-1]
    assert notice["text"] == "ada stopped sharing bob's x with you"
    assert store.audit_log()[0]["action"] == "unshare"
