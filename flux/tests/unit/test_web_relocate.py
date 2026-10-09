"""Rename and transfer keep history while enforcing ownership and sandbox boundaries."""

from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import closing
from unittest.mock import patch

import pytest

from flux_records import Records
from flux_web.admin import cache_root
from flux_web.relocate import move_loop
from flux_web.workspace import Workspace
from test_web import H, _client, server  # noqa: F401 -- shared fixture


@pytest.fixture()
def moving_loop(server, monkeypatch):  # noqa: F811
    app, tmp = server
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp / "cache"))
    store = app.state.store
    store.add_user("cy", "a long secret for cy", "external")
    bob = _client(app, "bob", "another long secret")
    cy = _client(app, "cy", "a long secret for cy")
    ada = _client(app, "ada", "correct horse battery")
    w = Workspace(store.data, "bob")
    w.create_from_text("x", "problem.yaml", "statement: Move me\nlanguage: text\nflow: {test: 'true'}\n")
    d = w.app("x")
    (d / "runs").mkdir()
    (d / "runs/loop.log").write_text("old full output\n" * 2)
    (d / "workbench").mkdir()
    (d / "workbench/keep.py").write_text("# keep\n")
    cache = cache_root() / "bob.x"
    trace = cache / "tmp/trace"
    trace.mkdir(parents=True)
    (trace / "events.jsonl").write_text('{"ev":"hello","t":1}\n')
    ids = []
    for index, record in enumerate(("x", "x.alt")):
        db = d / "out" / f"{record}.db"
        rec = Records(str(db), {"study": record}, name=record)
        rec.trial({"name": "kept design", "artifact": "design source"}, "key", stage="bench", strategy="loop", metrics={"time_ms": 42})
        rec.conclude({"decision": "kept design"})
        rec.close("paused")
        rec.store.close()
        (d / "out" / f"{record}.db.runs.json").write_text(json.dumps({record: str(trace)}))
        rid = store.add_run(store.user(name="bob"), "x", str(db), str(d / "runs/loop.log"),
                            ["flux", "task", "run", str(d / "problem.yaml"), "--db", str(db)],
                            {"passes": 10, "log_offset": index * len("old full output\n")})
        store.set_run(rid, ended=10, rc=0)
        ids.append(rid)
    w.set_meta("x", last_check={"ok": True}, last_options={"passes": 10})
    store.set_env("loop:bob:x", "LOOP_SECRET", "kept secret", True)
    store.server_set("adv:bob:x", {"sandbox": False, "raw_network": True, "mounts": [{"host": "/host/special", "inside": "/special", "mode": "rw"}]})
    store.set_share("bob", "x", "cy", "edit")
    return app, bob, cy, ada, w, d, cache, ids


def test_rename_keeps_settings_records_results_logs_and_cached_transcripts(moving_loop):
    app, bob, cy, _ada, w, d, cache, ids = moving_loop
    response = bob.post("/api/apps/x/rename", json={"to": "renamed"}, headers=H)
    assert response.status_code == 200, response.text
    assert response.json() == {"name": "renamed", "owner": "bob"}
    new = w.app("renamed")
    assert not d.exists() and not cache.exists()
    assert (new / "workbench/keep.py").read_text() == "# keep\n"
    store = app.state.store
    assert store.env("loop:bob:renamed", reveal=True)["LOOP_SECRET"]["value"] == "kept secret"
    assert store.server_get("adv:bob:renamed")["sandbox"] is False
    assert store.shares("bob", "renamed") == {"cy": "edit"}
    assert not store.env("loop:bob:x") and not store.server_get("adv:bob:x") and not store.shares("bob", "x")
    for rid, record in zip(ids, ("renamed", "renamed.alt")):
        run = store.run(rid)
        assert run["app"] == "renamed" and run["user"] == "bob"
        assert run["db"] == str(new / "out" / f"{record}.db")
        assert run["log"] == str(new / "runs/loop.log")
        assert json.loads(run["options"])["passes"] == 10
        # Historical invocation and record agree with the next launch's file names.
        assert str(new / "out" / f"{record}.db") in json.loads(run["argv"])
        with closing(sqlite3.connect(run["db"])) as db:
            assert db.execute("SELECT campaign_id FROM campaigns").fetchone()[0] == record
            assert db.execute("SELECT campaign_id FROM trials").fetchone()[0] == record
        results = bob.get("/api/apps/renamed/results", params={"run_id": rid, "campaign": record})
        assert results.status_code == 200 and results.json()["designs"][0]["name"] == "kept design"
        events = bob.get("/api/apps/renamed/run-data", params={"run_id": rid, "campaign": record, "kind": "events"})
        assert events.status_code == 200 and '"ev":"hello"' in events.text
        assert bob.get("/api/apps/renamed/log/raw", params={"run_id": rid}).text == "old full output\n"
    assert cy.get("/api/apps/renamed", params={"owner": "bob"}).status_code == 200
    assert w.meta("renamed")["id"] == "renamed" and "last_check" not in w.meta("renamed")
    assert w.meta("renamed")["last_options"] == {"passes": 10}


def test_transfer_moves_history_and_variables_but_clears_admin_overrides_and_shares(moving_loop):
    app, bob, cy, _ada, _w, d, cache, ids = moving_loop
    store = app.state.store
    store.set_setting(store.user(name="bob"), "FLUX_REMOTE_MODEL", "bob-only-model")
    response = bob.post("/api/apps/x/transfer", json={"user": " CY ", "to": "mine"}, headers=H)
    assert response.status_code == 200, response.text
    assert response.json() == {"name": "mine", "owner": "cy"}
    assert not d.exists() and not cache.exists()
    new = Workspace(store.data, "cy").app("mine")
    assert (new / "runs/loop.log").is_file() and (cache_root() / "cy.mine/tmp/trace/events.jsonl").is_file()
    assert store.env("loop:cy:mine", reveal=True)["LOOP_SECRET"]["value"] == "kept secret"
    assert cy.get("/api/apps/mine/env").json()["advanced"].get("sandbox", True) is True
    assert not store.server_get("adv:cy:mine") and not store.shares("cy", "mine")
    assert not store.server_get("adv:bob:x") and not store.shares("bob", "x")
    assert bob.get("/api/apps/mine", params={"owner": "cy"}).status_code == 403
    assert cy.get("/api/apps/mine/runs").json()["starts"][0]["id"] in ids
    assert all(store.run(rid)["user"] == "cy" for rid in ids)
    assert "bob-only-model" not in store.settings(store.user(name="cy")).values()
    assert store.take_notices("cy")[-1]["href"] == "#/app/mine/settings"


@pytest.mark.parametrize("action,body", [("rename", {"to": "new"}), ("transfer", {"user": "ada"})])
def test_shared_editors_cannot_rename_or_transfer_but_admins_can(moving_loop, action, body):
    _app, _bob, cy, ada, w, _d, _cache, _ids = moving_loop
    args = {"params": {"owner": "bob"}, "json": body, "headers": H}
    assert cy.post(f"/api/apps/x/{action}", **args).status_code == 403
    assert cy.get("/api/apps/x/ownership", params={"owner": "bob"}).json() == {"can_manage": False, "users": []}
    response = ada.post(f"/api/apps/x/{action}", **args)
    assert response.status_code == 200, response.text
    assert not (w.root / "x").exists()


@pytest.mark.parametrize("busy", ["loop", "starting", "ask", "author", "maintenance"])
def test_move_refuses_active_work(moving_loop, busy):
    app, bob, _cy, _ada, w, _d, _cache, ids = moving_loop
    store = app.state.store
    if busy in ("loop", "starting"):
        store.set_run(ids[-1], ended=None, started=time.time(), pid=os.getpid() if busy == "loop" else None)
    with patch.object(app.state.asks, "running", return_value=busy == "ask"), \
         patch.object(app.state.authoring, "state", return_value={"running": busy == "author"}):
        if busy == "maintenance":
            app.state.maintenance._lock.acquire()
        try:
            response = bob.post("/api/apps/x/rename", json={"to": "new"}, headers=H)
            assert response.status_code == 409, response.text
        finally:
            if busy == "maintenance":
                app.state.maintenance._lock.release()
    assert w.app("x").exists() and not (w.root / "new").exists()


def test_move_rejects_invalid_names_collisions_and_disabled_recipients(moving_loop):
    app, bob, _cy, _ada, w, _d, _cache, _ids = moving_loop
    w.create_empty("taken")
    for to, code in (("../escape", 400), ("taken", 409), ("x", 400)):
        assert bob.post("/api/apps/x/rename", json={"to": to}, headers=H).status_code == code
    for user in ("nobody", "bob"):
        assert bob.post("/api/apps/x/transfer", json={"user": user}, headers=H).status_code == 400
    app.state.store.set_user("cy", disabled=True)
    assert bob.post("/api/apps/x/transfer", json={"user": "cy"}, headers=H).status_code == 400
    assert w.app("x").exists()


@pytest.mark.parametrize("linked", [".flux-app.json", "out", "out/x.db", "out/x.db-wal", "out/x.db.runs.json"])
def test_move_never_writes_through_a_metadata_link(moving_loop, linked):
    _app, bob, _cy, _ada, w, d, _cache, _ids = moving_loop
    sentinel = d.parent / "private"
    sentinel.write_text("do not change")
    entry = d / linked
    if entry.is_dir():
        entry.rename(d / "old-out")
    else:
        entry.unlink(missing_ok=True)
    entry.symlink_to(sentinel)
    response = bob.post("/api/apps/x/rename", json={"to": "new"}, headers=H)
    assert response.status_code == 400, response.text
    assert sentinel.read_text() == "do not change" and w.app("x").exists()


def test_failed_server_transaction_restores_original_folder_records_cache_and_settings(moving_loop):
    app, _bob, _cy, _ada, w, d, cache, ids = moving_loop
    store = app.state.store
    original = (d / "out/x.db").read_bytes()
    # A failed settings update occurs after the files and run rows have been changed.
    with closing(store._db()) as con:
        con.execute("CREATE TRIGGER refuse_move BEFORE UPDATE OF key ON server BEGIN SELECT RAISE(ABORT, 'refuse move'); END")
        con.commit()
    with pytest.raises(sqlite3.IntegrityError, match="refuse move"):
        move_loop(store, store.user(name="bob"), "x", store.user(name="bob"), "new")
    assert w.app("x").exists() and not (w.root / "new").exists()
    assert (d / "out/x.db").read_bytes() == original and cache.exists()
    assert store.server_get("adv:bob:x")["sandbox"] is False
    assert store.run(ids[0])["app"] == "x" and store.run(ids[0])["db"] == str(d / "out/x.db")


@pytest.mark.parametrize("caller", ["cy", "ada"])
def test_cloning_admin_configured_loops_defaults_to_no_privileges_or_secrets(moving_loop, caller):
    app, _bob, cy, ada, _w, _d, _cache, _ids = moving_loop
    client = cy if caller == "cy" else ada
    response = client.post("/api/apps/x/clone", params={"owner": "bob"}, json={"to": "clone"}, headers=H)
    assert response.status_code == 200, response.text
    settings = client.get("/api/apps/clone/env").json()
    assert settings["loop"] == []
    assert settings["advanced"].get("sandbox", True) is True
    assert not settings["advanced"].get("mounts") and not settings["advanced"].get("raw_network")
    assert not app.state.store.shares(caller, "clone")
    assert not app.state.store.server_get(f"adv:{caller}:clone")
    assert client.get("/api/apps/clone/runs").json()["starts"] == []


@pytest.mark.parametrize("action", ["clone", "transfer"])
def test_admin_can_explicitly_keep_only_execution_permissions(moving_loop, action):
    app, _bob, _cy, ada, _w, d, _cache, ids = moving_loop
    store = app.state.store
    permissions = {**store.server_get("adv:bob:x"), "allow": ["example.com", "192.0.2.1"]}
    store.server_set("adv:bob:x", {**permissions, "memory": "8g", "cpus": "4", "parallel": True,
                                   "nix_packages": ["hello"], "nixchip_packages": ["yosys"]})
    preview = ada.get("/api/apps/x/ownership", params={"owner": "bob"}).json()
    assert preview["permissions"] == permissions
    body = {"to": "kept", "keep_permissions": True}
    if action == "transfer":
        body["user"] = "cy"
    response = ada.post(f"/api/apps/x/{action}", params={"owner": "bob"}, json=body, headers=H)
    assert response.status_code == 200, response.text
    owner = "cy" if action == "transfer" else "ada"
    assert store.server_get(f"adv:{owner}:kept") == permissions
    assert not store.shares(owner, "kept")
    if action == "transfer":
        assert not d.exists() and not store.server_get("adv:bob:x")
        assert store.env("loop:cy:kept", reveal=True)["LOOP_SECRET"]["value"] == "kept secret"
        assert all(store.run(rid)["user"] == "cy" for rid in ids)
    else:
        assert d.exists() and store.server_get("adv:bob:x")["memory"] == "8g"
        assert not store.env("loop:ada:kept")
        assert ada.get("/api/apps/kept/runs").json()["starts"] == []
    audit = next(a for a in store.audit_log() if a["action"] == f"{action} loop")
    assert audit["user"] == "ada" and "special permissions kept" in audit["detail"]


@pytest.mark.parametrize("caller,action", [("bob", "clone"), ("cy", "clone"), ("bob", "transfer")])
def test_nonadmins_cannot_forge_keep_permissions(moving_loop, caller, action):
    app, bob, cy, _ada, _w, d, cache, ids = moving_loop
    store = app.state.store
    client = bob if caller == "bob" else cy
    params = {"owner": "bob"} if caller == "cy" else {}
    body = {"to": "kept", "keep_permissions": True, **({"user": "cy"} if action == "transfer" else {})}
    response = client.post(f"/api/apps/x/{action}", params=params, json=body, headers=H)
    assert response.status_code == 403, response.text
    assert "only an admin" in response.json()["detail"]
    assert d.exists() and cache.exists() and store.server_get("adv:bob:x")["sandbox"] is False
    assert store.run(ids[0])["user"] == "bob" and store.shares("bob", "x") == {"cy": "edit"}
    assert not (Workspace(store.data, caller).root / "kept").exists()
    assert not (Workspace(store.data, "cy").root / "kept").exists()


def test_failed_transfer_restores_permissions_and_history(moving_loop):
    app, _bob, _cy, _ada, w, d, cache, ids = moving_loop
    store = app.state.store
    original = (d / "out/x.db").read_bytes()
    with closing(store._db()) as con:
        con.execute("CREATE TRIGGER refuse_permissions BEFORE INSERT ON server WHEN NEW.key = 'adv:cy:kept' "
                    "BEGIN SELECT RAISE(ABORT, 'refuse permissions'); END")
        con.commit()
    with pytest.raises(sqlite3.IntegrityError, match="refuse permissions"):
        move_loop(store, store.user(name="bob"), "x", store.user(name="cy"), "kept", keep_permissions=True)
    assert w.app("x").exists() and not (Workspace(store.data, "cy").root / "kept").exists()
    assert (d / "out/x.db").read_bytes() == original and cache.exists()
    assert store.server_get("adv:bob:x")["sandbox"] is False and not store.server_get("adv:cy:kept")
    assert store.env("loop:bob:x", reveal=True)["LOOP_SECRET"]["value"] == "kept secret"
    assert store.shares("bob", "x") == {"cy": "edit"} and store.run(ids[0])["user"] == "bob"


def test_failed_clone_permissions_save_removes_incomplete_clone(moving_loop):
    app, _bob, _cy, ada, _w, d, _cache, _ids = moving_loop
    with patch.object(app.state.store, "server_set", side_effect=RuntimeError("settings unavailable")):
        with pytest.raises(RuntimeError, match="settings unavailable"):
            ada.post("/api/apps/x/clone", params={"owner": "bob"},
                     json={"to": "kept", "keep_permissions": True}, headers=H)
    assert d.exists() and not (Workspace(app.state.store.data, "ada").root / "kept").exists()
    assert app.state.store.server_get("adv:bob:x")["sandbox"] is False


def test_launches_resolved_before_a_move_cannot_recreate_the_old_loop(moving_loop):
    app, bob, _cy, _ada, w, d, _cache, _ids = moving_loop
    assert bob.post("/api/apps/x/rename", json={"to": "new"}, headers=H).status_code == 200
    with pytest.raises(ValueError, match="moved or been removed"):
        app.state.runs.start(app.state.store.user(name="bob"), "x", d, "problem.yaml", "x", {"passes": 1})
    with pytest.raises(ValueError, match="moved or been removed"):
        app.state.authoring.start(app_dir=d, workspace=w, name="x", prompt="revise", author="opencode", env={}, attachments=[], revise=None, by="bob")
    with pytest.raises(ValueError, match="moved or been removed"):
        app.state.asks.start(app_dir=d, question="Why?", author="opencode", env={}, by="bob")
    assert not d.exists() and w.app("new").exists()
