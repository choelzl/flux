"""`flux serve` (D683): accounts, the CSRF header, applications and their files, isolation between
users, and a run started through the API and followed through its journal."""

from __future__ import annotations

import io
import json
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


def test_files_are_added_to_an_existing_application(server):
    app, _ = server
    bob = _client(app, "bob", "another long secret")
    files = [("files", ("x.problem.yaml", b"id: x\nstatement: s\n"))]
    assert bob.post("/api/apps", data={"name": "x"}, files=files, headers=H).status_code == 200
    r = bob.post("/api/apps/x/files", data={"folder": "notes"}, files=[("files", ("a.md", b"# a")), ("files", ("b.md", b"# b"))], headers=H)
    assert r.status_code == 200 and r.json()["written"] == ["notes/a.md", "notes/b.md"], r.text
    assert bob.get("/api/apps/x/file", params={"path": "notes/b.md"}).text == "# b"
    assert bob.post("/api/apps/x/files", files=[("files", (".flux-app.json", b"{}"))], headers=H).status_code == 400
    assert bob.post("/api/apps/x/files", data={"folder": "../.."}, files=[("files", ("e", b"x"))], headers=H).status_code == 400
    r = bob.post("/api/apps/x/files", files=[("files", ("x.problem.yaml", b"id: renamed\n"))], headers=H)
    assert r.status_code == 200 and bob.get("/api/apps").json()[0]["id"] == "renamed", "a new document, a new id"


def test_an_admin_sees_every_application_read_only(server):
    app, _ = server
    bob = _client(app, "bob", "another long secret")
    bob.post("/api/apps", data={"name": "x"}, files=[("files", ("x.problem.yaml", b"id: x\n"))], headers=H)
    ada = _client(app, "ada", "correct horse battery")
    everyone = ada.get("/api/admin/apps").json()
    assert [(a["owner"], a["name"]) for a in everyone] == [("bob", "x")]
    info = ada.get("/api/apps/x", params={"owner": "bob"}).json()
    assert info["owner"] == "bob" and info["mine"] is False
    assert ada.get("/api/apps/x/file", params={"path": "x.problem.yaml", "owner": "bob"}).text == "id: x\n"
    assert bob.get("/api/apps/x", params={"owner": "ada"}).status_code == 403, "users read only their own"
    assert bob.get("/api/admin/apps").status_code == 403


def test_a_users_model_settings_are_theirs_and_their_keys_secret(server, monkeypatch):
    from flux_web.runs import run_env

    app, tmp = server
    monkeypatch.setenv("FLUX_REMOTE_API_KEY", "the-servers-key")
    monkeypatch.setenv("FLUX_REMOTE_API_KEY_FILE", "/server/key")
    bob = _client(app, "bob", "another long secret")
    assert bob.put("/api/settings", json={"values": {"FLUX_REMOTE_BASE_URL": "ftp://x"}}, headers=H).status_code == 400
    assert bob.put("/api/settings", json={"values": {"PATH": "/evil"}}, headers=H).status_code == 400
    r = bob.put("/api/settings", json={"values": {"FLUX_REMOTE_BASE_URL": "https://bob.example/v1", "FLUX_REMOTE_MODEL": "m",
                                                   "ANTHROPIC_API_KEY": "sk-bob"}}, headers=H)
    assert r.status_code == 200 and r.json()["values"]["ANTHROPIC_API_KEY"] == "set"
    assert "sk-bob" not in bob.get("/api/settings").text
    assert b"sk-bob" not in (tmp / "data" / "flux-web.db").read_bytes(), "encrypted at rest"
    store = app.state.store
    env = run_env(store, store.user(name="bob"))
    assert env["FLUX_REMOTE_BASE_URL"] == "https://bob.example/v1" and env["ANTHROPIC_API_KEY"] == "sk-bob"
    assert "FLUX_REMOTE_API_KEY" not in env and "FLUX_REMOTE_API_KEY_FILE" not in env, "the server's key never goes to bob's endpoint"
    assert env["FLUX_LLM_REMOTE"] == "1" and env["FLUX_CONFIG"].startswith("/dev/null")
    ada_env = run_env(store, store.user(name="ada"))
    assert ada_env["FLUX_REMOTE_API_KEY"] == "the-servers-key", "no settings: the server's model"
    bob.put("/api/settings", json={"values": {"ANTHROPIC_API_KEY": None}}, headers=H)
    assert "ANTHROPIC_API_KEY" not in bob.get("/api/settings").json()["values"]


def test_notes_reach_a_live_run_through_its_inbox(server, tmp_path):
    import json as _json

    app, _ = server
    store, runs = app.state.store, app.state.runs
    bob = store.user(name="bob")
    log = tmp_path / "r.log"
    log.write_text("")
    import subprocess
    import sys

    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        rid = store.add_run(bob, "x", str(tmp_path / "x.db"), str(log), ["x"], {})
        store.set_run(rid, pid=proc.pid)
        c = _client(app, "bob", "another long secret")
        assert c.post(f"/api/runs/{rid}/notes", json={"text": "try carry-select"}, headers=H).status_code == 200
        line = _json.loads((tmp_path / "r.inbox.jsonl").read_text())
        assert line["text"] == "try carry-select" and line["by"] == "bob"
        assert [n["text"] for n in c.get(f"/api/runs/{rid}/notes").json()] == ["try carry-select"]
        ada = _client(app, "ada", "correct horse battery")
        assert ada.post(f"/api/runs/{rid}/notes", json={"text": "x"}, headers=H).status_code == 403, "only the owner steers"
    finally:
        proc.kill()
        proc.wait()
    store.set_run(rid, ended=1.0)
    assert c.post(f"/api/runs/{rid}/notes", json={"text": "late"}, headers=H).status_code == 409


def test_the_configurator_reads_a_document_back_and_saves_it_with_what_it_keeps(server):
    app, _ = server
    bob = _client(app, "bob", "another long secret")
    doc = b"id: x\nstatement: make x\nlanguage: python\ngate:\n  - {name: test, run: '{python} {home}/check.py {artifact}'}\n" \
          b"stages:\n  - {name: bench, command: '{python} {home}/bench.py {artifact}', metrics: [time_ms]}\n" \
          b"objectives:\n  - {metric: time_ms, direction: minimize}\nparams: {n: 5}\n"
    files = [("files", ("x.problem.yaml", doc)), ("files", ("check.py", b"print('0 failing')\n")), ("files", ("bench.py", b"print('time_ms=1')\n"))]
    assert bob.post("/api/apps", data={"name": "x"}, files=files, headers=H).status_code == 200
    v = bob.get("/api/apps/x/document").json()
    assert v["document"] == "x.problem.yaml" and v["raw"]["params"] == {"n": 5} and "test" in json.dumps(v["normal"]["gate"])
    new = "id: x\nstatement: make x faster\nlanguage: python\n"
    r = bob.put("/api/apps/x/document", json={"text": new, "kept": ["params"]}, headers=H)
    assert r.status_code == 200, r.text
    text = bob.get("/api/apps/x/file", params={"path": "x.problem.yaml"}).text
    assert "make x faster" in text and "Kept as written" in text and "n: 5" in text
    assert bob.put("/api/apps/x/document", json={"text": "params: {n: 1}\n", "kept": ["params"]}, headers=H).status_code == 400, \
        "a kept key the configurator also wrote is refused"
    assert TestClient(app).get("/crafter-assets/crafter.js").status_code == 200
    assert TestClient(app).get("/crafter-assets/tools.json").json()


def test_a_user_can_neither_see_nor_use_another_users_loops(server, tmp_path):
    """Loops are per user (D687): every route that names another user's application or run
    answers as if it did not exist, or refuses; only an admin reads them."""
    app, _ = server
    store = app.state.store
    store.add_user("cy", "a third long secret")
    bob = _client(app, "bob", "another long secret")
    bob.post("/api/apps", data={"name": "x"}, files=[("files", ("x.problem.yaml", b"id: x\nstatement: s\n"))], headers=H)
    log = tmp_path / "r.log"
    log.write_text("secret output\n")
    rid = store.add_run(store.user(name="bob"), "x", str(tmp_path / "x.db"), str(log), ["x"], {})
    cy = _client(app, "cy", "a third long secret")
    assert cy.get("/api/apps").json() == [] and cy.get("/api/runs").json() == []
    assert cy.get("/api/runs", params={"everyone": 1}).json() == [], "everyone is an admin's"
    for path in ("/api/apps/x", "/api/apps/x/files", "/api/apps/x/document"):
        assert cy.get(path).status_code in (400, 404), path
    assert cy.get("/api/apps/x/file", params={"path": "x.problem.yaml"}).status_code == 400
    for path, kw in (("/api/apps/x", {"owner": "bob"}), ("/api/apps/x/document", {"owner": "bob"}),
                     ("/api/apps/x/file", {"owner": "bob", "path": "x.problem.yaml"})):
        assert cy.get(path, params=kw).status_code == 403, path
    for path in ("", "/turns", "/results", "/report", "/notes"):
        assert cy.get(f"/api/runs/{rid}{path}").status_code == 404, path
    assert cy.post(f"/api/runs/{rid}/stop", json={"now": True}, headers=H).status_code == 404
    assert cy.post(f"/api/runs/{rid}/notes", json={"text": "hi"}, headers=H).status_code == 404
    for method, path, kw in (("post", "/api/apps/x/runs", {"json": {"passes": 1}}), ("post", "/api/apps/x/check", {}),
                             ("put", "/api/apps/x/document", {"json": {"text": "id: y\\n"}}),
                             ("put", "/api/apps/x/file", {"params": {"path": "x.problem.yaml"}, "json": {"text": "id: y"}}),
                             ("delete", "/api/apps/x", {})):
        r = getattr(cy, method)(path, headers=H, **kw)
        assert r.status_code in (400, 404), (path, r.status_code)
    assert bob.get("/api/apps/x/file", params={"path": "x.problem.yaml"}).text.startswith("id: x"), "untouched"
    ada = _client(app, "ada", "correct horse battery")
    assert ada.get(f"/api/runs/{rid}").status_code == 200 and ada.get("/api/apps/x", params={"owner": "bob"}).status_code == 200
    assert ada.put("/api/apps/x/file", params={"path": "x.problem.yaml", "owner": "bob"}, json={"text": "id: z"},
                   headers=H).status_code in (400, 404), "an admin reads; the owner writes"


def test_two_users_same_named_applications_never_share_a_sandbox(server):
    from flux_web.runs import run_env  # noqa: F401 -- the key is set where runs start

    app, _ = server
    store = app.state.store
    store.add_user("a-b", "long enough one")
    store.add_user("a", "long enough two")
    import types

    from flux_cli.sandbox import app_dir

    keys = []
    for user, name in (("a-b", "c"), ("a", "b-c")):
        import os

        os.environ["FLUX_SANDBOX_APP"] = f"{user}.{name}"
        try:
            keys.append(app_dir(types.SimpleNamespace(file="nope.yaml"), "task run").name)
        finally:
            del os.environ["FLUX_SANDBOX_APP"]
    assert keys[0] != keys[1], keys


def test_the_workbench_the_log_download_and_an_open_question(server, tmp_path):
    """D688: the workbench listed with first lines; the whole log as a file; an agent's question
    in the run's state until it is answered or its time is up; each application's last run."""
    import json as _json
    import subprocess
    import sys
    import time as _time

    app, _ = server
    store, runs = app.state.store, app.state.runs
    bob = _client(app, "bob", "another long secret")
    bob.post("/api/apps", data={"name": "x"}, files=[("files", ("x.problem.yaml", b"id: x\nstatement: s\n")),
                                                     ("files", ("workbench/notes/adders.md", b"# Carry-select wins above 3 GHz\n")),
                                                     ("files", ("workbench/tools/fit.py", b'"""Fit a cubic per segment."""\n'))], headers=H)
    wb = bob.get("/api/apps/x/workbench").json()
    assert {(w["kind"], w["first"]) for w in wb} == {("notes", "Carry-select wins above 3 GHz"), ("tools", "Fit a cubic per segment.")}
    rdir = tmp_path / "rundir"
    rdir.mkdir()
    (tmp_path / "x.db.runs.json").write_text(_json.dumps({"x": str(rdir)}))
    log = tmp_path / "r.log"
    log.write_text("line one\nERROR two\n")
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        rid = store.add_run(store.user(name="bob"), "x", str(tmp_path / "x.db"), str(log), ["x"], {})
        store.set_run(rid, pid=proc.pid)
        r = bob.get(f"/api/runs/{rid}/log/raw")
        assert r.text == "line one\nERROR two\n" and "attachment" in r.headers["content-disposition"]
        assert bob.get(f"/api/runs/{rid}").json()["question"] is None
        q = {"question": "Ripple or carry-select?", "wait_s": 300, "asked": _time.time()}
        (rdir / "events.jsonl").write_text(_json.dumps({"t": _time.time(), "ev": "mark", "name": "question", "why": _json.dumps(q)}) + "\n")
        assert bob.get(f"/api/runs/{rid}").json()["question"]["question"] == "Ripple or carry-select?"
        assert bob.get("/api/apps").json()[0]["last_run"]["id"] == rid
        bob.post(f"/api/runs/{rid}/notes", json={"text": "carry-select"}, headers=H)
        assert bob.get(f"/api/runs/{rid}").json()["question"] is None, "answered"
    finally:
        proc.kill()
        proc.wait()


def test_one_live_run_per_application(server, tmp_path):
    """D688: runs of one application share its record: a second is refused while one is live."""
    import subprocess
    import sys

    app, _ = server
    store = app.state.store
    bob = _client(app, "bob", "another long secret")
    bob.post("/api/apps", data={"name": "x"}, files=[("files", ("x.problem.yaml", b"id: x\nstatement: s\n"))], headers=H)
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        rid = store.add_run(store.user(name="bob"), "x", str(tmp_path / "x.db"), str(tmp_path / "r.log"), ["x"], {})
        store.set_run(rid, pid=proc.pid)
        r = bob.post("/api/apps/x/runs", json={"passes": 1}, headers=H)
        assert r.status_code == 429 and f"run #{rid}" in r.json()["detail"]
    finally:
        proc.kill()
        proc.wait()
