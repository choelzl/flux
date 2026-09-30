"""`flux serve` (D683): accounts, the CSRF header, applications and their files, isolation between
users, and a run started through the API and followed through its journal."""

from __future__ import annotations

import io
import time
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from flux_web import create_app
from flux_web.store import Store

H = {"X-Flux": "1"}


@pytest.fixture()
def server(tmp_path):
    store = Store(tmp_path / "data")
    store.add_user("ada", "correct horse battery", "admin")
    store.add_user("bob", "another long secret")
    app = create_app(tmp_path / "data", sandbox=False)
    return app, tmp_path


def _client(app, name, password):
    c = TestClient(app)
    r = c.post("/api/login", json={"name": name, "password": password}, headers=H)
    assert r.status_code == 200, r.text
    return c


def test_login_sessions_csrf_and_the_lock(server):
    app, _ = server
    c = TestClient(app)
    assert c.get("/api/me").status_code == 401
    assert c.post("/api/login", json={"name": "ada", "password": "correct horse battery"}).status_code == 403, "no X-Flux header"
    r = c.post("/api/login", json={"name": "ada", "password": "correct horse battery"}, headers=H)
    assert r.status_code == 200 and "httponly" in r.headers["set-cookie"].lower() and "samesite=strict" in r.headers["set-cookie"].lower()
    assert c.get("/api/me").json() == {"name": "ada", "role": "admin"}
    assert c.post("/api/logout").status_code == 403, "a change needs the header"
    c.post("/api/logout", headers=H)
    assert c.get("/api/me").status_code == 401
    bad = TestClient(app)
    for _ in range(5):
        assert bad.post("/api/login", json={"name": "bob", "password": "wrong"}, headers=H).status_code == 401
    assert bad.post("/api/login", json={"name": "bob", "password": "another long secret"}, headers=H).status_code == 401, \
        "five failures lock the name"


def test_admins_manage_users_and_users_do_not(server):
    app, _ = server
    bob = _client(app, "bob", "another long secret")
    assert bob.get("/api/users").status_code == 403 and bob.get("/api/audit").status_code == 403
    ada = _client(app, "ada", "correct horse battery")
    assert ada.post("/api/users", json={"name": "cy", "password": "short"}, headers=H).status_code == 400
    assert ada.post("/api/users", json={"name": "cy", "password": "a long enough one"}, headers=H).status_code == 200
    assert ada.patch("/api/users/cy", json={"disabled": True}, headers=H).status_code == 200
    assert TestClient(app).post("/api/login", json={"name": "cy", "password": "a long enough one"}, headers=H).status_code == 401
    assert ada.patch("/api/users/ada", json={"disabled": True}, headers=H).status_code == 400
    assert any(a["action"] == "add user" for a in ada.get("/api/audit").json())


def test_uploads_are_checked_and_users_are_apart(server):
    app, _ = server
    bob = _client(app, "bob", "another long secret")
    files = [("files", ("sw/sw.problem.yaml", b"id: sw\nstatement: x\n")), ("files", ("sw/golden.py", b"def golden(a): return {}\n"))]
    r = bob.post("/api/apps", data={"name": "sw"}, files=files, headers=H)
    assert r.status_code == 200 and r.json()["document"] == "sw.problem.yaml" and r.json()["id"] == "sw", r.text
    assert bob.post("/api/apps", data={"name": "sw"}, files=files, headers=H).status_code == 400, "exists"
    assert bob.get("/api/apps/sw/file", params={"path": "golden.py"}).text.startswith("def golden")
    for bad in ("../../../etc/passwd", "/etc/passwd", "a/../../x"):
        assert bob.get("/api/apps/sw/file", params={"path": bad}).status_code == 400, bad
    zbuf = io.BytesIO()
    with zipfile.ZipFile(zbuf, "w") as z:
        z.writestr("x.problem.yaml", "id: x\n")
        z.writestr("../evil.py", "boom")
    assert bob.post("/api/apps", data={"name": "zz"}, files=[("files", ("a.zip", zbuf.getvalue()))], headers=H).status_code == 400
    lbuf = io.BytesIO()
    with zipfile.ZipFile(lbuf, "w") as z:
        z.writestr("x.problem.yaml", "id: x\n")
        info = zipfile.ZipInfo("link")
        info.external_attr = (0o120777 << 16)
        z.writestr(info, "/etc/passwd")
    assert "link" in bob.post("/api/apps", data={"name": "zl"}, files=[("files", ("a.zip", lbuf.getvalue()))], headers=H).text
    assert bob.put("/api/apps/sw/file", params={"path": "golden.py"}, json={"text": "# edited\n"}, headers=H).status_code == 200
    ada = _client(app, "ada", "correct horse battery")
    assert ada.get("/api/apps").json() == [], "bob's applications are bob's"
    assert ada.get("/api/apps/sw").status_code == 404


def test_a_run_started_from_the_web_is_followed_through_its_journal(server, tmp_path):
    from flux_cli.main import main

    app, _ = server
    assert main(["new", "--kind", "sweep", "sw", "--dir", str(tmp_path / "sw")]) == 0
    bob = _client(app, "bob", "another long secret")
    files = [("files", (f"sw/{p.name}", p.read_bytes())) for p in (tmp_path / "sw").iterdir() if p.is_file()]
    assert bob.post("/api/apps", data={"name": "sw"}, files=files, headers=H).status_code == 200
    r = bob.post("/api/apps/sw/runs", json={"passes": 1}, headers=H)
    assert r.status_code == 200, r.text
    run = r.json()["id"]
    deadline = time.time() + 240
    while time.time() < deadline and bob.get(f"/api/runs/{run}").json()["live"]:
        time.sleep(1)
    st = bob.get(f"/api/runs/{run}").json()
    assert not st["live"] and st["rc"] == 0 and st["campaign"] and st["events"], st
    runs = app.state.runs
    from flux_loop.journal import read_events

    events, _ = read_events(runs.events_path(app.state.store.run(run)))
    assert any(e["ev"] == "start" for e in events) and any(e["ev"] == "end" for e in events)
    res = bob.get(f"/api/runs/{run}/results").json()
    assert res["rows"] and "time_ms" in res["rows"][0]["metrics"] and res["answer"], res
    assert "<svg" in bob.get(f"/api/runs/{run}/report").text or "<h1>" in bob.get(f"/api/runs/{run}/report").text
    assert _client(app, "ada", "correct horse battery").get(f"/api/runs/{run}").status_code == 200, "an admin reads every run"
    assert bob.post(f"/api/runs/{run}/stop", json={"now": True}, headers=H).json()["ok"] == "not running"
    assert Path(app.state.store.run(run)["log"]).read_text()
