"""D695: the admin's view of the machine -- every loop's disk, the caches no loop owns, the
sandbox's containers -- and the controls: clean a cache, pause new starts, a user's running
limit, stop every loop. None of it for a user who is not an admin."""

from __future__ import annotations

import subprocess
import sys
import json

import pytest
from fastapi.testclient import TestClient

from flux_web import create_app
from flux_web.runs import loop_files
from flux_web.store import Store

H = {"X-Flux": "1"}


def test_token_rates_include_retained_campaigns_and_records_once(server, monkeypatch):
    app, tmp = server
    bob = _client(app, "bob", "another long secret")
    ada = _client(app, "ada", "correct horse battery")
    _loop(bob, "tokens")
    store, runs = app.state.store, app.state.runs
    directory = store.data / "users/bob/apps/tokens"
    log = directory / "runs/loop.log"
    log.parent.mkdir(exist_ok=True)
    db = directory / "out/old.db"
    db.parent.mkdir(exist_ok=True)
    paths = {}
    for name, kind, tin, tout in (("old", "agent", 6000, 600), ("new", "model", 3000, 300)):
        trace = directory / "out" / name
        trace.mkdir()
        path = trace / "turns.jsonl"
        path.write_text(json.dumps({"ts": 4200, "kind": kind, "seconds": 600,
                                    "tokens_in": tin, "tokens_out": tout}) + "\n")
        paths[name] = str(trace)
    # Resumed campaigns can point at the same directory, and starts can share a record.
    db.with_name(db.name + ".runs.json").write_text(json.dumps({"old": paths["old"], "resume": paths["old"] + "/."}))
    for record in (db, db, directory / "out/new.db"):
        store.add_run(store.user(name="bob"), "tokens", str(record), str(log), ["flux"], {})
    outside = tmp / "outside"
    outside.mkdir()
    (outside / "turns.jsonl").write_text(json.dumps({"ts": 4200, "seconds": 600, "tokens_in": 999999}) + "\n")
    linked = directory / "out/linked"
    linked.mkdir()
    (linked / "turns.jsonl").symlink_to(outside / "turns.jsonl")
    (directory / "out/new.db.runs.json").write_text(json.dumps({
        "new": paths["new"], "bad": str(outside), "link": str(linked), "invalid": 42,
        "missing": str(directory / "out/missing"),
    }))
    from flux_web import insights

    monkeypatch.setattr(insights.time, "time", lambda: 4200)
    assert bob.get("/api/admin/token-rate").status_code == 403
    response = ada.get("/api/admin/token-rate?hours=1")
    assert response.status_code == 200
    got = response.json()
    assert got["bucket_seconds"] == 20
    samples = got["samples"]
    assert sum(p["in_agent"] for p in samples) * 20 == pytest.approx(6000)
    assert sum(p["out_agent"] for p in samples) * 20 == pytest.approx(600)
    assert sum(p["in_model"] for p in samples) * 20 == pytest.approx(3000)
    assert sum(p["out_model"] for p in samples) * 20 == pytest.approx(300)
    assert runs.turns_paths(store.latest_run(store.user(name="bob"), "tokens")) == sorted([
        str(directory / "out/missing/turns.jsonl"), str(directory / "out/new/turns.jsonl"),
    ])
    # An appended turn is picked up without rereading or duplicating old turns.
    with (directory / "out/old/turns.jsonl").open("a") as fh:
        fh.write(json.dumps({"ts": 4200, "kind": "agent", "seconds": 20, "tokens_in": 1000}) + "\n")
    fresh = ada.get("/api/admin/token-rate?hours=1").json()["samples"]
    assert sum(p["in_agent"] for p in fresh) * 20 == pytest.approx(7000)


@pytest.fixture()
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    store = Store(tmp_path / "data")
    store.add_user("ada", "correct horse battery", "admin")
    store.add_user("bob", "another long secret")
    app = create_app(tmp_path / "data", sandbox=False)
    return app, tmp_path


def _client(app, name, password):
    c = TestClient(app)
    assert c.post("/api/login", json={"name": name, "password": password}, headers=H).status_code == 200
    return c


def _loop(c, name):
    files = [("files", (f"{name}.problem.yaml", b"statement: s\n"))]
    assert c.post("/api/apps", data={"name": name}, files=files, headers=H).status_code == 200


def _running(app, tmp_path, user, name):
    d = tmp_path / "data" / "users" / user / "apps" / name
    lf = loop_files(d)
    lf["log"].parent.mkdir(exist_ok=True)
    lf["log"].write_text("")
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True)
    rid = app.state.store.add_run(app.state.store.user(name=user), name, str(d / "out" / "x.db"), str(lf["log"]), ["x"], {})
    app.state.store.set_run(rid, pid=proc.pid)
    return proc


def test_the_admin_sees_every_loops_disk_and_the_caches_no_loop_owns(server):
    app, tmp = server
    bob, ada = _client(app, "bob", "another long secret"), _client(app, "ada", "correct horse battery")
    _loop(bob, "x")
    caches = tmp / "xdg" / "flux" / "apps"
    run = caches / "bob.x" / "tmp" / "flux-traces" / "x"
    (run / "20260930T120000" / "agents").mkdir(parents=True)
    (run / "20260930T120000" / "agents" / "draft.sv").write_bytes(b"m" * 5000)
    (run / "events.jsonl").write_text("{}\n")
    (caches / "bob.x" / "cache" / "yosys").mkdir(parents=True)
    (caches / "bob.x" / "cache" / "yosys" / "blob").write_bytes(b"y" * 3000)
    (caches / "bob.gone" / "tmp").mkdir(parents=True)
    (caches / "bob.gone" / "tmp" / "f").write_bytes(b"g" * 100)
    (caches / "adder16").mkdir(parents=True)
    assert bob.get("/api/admin/resources").status_code == 403
    assert bob.post("/api/admin/caches/bob.x/clean", json={"what": "all"}, headers=H).status_code == 403
    r = ada.get("/api/admin/resources").json()
    loop = next(x for x in r["loops"] if x["user"] == "bob" and x["app"] == "x")
    assert loop["cache"] >= 8000 and loop["inputs"] > 0 and loop["total"] == sum(loop[k] for k in ("inputs", "record", "log", "workbench", "cache"))
    kinds = {c["key"]: c["kind"] for c in r["caches"]}
    assert kinds == {"bob.gone": "gone", "adder16": "other"}, "the loop's own cache is with its loop"
    assert r["machine"]["cpus"] >= 1 and r["machine"]["disks"] and r["paused"] is None
    freed = ada.post("/api/admin/caches/bob.x/clean", json={"what": "scratch"}, headers=H).json()["freed"]
    assert freed >= 5000 and (run / "events.jsonl").exists() and not (run / "20260930T120000").exists(), \
        "a past pass's scratch goes, the journal stays"
    assert ada.post("/api/admin/caches/bob.x/clean", json={"what": "tools"}, headers=H).json()["freed"] >= 3000
    assert (caches / "bob.x" / "cache").is_dir() and not any((caches / "bob.x" / "cache").iterdir())
    assert ada.post("/api/admin/caches/bob.gone/clean", json={"what": "all"}, headers=H).status_code == 200
    assert not (caches / "bob.gone").exists()
    assert ada.post("/api/admin/caches/..%2F..%2Fetc/clean", json={"what": "all"}, headers=H).status_code in (400, 404)
    assert ada.post("/api/admin/caches/bob.x/clean", json={"what": "everything"}, headers=H).status_code == 400
    assert ada.post("/api/admin/containers/not-ours/kill", headers=H).status_code == 400, "only a sandbox container"


def test_pausing_starts_limits_and_stopping_every_loop(server):
    app, tmp = server
    bob, ada = _client(app, "bob", "another long secret"), _client(app, "ada", "correct horse battery")
    _loop(bob, "x")
    _loop(bob, "y")
    assert ada.put("/api/admin/paused", json={"reason": "upgrading the server"}, headers=H).json()["paused"] == "upgrading the server"
    r = bob.post("/api/apps/x/start", json={"passes": 1}, headers=H)
    assert r.status_code == 409 and "upgrading the server" in r.json()["detail"]
    assert bob.get("/api/apps/x/preflight").json()["paused"] == "upgrading the server"
    assert ada.put("/api/admin/paused", json={"reason": None}, headers=H).json()["paused"] is None
    assert ada.put("/api/admin/users/bob/limit", json={"max_running": 0}, headers=H).status_code == 200
    r = bob.post("/api/apps/x/start", json={"passes": 1}, headers=H)
    assert r.status_code == 409 and "at most 0" in r.json()["detail"]
    assert ada.put("/api/admin/users/bob/limit", json={"max_running": 99}, headers=H).status_code == 400
    assert ada.put("/api/admin/users/nobody/limit", json={"max_running": 1}, headers=H).status_code == 404
    assert ada.put("/api/admin/users/bob/limit", json={"max_running": None}, headers=H).status_code == 200
    assert app.state.runs.limit(app.state.store.user(name="bob")) == 4, "back to the server's"
    procs = [_running(app, tmp, "bob", n) for n in ("x", "y")]
    try:
        r = ada.get("/api/admin/resources").json()
        assert {(x["user"], x["app"]) for x in r["running"]} == {("bob", "x"), ("bob", "y")}
        assert ada.post("/api/admin/caches/bob.x/clean", json={"what": "all"}, headers=H).status_code in (400, 409)
        said = ada.post("/api/admin/stop-all", json={"now": True}, headers=H).json()["stopped"]
        assert set(said) == {"bob/x", "bob/y"}
        for p in procs:
            p.wait(timeout=10)
        assert not bob.get("/api/apps/x/state").json()["running"]
        assert any(a["action"] == "stop all" for a in ada.get("/api/audit").json())
    finally:
        for p in procs:
            p.kill()


def test_an_admin_edits_anyones_loop_and_a_user_still_only_their_own(server):
    """D812: an admin changes and runs anyone's loop, as an editor of it would; a user without a
    share still only reads nothing of it."""
    app, tmp = server
    bob, ada = _client(app, "bob", "another long secret"), _client(app, "ada", "correct horse battery")
    _loop(bob, "x")
    assert ada.put("/api/apps/x/file", params={"path": "bench.sh", "owner": "bob"}, json={"text": "echo t=1\n"}, headers=H).status_code == 200
    assert (tmp / "data/users/bob/apps/x/bench.sh").read_text() == "echo t=1\n"
    assert ada.put("/api/apps/x/env", params={"owner": "bob"}, json={"name": "SEED", "value": "7"}, headers=H).status_code == 200
    assert ada.get("/api/apps/x", params={"owner": "bob"}).json()["perm"] == "admin"
    from flux_web.store import Store

    store = Store(tmp / "data")
    store.add_user("cy", "cy has a long secret")
    cy = _client(app, "cy", "cy has a long secret")
    assert cy.put("/api/apps/x/file", params={"path": "y", "owner": "bob"}, json={"text": "z"}, headers=H).status_code == 403


def test_an_admin_cannot_remove_a_running_loops_network_helper(server, monkeypatch):
    from flux_web import admin

    app, tmp = server
    bob, ada = _client(app, "bob", "another long secret"), _client(app, "ada", "correct horse battery")
    _loop(bob, "x")
    proc = _running(app, tmp, "bob", "x")
    monkeypatch.setattr(app.state.runs, "state", lambda *args: {"container": "flux-abcdef"})
    removed = []
    monkeypatch.setattr(admin, "kill_container", lambda name: removed.append(name) or "removed")
    try:
        for name in ("flux-abcdef", "flux-abcdef-network"):
            r = ada.post(f"/api/admin/containers/{name}/kill", headers=H)
            assert r.status_code == 409 and "stop the loop" in r.json()["detail"]
        assert removed == []
        assert ada.post("/api/admin/containers/flux-123456-network/kill", headers=H).status_code == 200
        assert removed == ["flux-123456-network"]
    finally:
        proc.kill()
        proc.wait(timeout=5)
