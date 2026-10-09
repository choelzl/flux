"""Admin view-as sessions use the target's access, forbid changes and retain a reversible owner."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from flux_web import create_app
from flux_web.workspace import Workspace
from test_web import H, _client, server  # noqa: F401 -- shared fixture

DOCUMENT = "statement: View-as access\nlanguage: text\nflow: {test: 'true'}\n"


@pytest.fixture()
def viewing(server):  # noqa: F811
    app, tmp = server
    store = app.state.store
    store.add_user("cy", "a long secret for cy", "external")
    for name in ("ada", "bob", "cy"):
        Workspace(store.data, name).create_from_text("mine", "problem.yaml", DOCUMENT)
    admin = _client(app, "ada", "correct horse battery")
    bob = _client(app, "bob", "another long secret")
    other = _client(app, "ada", "correct horse battery")
    return app, store, admin, bob, other, tmp


def test_view_as_uses_the_users_access_and_credentials_and_survives_reload(viewing):
    app, store, admin, bob, other, tmp = viewing
    store.set_server_setting("FLUX_REMOTE_MODEL", "server-model")
    store.set_setting(store.user(name="bob"), "FLUX_REMOTE_MODEL", "bob-model")
    cookie = admin.cookies.get("flux_session")
    identity = bob.get("/api/me").json()
    response = admin.post("/api/users/bob/impersonate", headers=H)
    assert response.status_code == 200 and response.json() == {**identity, "impersonator": "ada"}
    assert admin.cookies.get("flux_session") == cookie, "the original session is retained, never sent separately"
    assert admin.get("/api/me").json() == response.json()
    assert admin.get("/api/settings").json() == bob.get("/api/settings").json()
    for path in ("/api/apps", "/api/group-loops", "/api/shared"):
        assert admin.get(path).status_code == 200
        assert admin.get(path).json() == bob.get(path).json()
    assert admin.get("/api/apps/mine/file", params={"path": "problem.yaml", "owner": "ada"}).status_code == 403
    for path in ("/api/users", "/api/groups", "/api/admin/apps", "/api/audit"):
        assert admin.get(path).status_code == 403
    assert other.get("/api/me").json()["name"] == "ada" and other.get("/api/users").status_code == 200
    assert "impersonator" not in bob.get("/api/me").json()
    # Both a server restart and a new client with the same cookie retain the target.
    restarted = create_app(tmp / "data", sandbox=False)
    client = TestClient(restarted)
    client.cookies.set("flux_session", cookie)
    assert client.get("/api/me").json() == {**identity, "impersonator": "ada"}
    assert client.delete("/api/impersonation", headers=H).json()["name"] == "ada"
    assert admin.get("/api/users").status_code == 200
    assert admin.get("/api/impersonation").json() is None
    assert [(x["user"], x["action"], x["detail"]) for x in store.audit_log() if "view as" in x["action"]] == [
        ("ada", "return from view as", "bob"), ("ada", "view as user", "bob")]


@pytest.mark.parametrize("method,path,body", [
    ("PUT", "/api/settings", {"values": {"FLUX_REMOTE_MODEL": "changed"}}),
    ("POST", "/api/password", {"text": "a changed password"}),
    ("PUT", "/api/apps/mine/file?path=problem.yaml", {"text": "statement: changed"}),
    ("POST", "/api/apps/mine/start", {"passes": 1}),
    ("POST", "/api/apps/mine/stop", {}),
    ("POST", "/api/apps/mine/ask", {"text": "run an agent"}),
    ("DELETE", "/api/apps/mine", None),
    ("POST", "/api/apps/from-text", {"name": "new", "filename": "problem.yaml", "text": DOCUMENT}),
    ("POST", "/api/users/cy/impersonate", None),
])
def test_view_as_cannot_mutate_or_launch_anything(viewing, monkeypatch, method, path, body):
    app, store, admin, _bob, _other, _tmp = viewing
    before = Workspace(store.data, "bob").apps()
    monkeypatch.setattr(app.state.runs, "start", lambda *_a, **_k: pytest.fail("view-as launched a process"))
    monkeypatch.setattr(app.state.runs, "stop", lambda *_a, **_k: pytest.fail("view-as stopped a process"))
    assert admin.post("/api/users/bob/impersonate", headers=H).status_code == 200
    response = admin.request(method, path, json=body, headers=H)
    assert response.status_code == 403 and "read-only" in response.json()["detail"]
    assert Workspace(store.data, "bob").apps() == before
    assert admin.get("/api/apps/mine/file", params={"path": "problem.yaml"}).text == DOCUMENT
    assert store.settings(store.user(name="bob")) == {}
    with store._db() as db:
        assert store.check_password("another long secret", db.execute("SELECT pw FROM users WHERE name='bob'").fetchone()[0])


def test_only_authenticated_admins_can_start_and_targets_must_be_enabled_and_ready(viewing):
    app, store, admin, bob, _other, _tmp = viewing
    assert TestClient(app).post("/api/users/bob/impersonate", headers=H).status_code == 401
    assert bob.post("/api/users/ada/impersonate", headers=H).status_code == 403
    assert admin.post("/api/users/bob/impersonate").status_code == 403
    assert admin.post("/api/users/absent/impersonate", headers=H).status_code == 404
    assert admin.post("/api/users/ada/impersonate", headers=H).status_code == 400
    store.set_user("cy", disabled=True)
    assert admin.post("/api/users/cy/impersonate", headers=H).status_code == 400
    store.add_user("invited", None)
    assert admin.post("/api/users/invited/impersonate", headers=H).status_code == 400
    assert admin.get("/api/me").json()["name"] == "ada"
    assert bob.delete("/api/impersonation", headers=H).json()["name"] == "bob", "ordinary users cannot restore an admin"


def test_target_permissions_and_group_server_access_are_rechecked_live(viewing):
    _app, store, admin, _bob, other, _tmp = viewing
    store.set_server_setting("FLUX_REMOTE_MODEL", "server-model")
    store.set_user("cy", permissions={"run_loops": False, "create_loops": False})
    assert admin.post("/api/users/cy/impersonate", headers=H).status_code == 200
    identity = admin.get("/api/me").json()
    assert identity["role"] == "external" and not identity["permissions"]["run_loops"]
    assert not identity["permissions"]["create_loops"]
    settings = admin.get("/api/settings").json()
    assert settings["external"] and not any(settings["inherited"].values())
    store.set_user("cy", permissions={"create_loops": True})
    assert admin.get("/api/me").json()["permissions"]["create_loops"]
    group = store.user(name="cy").group_id
    assert other.patch(f"/api/groups/{group}", json={"server_access": True}, headers=H).status_code == 200
    assert admin.get("/api/me").json()["role"] == "internal"
    assert admin.get("/api/settings").json()["external"] is False
    assert admin.delete("/api/impersonation").status_code == 403, "return still requires CSRF protection"
    assert admin.get("/api/me").json()["name"] == "cy"


def test_revocation_is_checked_live_and_return_survives_a_disabled_target(viewing):
    _app, store, admin, bob, other, _tmp = viewing
    assert other.put("/api/apps/mine/shares", json={"user": "bob", "perm": "watch"}, headers=H).status_code == 200
    assert admin.post("/api/users/bob/impersonate", headers=H).status_code == 200
    args = {"params": {"owner": "ada", "path": "problem.yaml"}}
    assert admin.get("/api/apps/mine/file", **args).status_code == 200
    assert other.put("/api/apps/mine/shares", json={"user": "bob", "perm": None}, headers=H).status_code == 200
    assert admin.get("/api/apps/mine/file", **args).status_code == 403
    assert other.patch("/api/users/bob", json={"disabled": True}, headers=H).status_code == 200
    assert bob.get("/api/me").status_code == 401 and admin.get("/api/me").status_code == 401
    assert admin.get("/api/impersonation").json() == {"name": "bob", "impersonator": "ada"}
    assert admin.delete("/api/impersonation", headers=H).json()["name"] == "ada"
    assert admin.get("/api/users").status_code == 200


@pytest.mark.parametrize("revoke", ["disable", "demote", "expire", "reset", "logout"])
def test_origin_revocation_never_retains_impersonation_power(viewing, revoke):
    app, store, admin, bob, other, _tmp = viewing
    store.add_user("eve", "eve has a long secret", "admin")
    assert admin.post("/api/users/eve/impersonate", headers=H).status_code == 200
    assert admin.get("/api/users").status_code == 200, "read-only viewing also supports admin targets"
    if revoke == "disable":
        store.set_user("ada", disabled=True)
    elif revoke == "demote":
        store.set_user("ada", role="external")
    elif revoke == "expire":
        with patch("flux_web.store.time.time", return_value=10**12):
            assert admin.get("/api/me").status_code == 401
            assert admin.delete("/api/impersonation", headers=H).status_code == 401
        return
    elif revoke == "reset":
        token, _ = store.invite("ada")
        store.use_invite(token, "a new admin password")
    else:
        assert admin.post("/api/logout", headers=H).status_code == 200
    assert admin.get("/api/me").status_code == 401
    assert admin.get("/api/users").status_code == 401
    if revoke == "demote":
        response = admin.delete("/api/impersonation", headers=H)
        assert response.status_code == 200 and response.json()["role"] == "external"
        assert admin.get("/api/users").status_code == 403
    else:
        assert admin.delete("/api/impersonation", headers=H).status_code == 401
        with store._db() as db:
            assert db.execute("SELECT COUNT(*) FROM impersonations").fetchone()[0] == 0
    assert bob.get("/api/me").status_code == 200
