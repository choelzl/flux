"""Single-group membership, per-user capabilities and migration without broader access."""

from __future__ import annotations

import json
import sqlite3
import sys
from contextlib import closing
from unittest.mock import patch

import pytest

from flux_web.store import DEFAULT_PERMISSIONS, Store
from flux_web.workspace import Workspace
from test_web import H, _client, server  # noqa: F401 -- shared fixture

DOCUMENT = "statement: Group permissions\nlanguage: text\nflow: {test: 'true'}\n"


@pytest.fixture()
def team(server):  # noqa: F811
    app, tmp = server
    store = app.state.store
    store.add_user("cy", "a long secret for cy")
    store.add_user("dee", "a long secret for dee", "external")
    clients = {n: _client(app, n, p) for n, p in (("ada", "correct horse battery"), ("bob", "another long secret"),
                                                ("cy", "a long secret for cy"), ("dee", "a long secret for dee"))}
    for name in ("bob", "cy", "dee"):
        Workspace(store.data, name).create_from_text("x", "problem.yaml", DOCUMENT)
    return app, store, clients, tmp


def test_legacy_roles_migrate_once_and_group_renames_do_not_change_powers_or_credentials(tmp_path):
    data = tmp_path / "old"
    data.mkdir()
    with closing(sqlite3.connect(data / "flux-web.db")) as db:
        db.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, pw TEXT NOT NULL, "
                   "role TEXT NOT NULL DEFAULT 'user', created REAL NOT NULL, disabled INTEGER NOT NULL DEFAULT 0)")
        for i, (name, role) in enumerate((("ada", "admin"), ("bob", "user"), ("cy", "internal"), ("dee", "external")), 1):
            db.execute("INSERT INTO users VALUES (?, ?, ?, ?, ?, 0)", (i, name, Store.hash_password("a long secret"), role, 42))
        db.commit()
    store = Store(data)
    before = {u.name: u for u in store.users()}
    assert before["bob"].group_id == before["cy"].group_id
    assert before["ada"].group == "Admin" and before["dee"].group == "External"
    assert before["ada"].admin and before["dee"].external and not before["bob"].external
    assert before["bob"].permissions == DEFAULT_PERMISSIONS
    assert store.loop_access(before["bob"], before["cy"], "x") is None
    token = store.login("ada", "a long secret")
    store.save_group("Operations", before["ada"].group_id)
    store.save_group("Research", before["bob"].group_id)
    # A new group can use a former built-in label; reopening must not recreate the built-in.
    custom = store.save_group("Internal", server_access=False)
    reopened = Store(data)
    assert len(reopened.groups()) == 4
    assert reopened.session_user(token).admin and reopened.session_user(token).group == "Operations"
    assert reopened.user(name="bob").group == "Research" and reopened.user(name="bob").group_id != custom["id"]
    assert reopened.user(name="dee").external
    assert reopened.user(name="bob").permissions == DEFAULT_PERMISSIONS
    with closing(reopened._db()) as db:
        assert {r[0] for r in db.execute("SELECT created FROM users")} == {42}


def test_groups_are_admin_managed_and_memberships_preserve_account_settings(team):
    app, store, c, _tmp = team
    catalog = c["ada"].get("/api/groups").json()
    assert len(catalog["groups"]) == 3 and catalog["defaults"] == DEFAULT_PERMISSIONS
    for method, url, body in (("get", "/api/groups", None), ("post", "/api/groups", {"name": "Team", "server_access": False}),
                              ("patch", "/api/groups/1", {"name": "Changed"})):
        args = {"headers": H, **({"json": body} if body else {})}
        assert getattr(c["bob"], method)(url, **args).status_code == 403
    created = c["ada"].post("/api/groups", json={"name": "Team", "server_access": False}, headers=H).json()
    assert created["members"] == 0 and created["admin"] is False
    added = c["ada"].post("/api/users", json={"name": "invited", "group_id": created["id"],
                                              "permissions": {"create_loops": False}}, headers=H)
    assert added.status_code == 200 and added.json()["kind"] == "invite"
    invitation = c["bob"].post("/api/invite/" + added.json()["token"], json={"text": "an invited user's secret"}, headers=H)
    assert invitation.status_code == 200 and invitation.json()["group_id"] == created["id"]
    assert invitation.json()["credential_mode"] == "external" and not invitation.json()["permissions"]["create_loops"]
    # The invitation changes this client's session; return it to its fixture account.
    c["bob"] = _client(app, "bob", "another long secret")
    assert c["ada"].post("/api/groups", json={"name": " team ", "server_access": True}, headers=H).status_code == 400
    assert c["ada"].patch("/api/groups/99999", json={"name": "Absent"}, headers=H).status_code == 404
    store.set_setting(store.user(name="dee"), "FLUX_REMOTE_MODEL", "personal-model")
    group_id = created["id"]
    assert c["ada"].patch("/api/users/dee", json={"group_id": group_id, "permissions": {"view_others": True}}, headers=H).status_code == 200
    assert store.user(name="dee").external and store.settings(store.user(name="dee"))["FLUX_REMOTE_MODEL"] == "personal-model"
    assert Workspace(store.data, "dee").app("x").exists()
    assert c["ada"].patch("/api/groups/" + str(group_id), json={"name": "Research"}, headers=H).status_code == 200
    identity = c["dee"].get("/api/me").json()
    assert identity["group"] == "Research" and identity["permissions"]["view_others"]
    assert c["dee"].get("/api/groups").status_code == 403
    assert c["ada"].patch("/api/users/dee", json={"group_id": 99999}, headers=H).status_code == 400
    assert c["ada"].patch("/api/users/dee", json={"permissions": {"admin": True}}, headers=H).status_code == 400
    assert c["ada"].patch("/api/users/dee", json={"permissions": {"run_loops": "false"}}, headers=H).status_code == 422
    assert store.user(name="dee").group_id == group_id
    assert any(a["action"] == "rename group" for a in store.audit_log())
    # Server administration belongs to the built-in group's ID, not its editable name.
    admin_group = store.user(name="ada").group_id
    assert c["ada"].patch("/api/groups/" + str(admin_group), json={"name": "Operations"}, headers=H).status_code == 200
    assert c["ada"].get("/api/users").status_code == 200
    assert c["ada"].patch("/api/users/ADA", json={"group_id": group_id}, headers=H).status_code == 400
    with pytest.raises(ValueError, match="enabled admin"):
        store.set_user("ada", group_id=group_id)
    assert app.state.store.user(name="ada").admin


def test_group_server_access_applies_to_every_member_and_existing_sessions(team, monkeypatch):
    from flux_web.runs import run_env

    _app, store, c, _tmp = team
    group = store.save_group("Shared configuration", server_access=False)
    for name in ("bob", "cy"):
        store.set_user(name, group_id=group["id"])
    store.set_server_setting("FLUX_REMOTE_MODEL", "group-server-model")
    store.set_server_setting("FLUX_CLAUDE_MODEL", "group-agent-model")
    store.set_env("global", "GROUP_SERVER_FLAG", "server-variable", False)
    store.set_setting(store.user(name="cy"), "FLUX_REMOTE_MODEL", "personal-model")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-machine-key")
    monkeypatch.setattr("flux_web.agents.found", lambda agent, _store: sys.executable if agent.name == "claude" else "")
    url = f"/api/groups/{group['id']}"
    assert c["bob"].patch(url, json={"server_access": True}, headers=H).status_code == 403
    assert c["ada"].patch(url, json={"server_access": "false"}, headers=H).status_code == 422
    for enabled in (True, False, True):
        response = c["ada"].patch(url, json={"server_access": enabled}, headers=H)
        assert response.status_code == 200 and response.json()["server_access"] is enabled
        for name in ("bob", "cy"):
            assert c[name].get("/api/me").json()["credential_mode"] == ("internal" if enabled else "external")
            assert c[name].get("/api/settings").json()["external"] is not enabled
            env = run_env(store, store.user(name=name))
            assert env.get("FLUX_REMOTE_MODEL") == ("personal-model" if name == "cy" else "group-server-model" if enabled else None)
            agent_env = json.loads(env.get("FLUX_CLAUDE_ENV", "{}"))
            assert agent_env.get("FLUX_CLAUDE_MODEL") == ("group-agent-model" if enabled else None)
            assert env.get("GROUP_SERVER_FLAG") == ("server-variable" if enabled else None)
            assert env.get("ANTHROPIC_API_KEY") == ("test-machine-key" if enabled else None)
        # Moving a member and adding one both take the group's policy, regardless of legacy role.
        store.set_user("dee", group_id=group["id"])
        assert store.user(name="dee").external is not enabled
    added = c["ada"].post("/api/users", json={"name": "joining", "password": "a long secret", "group_id": group["id"]}, headers=H)
    assert added.status_code == 200 and not store.user(name="joining").external
    renamed = c["ada"].patch(url, json={"name": "Renamed configuration"}, headers=H).json()
    assert renamed["server_access"] is True and not store.user(name="bob").external
    assert Store(store.data).user(name="bob").credential_mode == "internal"
    assert any(a["action"] == "group server access" for a in store.audit_log())


def test_group_creation_requires_explicit_access_and_users_cannot_override_it(team):
    _app, store, c, _tmp = team
    assert c["ada"].post("/api/groups", json={"name": "Unspecified"}, headers=H).status_code == 422
    assert c["ada"].post("/api/groups", json={"name": "Unspecified", "server_access": 1}, headers=H).status_code == 422
    assert c["ada"].patch("/api/users/bob", json={"credential_mode": "external"}, headers=H).status_code == 422
    assert c["ada"].post("/api/users", json={"name": "override", "credential_mode": "internal"}, headers=H).status_code == 422
    assert store.user(name="override") is None and not store.user(name="bob").external


def test_group_access_migration_preserves_consistent_and_mixed_members(tmp_path):
    data = tmp_path / "old-groups"
    data.mkdir()
    with closing(sqlite3.connect(data / "flux-web.db")) as db:
        db.execute("CREATE TABLE user_groups (id INTEGER PRIMARY KEY, name TEXT UNIQUE COLLATE NOCASE NOT NULL, kind TEXT UNIQUE)")
        db.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, pw TEXT NOT NULL, role TEXT NOT NULL, "
                   "created REAL NOT NULL, disabled INTEGER NOT NULL DEFAULT 0, group_id INTEGER, permissions TEXT, credential_mode TEXT)")
        for ident, name in enumerate(("Shared", "Private", "Mixed", "Empty"), 1):
            db.execute("INSERT INTO user_groups VALUES (?, ?, NULL)", (ident, name))
        for ident, (name, gid, mode) in enumerate((("shared", 1, "internal"), ("private", 2, "external"),
                                                  ("mixed-in", 3, "internal"), ("mixed-out", 3, "external")), 1):
            db.execute("INSERT INTO users VALUES (?, ?, '', ?, 42, 0, ?, '{}', ?)", (ident, name, mode, gid, mode))
        db.commit()
    store = Store(data)
    groups = {g["name"]: g for g in store.groups()}
    assert groups["Shared"]["server_access"] is True and groups["Private"]["server_access"] is False
    assert groups["Mixed"]["server_access"] is None and groups["Empty"]["server_access"] is None
    assert not store.user(name="mixed-in").external and store.user(name="mixed-out").external
    assert not Store(data).user(name="mixed-in").external and Store(data).user(name="mixed-out").external
    store.save_group(None, groups["Mixed"]["id"], server_access=False)
    store.save_group(None, groups["Shared"]["id"], server_access=False)
    reopened = Store(data)
    assert all(reopened.user(name=n).external for n in ("shared", "mixed-in", "mixed-out"))
    assert {u.name: u.group_id for u in reopened.users()} == {"shared": 1, "private": 2, "mixed-in": 3, "mixed-out": 3}


@pytest.mark.parametrize("permission", ["view_others", "edit_others", "run_others", "share_others"])
def test_independent_group_permissions_and_live_revocation(team, permission):
    app, store, c, _tmp = team
    bob, cy = c["bob"], c["cy"]
    params = {"owner": "bob"}
    assert cy.get("/api/apps/x", params=params).status_code == 403
    assert cy.get("/api/group-loops").json() == []
    store.set_user("cy", permissions={permission: True})
    info = cy.get("/api/apps/x", params=params).json()
    assert info["can_edit"] is (permission == "edit_others")
    assert info["can_run"] is (permission == "run_others")
    assert info["can_leave"] is False
    assert cy.get("/api/apps/x/preflight", params=params).status_code == (200 if permission == "run_others" else 403)
    assert cy.get("/api/apps/x/shares", params=params).json()["can_share"] is (permission == "share_others")
    assert {(l["owner"], l["name"]) for l in cy.get("/api/group-loops").json()} == {("bob", "x")}
    assert {(l.get("owner", "cy"), l["app"]) for l in cy.get("/api/loops").json()} == {("cy", "x"), ("bob", "x")}
    assert cy.get("/api/apps/x", params={"owner": "dee"}).status_code == 403
    edited = cy.put("/api/apps/x/file", params={**params, "path": "notes.txt"}, json={"text": "notes"}, headers=H)
    assert edited.status_code == (200 if permission == "edit_others" else 403)
    with patch.object(app.state.runs, "start") as launch:
        response = cy.post("/api/apps/x/start", params=params, json={"passes": 1}, headers=H)
        assert response.status_code == (200 if permission == "run_others" else 403), response.text
        assert launch.called is (permission == "run_others")
        if launch.called:
            assert launch.call_args.args[0].name == "bob" and launch.call_args.kwargs["by"].name == "cy"
    share = cy.put("/api/apps/x/shares", params=params, json={"user": "dee", "perm": "watch"}, headers=H)
    assert share.status_code == (200 if permission == "share_others" else 403)
    assert cy.post("/api/apps/x/rename", params=params, json={"to": "changed"}, headers=H).status_code == 403
    assert cy.post("/api/apps/x/transfer", params=params, json={"user": "dee"}, headers=H).status_code == 403
    assert cy.post("/api/apps/x/reset", params=params, headers=H).status_code == 403
    store.set_user("cy", permissions={permission: False})
    assert cy.get("/api/apps/x", params=params).status_code == 403
    assert cy.get("/api/group-loops").json() == []
    # Explicit sharing still works across groups, regardless of group-wide grants.
    assert bob.put("/api/apps/x/shares", json={"user": "dee", "perm": "edit"}, headers=H).status_code == 200
    assert c["dee"].get("/api/apps/x", params=params).json()["can_run"] is True


def test_explicit_shares_and_group_rights_combine_without_bypassing_run_restrictions(team):
    _app, store, c, _tmp = team
    store.set_user("cy", permissions={"edit_others": True})
    store.set_share("bob", "x", "cy", "watch")
    params = {"owner": "bob"}
    assert c["cy"].get("/api/apps/x", params=params).json()["can_edit"] is True
    assert c["cy"].get("/api/apps/x", params=params).json()["can_run"] is False
    assert c["cy"].get("/api/shared").json()[0]["perm"] == "edit"
    store.set_share("bob", "x", "cy", "edit")
    assert c["cy"].get("/api/apps/x", params=params).json()["can_run"] is True
    store.set_user("cy", permissions={"run_loops": False, "run_others": True})
    assert c["cy"].post("/api/apps/x/start", params=params, json={"passes": 1}, headers=H).status_code == 403
    assert c["cy"].post("/api/apps/x/author", params=params, data={"prompt": "Improve", "author": "model"}, headers=H).status_code == 403
    assert c["cy"].post("/api/apps/x/asks", params=params, json={"question": "Why?", "author": "model"}, headers=H).status_code == 403
    assert c["cy"].get("/api/group-loops").json() == []  # already listed under individual sharing
    assert [(row.get("owner"), row["app"]) for row in c["cy"].get("/api/loops").json()].count(("bob", "x")) == 1


def test_creation_and_execution_caps_are_enforced_on_all_entry_points(team):
    app, store, c, _tmp = team
    bob = c["bob"]
    store.set_user("bob", permissions={"create_loops": False, "run_loops": False})
    for url, args in (("/api/apps/from-text", {"json": {"name": "new", "filename": "problem.yaml", "text": DOCUMENT}}),
                      ("/api/apps/new-empty", {"json": {"name": "new"}}),
                      ("/api/apps/x/clone", {"json": {"to": "new"}}),
                      ("/api/apps", {"data": {"name": "new"}, "files": [("files", ("problem.yaml", DOCUMENT.encode()))]}),
                      ("/api/apps/new-by-agent", {"data": {"name": "new", "prompt": "Build", "author": "model"}}),
                      ("/api/apps/x/author", {"data": {"prompt": "Improve", "author": "model"}}),
                      ("/api/apps/x/asks", {"json": {"question": "Why?", "author": "model"}}),
                      ("/api/apps/x/start", {"json": {"passes": 1}}), ("/api/apps/x/check", {})):
        response = bob.post(url, headers=H, **args)
        assert response.status_code == 403, (url, response.text)
    assert not (Workspace(store.data, "bob").root / "new").exists()
    assert bob.get("/api/apps/x/preflight").status_code == 403
    assert bob.get("/api/apps/x").status_code == 200
    assert bob.put("/api/apps/x/file", params={"path": "notes.txt"}, json={"text": "still editable"}, headers=H).status_code == 200
    # Revoking run permission still lets owners stop an already running loop.
    with patch.object(app.state.runs, "stop", return_value="stopped"):
        assert bob.post("/api/apps/x/stop", json={"now": True}, headers=H).status_code == 200
    store.set_user("bob", permissions={"create_loops": True})
    assert bob.post("/api/apps/from-text", json={"name": "new", "filename": "problem.yaml", "text": DOCUMENT}, headers=H).status_code == 200
    assert bob.post("/api/apps/new-by-agent", data={"name": "agent", "prompt": "Build", "author": "model"}, headers=H).status_code == 403


def test_group_moves_revoke_automatic_access_but_keep_shares_and_credentials(team):
    _app, store, c, _tmp = team
    store.set_user("cy", permissions={"view_others": True})
    token_user = c["cy"].get("/api/me").json()
    assert token_user["permissions"]["view_others"]
    assert c["cy"].get("/api/apps/x", params={"owner": "bob"}).status_code == 200
    new = store.save_group("Independent", server_access=True)
    store.set_user("cy", group_id=new["id"])
    assert c["cy"].get("/api/apps/x", params={"owner": "bob"}).status_code == 403
    assert c["cy"].get("/api/group-loops").json() == []
    assert c["bob"].put("/api/apps/x/shares", json={"user": "cy", "perm": "watch"}, headers=H).status_code == 200
    assert c["cy"].get("/api/apps/x", params={"owner": "bob"}).status_code == 200
    assert store.user(name="cy").credential_mode == "internal"
    # Ordinary groups named like a legacy role cannot grant server administration.
    admin = store.user(name="ada")
    store.save_group("Operators", admin.group_id)
    impostor = store.save_group("Admin", server_access=False)
    store.set_user("cy", group_id=impostor["id"])
    assert c["cy"].get("/api/groups").status_code == 403
    assert c["ada"].get("/api/groups").status_code == 200
    assert store.user(name="cy").permissions["view_others"]
