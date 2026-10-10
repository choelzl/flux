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
        rec.trial({"name": "kept design", "artifact": "design source"}, "key", stage="bench", strategy="loop", metrics={"time_ms": 42}, evaluator="tool@records")
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


@pytest.fixture()
def named_history(moving_loop):
    app, _bob, _cy, _ada, _w, d, _cache, ids = moving_loop
    for rid, record in zip(ids, ("x", "x.alt")):
        rec = Records(app.state.store.run(rid)["db"], {"study": record}, name=record)
        try:
            for suffix in ("", "/part"):
                design = record + suffix + "#2"
                rec.trial({"name": design, "artifact": "unchanged source", "meta": {"previous": record + "#1", "evaluator": record + "@bench"}},
                          design + "@bench", stage="bench", strategy="loop", metrics={"time_ms": 30}, evaluator=record + "@bench")
            rec.conclude({"decision": record + "#2", "previous": record + "#1", "evaluator": record + "@bench"})
        finally:
            rec.close("paused")
            rec.store.close()
    value = {"x/bench": {"name": "x#2", "evaluator": "x@bench", "nested": [{"x/part": "x/part#2"}],
                           "folder": str(d / "workbench"), "cache": str(d / "out/x.x.json"), "number": 30}}
    for filename in (".x.json", "x.x.json", "x.alt.x.json", "metadata.json"):
        p = d / "out" / filename
        p.write_text(json.dumps(value))
        p.chmod(0o640)
    return moving_loop


@pytest.mark.parametrize("action", ["rename", "transfer"])
def test_move_rewrites_designs_evaluators_events_and_output_metadata(named_history, action):
    app, bob, cy, _ada, w, d, _cache, ids = named_history
    before = {}
    for rid in ids:
        with closing(sqlite3.connect(app.state.store.run(rid)["db"])) as db:
            before[rid] = db.execute("SELECT id, result_id, status FROM trials").fetchall()
    response = bob.post(f"/api/apps/x/{action}", json={"to": "Renamed_2", **({"user": "cy"} if action == "transfer" else {})}, headers=H)
    assert response.status_code == 200, response.text
    client = cy if action == "transfer" else bob
    new = Workspace(app.state.store.data, "cy").app("Renamed_2") if action == "transfer" else w.app("Renamed_2")
    for rid, old, record in zip(ids, ("x", "x.alt"), ("Renamed_2", "Renamed_2.alt")):
        with closing(sqlite3.connect(app.state.store.run(rid)["db"])) as db:
            assert db.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
            assert db.execute("PRAGMA foreign_key_check").fetchall() == []
            assert db.execute("SELECT id, result_id, status FROM trials").fetchall() == before[rid]
            for table, columns in (("trials", ("candidate_json", "candidate_key")), ("results", ("evaluator", "result_json")),
                                   ("campaign_events", ("detail_json",))):
                for col in columns:
                    assert db.execute(f"SELECT count(*) FROM {table} WHERE {col} LIKE ? OR {col} LIKE ?",
                                      ("%" + old + "#%", "%" + old + "@%")).fetchone()[0] == 0
            candidate, key = db.execute("SELECT candidate_json, candidate_key FROM trials WHERE seq = 2").fetchone()
            assert json.loads(candidate)["name"] == record + "#2" and key == record + "#2@bench"
            assert json.loads(candidate)["artifact"] == "unchanged source"
            assert db.execute("SELECT evaluator FROM results ORDER BY id DESC LIMIT 1").fetchone()[0] == record + "@bench"
            assert record + "/part#2" in db.execute("SELECT candidate_json FROM trials ORDER BY id DESC LIMIT 1").fetchone()[0]
            conclusion = json.loads(db.execute("SELECT detail_json FROM campaign_events WHERE kind='conclusion' ORDER BY id DESC LIMIT 1").fetchone()[0])
            assert conclusion["decision"] == record + "#2" and conclusion["evaluator"] == record + "@bench"
        rec = Records(app.state.store.run(rid)["db"], {"study": old}, name=record)
        try:
            assert rec.resumed
            assert {row.candidate["name"] for row in rec.known_rows()} == {"kept design", record + "#2", record + "/part#2"}
        finally:
            rec.store.close()
        results = client.get("/api/apps/Renamed_2/results", params={"run_id": rid, "campaign": record})
        assert results.status_code == 200 and record + "#2" in results.text
    expected = {"Renamed_2/bench": {"name": "Renamed_2#2", "evaluator": "Renamed_2@bench", "nested": [{"Renamed_2/part": "Renamed_2/part#2"}],
                                  "folder": str(new / "workbench"), "cache": str(new / "out/Renamed_2.Renamed_2.json"), "number": 30}}
    for filename in (".Renamed_2.json", "Renamed_2.Renamed_2.json", "Renamed_2.alt.Renamed_2.json", "metadata.json"):
        p = new / "out" / filename
        assert json.loads(p.read_text()) == expected
        assert p.stat().st_mode & 0o777 == 0o640
    assert not any((new / "out" / filename).exists() for filename in (".x.json", "x.x.json", "x.alt.x.json"))
    assert not d.exists()


def test_rename_does_not_rewrite_other_names_or_rewrite_the_new_name_twice(named_history):
    app, bob, _cy, _ada, _w, _d, _cache, ids = named_history
    with closing(sqlite3.connect(app.state.store.run(ids[0])["db"])) as db:
        db.execute("UPDATE trials SET candidate_json = ? WHERE seq = 2", (json.dumps({"name": "x#2", "other": ["prefixx#2", "other.x@bench"]}),))
        db.commit()
    response = bob.post("/api/apps/x/rename", json={"to": "xx"}, headers=H)
    assert response.status_code == 200, response.text
    with closing(sqlite3.connect(app.state.store.run(ids[0])["db"])) as db:
        value = json.loads(db.execute("SELECT candidate_json FROM trials WHERE seq = 2").fetchone()[0])
        assert value == {"name": "xx#2", "other": ["prefixx#2", "other.x@bench"]}


def test_repeated_rename_with_underscore_updates_an_archive_with_a_custom_filename(named_history):
    app, bob, _cy, _ada, w, _d, _cache, ids = named_history
    # A subdocument can share the loop's original name: only the loop prefix changes.
    rec = Records(str(w.app("x") / "out/x.x.db"), {"study": "subdocument"}, name="x.x")
    rec.trial({"name": "x.x#1"}, "x.x#1@bench", stage="bench", strategy="loop", metrics={"time_ms": 1})
    rec.close("paused")
    rec.store.close()
    assert bob.post("/api/apps/x/rename", json={"to": "Old_Name-1"}, headers=H).status_code == 200
    old = w.app("Old_Name-1")
    archive = old / "out/archive.db"
    with closing(sqlite3.connect(app.state.store.run(ids[0])["db"])) as src, closing(sqlite3.connect(archive)) as dst:
        src.backup(dst)
        # An underscore must be literal, never the single-character wildcard in LIKE.
        dst.execute("UPDATE trials SET candidate_json = ? WHERE seq = 2",
                    (json.dumps({"name": "Old_Name-1#2", "other": "OldXName-1#2"}),))
        dst.commit()
    response = bob.post("/api/apps/Old_Name-1/rename", json={"to": "Final"}, headers=H)
    assert response.status_code == 200, response.text
    with closing(sqlite3.connect(w.app("Final") / "out/archive.db")) as db:
        assert db.execute("SELECT campaign_id FROM campaigns").fetchone()[0] == "Final"
        candidate, key = db.execute("SELECT candidate_json, candidate_key FROM trials WHERE seq = 2").fetchone()
        assert json.loads(candidate) == {"name": "Final#2", "other": "OldXName-1#2"}
        assert key == "Final#2@bench"
        assert db.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
    assert (w.app("Final") / "out/Final.Final.json").is_file()
    assert not (w.app("Final") / "out/Final.Final.db").exists()
    with closing(sqlite3.connect(w.app("Final") / "out/Final.x.db")) as db:
        assert db.execute("SELECT campaign_id FROM campaigns").fetchone()[0] == "Final.x"
        assert db.execute("SELECT candidate_key FROM trials").fetchone()[0] == "Final.x#1@bench"


@pytest.mark.parametrize("collision", ["file", "two files", "key", "pointer"])
def test_output_collisions_abort_before_moving_anything(named_history, collision):
    app, bob, _cy, _ada, w, d, cache, ids = named_history
    if collision == "file":
        (d / "out/.new.json").write_text("{}")
    elif collision == "two files":
        (d / "out/x.new.json").write_text("{}")  # collides with x.x.json -> new.new.json
    elif collision == "key":
        (d / "out/.x.json").write_text('{"x/bench": 1, "new/bench": 2}')
    else:
        (d / "out/x.db.runs.json").write_text('{"x": "old trace", "new": "other trace"}')
    originals = {p.name: p.read_bytes() for p in (d / "out").iterdir()}
    response = bob.post("/api/apps/x/rename", json={"to": "new"}, headers=H)
    assert response.status_code in (400, 409), response.text
    assert w.app("x").exists() and not (w.root / "new").exists() and cache.exists()
    # Read-only SQLite backups may leave newly created WAL/SHM bookkeeping files.
    assert {name: (d / "out" / name).read_bytes() for name in originals} == originals
    assert all(p.name in originals or p.name.endswith((".db-wal", ".db-shm")) for p in (d / "out").iterdir())
    assert app.state.store.run(ids[0])["app"] == "x"


def test_invalid_record_references_abort_before_installing_prepared_files(named_history):
    _app, bob, _cy, _ada, w, d, cache, _ids = named_history
    with closing(sqlite3.connect(d / "out/x.db")) as db:
        db.execute("UPDATE trials SET result_id = 999999 WHERE seq = 2")
        db.commit()
    original = (d / "out/x.db").read_bytes()
    response = bob.post("/api/apps/x/rename", json={"to": "new"}, headers=H)
    assert response.status_code == 400 and "integrity check" in response.text
    assert w.app("x").exists() and not (w.root / "new").exists() and cache.exists()
    assert (d / "out/x.db").read_bytes() == original


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


@pytest.mark.parametrize("linked", [".flux-app.json", "out", "out/x.db", "out/x.db-wal", "out/x.db.runs.json",
                                    "out/.x.json", "out/x.x.json", "out/archive.db"])
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


def test_failed_server_transaction_restores_original_folder_records_cache_and_settings(named_history):
    app, _bob, _cy, _ada, w, d, cache, ids = named_history
    store = app.state.store
    original = {p.name: p.read_bytes() for p in (d / "out").iterdir()}
    # A failed settings update occurs after the files and run rows have been changed.
    with closing(store._db()) as con:
        con.execute("CREATE TRIGGER refuse_move BEFORE UPDATE OF key ON server BEGIN SELECT RAISE(ABORT, 'refuse move'); END")
        con.commit()
    with pytest.raises(sqlite3.IntegrityError, match="refuse move"):
        move_loop(store, store.user(name="bob"), "x", store.user(name="bob"), "new")
    assert w.app("x").exists() and not (w.root / "new").exists()
    assert {name: (d / "out" / name).read_bytes() for name in original} == original and cache.exists()
    assert all(p.name in original or p.name.endswith((".db-wal", ".db-shm")) for p in (d / "out").iterdir())
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
