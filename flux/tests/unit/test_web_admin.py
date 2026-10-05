"""D695: the admin's view of the machine -- every loop's disk, the caches no loop owns, the
sandbox's containers -- and the controls: clean a cache, pause new starts, a user's running
limit, stop every loop. None of it for a user who is not an admin."""

from __future__ import annotations

import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from flux_web import create_app
from flux_web.runs import loop_files
from flux_web.store import Store

H = {"X-Flux": "1"}


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
