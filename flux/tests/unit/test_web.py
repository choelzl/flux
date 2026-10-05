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
    files = [("files", ("sw/sw.problem.yaml", b"statement: x\n")), ("files", ("sw/golden.py", b"def golden(a): return {}\n"))]
    r = bob.post("/api/apps", data={"name": "sw"}, files=files, headers=H)
    assert r.status_code == 200 and r.json()["document"] == "sw.problem.yaml" and r.json()["id"] == "sw", r.text
    assert bob.post("/api/apps", data={"name": "sw"}, files=files, headers=H).status_code == 400, "exists"
    assert bob.get("/api/apps/sw/file", params={"path": "golden.py"}).text.startswith("def golden")
    for bad in ("../../../etc/passwd", "/etc/passwd", "a/../../x"):
        assert bob.get("/api/apps/sw/file", params={"path": bad}).status_code == 400, bad
    zbuf = io.BytesIO()
    with zipfile.ZipFile(zbuf, "w") as z:
        z.writestr("x.problem.yaml", "")
        z.writestr("../evil.py", "boom")
    assert bob.post("/api/apps", data={"name": "zz"}, files=[("files", ("a.zip", zbuf.getvalue()))], headers=H).status_code == 400
    lbuf = io.BytesIO()
    with zipfile.ZipFile(lbuf, "w") as z:
        z.writestr("x.problem.yaml", "")
        info = zipfile.ZipInfo("link")
        info.external_attr = (0o120777 << 16)
        z.writestr(info, "/etc/passwd")
    assert "link" in bob.post("/api/apps", data={"name": "zl"}, files=[("files", ("a.zip", lbuf.getvalue()))], headers=H).text
    assert bob.put("/api/apps/sw/file", params={"path": "golden.py"}, json={"text": "# edited\n"}, headers=H).status_code == 200
    ada = _client(app, "ada", "correct horse battery")
    assert ada.get("/api/apps").json() == [], "bob's applications are bob's"
    assert ada.get("/api/apps/sw").status_code == 404


def test_files_are_added_to_an_existing_application(server):
    app, _ = server
    bob = _client(app, "bob", "another long secret")
    files = [("files", ("x.problem.yaml", b"statement: s\n"))]
    assert bob.post("/api/apps", data={"name": "x"}, files=files, headers=H).status_code == 200
    r = bob.post("/api/apps/x/files", data={"folder": "notes"}, files=[("files", ("a.md", b"# a")), ("files", ("b.md", b"# b"))], headers=H)
    assert r.status_code == 200 and r.json()["written"] == ["notes/a.md", "notes/b.md"], r.text
    assert bob.get("/api/apps/x/file", params={"path": "notes/b.md"}).text == "# b"
    assert bob.post("/api/apps/x/files", files=[("files", (".flux-app.json", b"{}"))], headers=H).status_code == 400
    assert bob.post("/api/apps/x/files", data={"folder": "../.."}, files=[("files", ("e", b"x"))], headers=H).status_code == 400
    r = bob.post("/api/apps/x/files", files=[("files", ("x.problem.yaml", b""))], headers=H)
    assert r.status_code == 200 and bob.get("/api/apps").json()[0]["id"] == "x", "the folder is the id (D786)"


def test_an_admin_sees_every_application_read_only(server):
    app, _ = server
    bob = _client(app, "bob", "another long secret")
    bob.post("/api/apps", data={"name": "x"}, files=[("files", ("x.problem.yaml", b""))], headers=H)
    ada = _client(app, "ada", "correct horse battery")
    everyone = ada.get("/api/admin/apps").json()
    assert [(a["owner"], a["name"]) for a in everyone] == [("bob", "x")]
    info = ada.get("/api/apps/x", params={"owner": "bob"}).json()
    assert info["owner"] == "bob" and info["mine"] is False
    assert ada.get("/api/apps/x/file", params={"path": "x.problem.yaml", "owner": "bob"}).text == ""
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
                                                   "FLUX_CLAUDE_API_KEY": "sk-bob"}}, headers=H)
    assert r.status_code == 200 and r.json()["values"]["FLUX_CLAUDE_API_KEY"] == "set"
    assert "sk-bob" not in bob.get("/api/settings").text
    assert b"sk-bob" not in (tmp / "data" / "flux-web.db").read_bytes(), "encrypted at rest"
    store = app.state.store
    env = run_env(store, store.user(name="bob"))
    from web_agents import install

    install(monkeypatch, tmp)                                       # D807: an agent's settings reach it where it is offered
    env = run_env(store, store.user(name="bob"))
    assert env["FLUX_REMOTE_BASE_URL"] == "https://bob.example/v1" and "sk-bob" in env["FLUX_CLAUDE_ENV"] and "ANTHROPIC_API_KEY" not in env
    assert "FLUX_REMOTE_API_KEY" not in env and "FLUX_REMOTE_API_KEY_FILE" not in env, "the server's key never goes to bob's endpoint"
    assert env["FLUX_LLM_REMOTE"] == "1" and env["FLUX_CONFIG"].startswith("/dev/null")
    ada_env = run_env(store, store.user(name="ada"))
    assert ada_env["FLUX_REMOTE_API_KEY"] == "the-servers-key", "no settings: the server's model"
    bob.put("/api/settings", json={"values": {"FLUX_CLAUDE_API_KEY": None}}, headers=H)
    assert "FLUX_CLAUDE_API_KEY" not in bob.get("/api/settings").json()["values"]


def test_the_configurator_reads_a_document_back_and_saves_it_with_what_it_keeps(server):
    app, _ = server
    bob = _client(app, "bob", "another long secret")
    doc = b"statement: make x\nlanguage: python\nflow:\n  test:\n    test: '{python} {home}/check.py {artifact}'\n" \
          b"  measure:\n    bench: {command: '{python} {home}/bench.py {artifact}', metrics: [time_ms]}\n" \
          b"objectives:\n  - {metric: time_ms, direction: minimize}\nparams: {n: 5}\n"
    files = [("files", ("x.problem.yaml", doc)), ("files", ("check.py", b"print('0 failing')\n")), ("files", ("bench.py", b"print('time_ms=1')\n"))]
    assert bob.post("/api/apps", data={"name": "x"}, files=files, headers=H).status_code == 200
    v = bob.get("/api/apps/x/document").json()
    assert v["document"] == "x.problem.yaml" and v["raw"]["params"] == {"n": 5} and "test" in json.dumps(v["normal"]["flow"]["test"])
    new = "statement: make x faster\nlanguage: python\n"
    r = bob.put("/api/apps/x/document", json={"text": new, "kept": ["params"]}, headers=H)
    assert r.status_code == 200, r.text
    text = bob.get("/api/apps/x/file", params={"path": "x.problem.yaml"}).text
    assert "make x faster" in text and "Kept as written" in text and "n: 5" in text
    assert bob.put("/api/apps/x/document", json={"text": "params: {n: 1}\n", "kept": ["params"]}, headers=H).status_code == 400, \
        "a kept key the configurator also wrote is refused"
    assert TestClient(app).get("/crafter-assets/crafter.js").status_code == 200
    assert TestClient(app).get("/crafter-assets/tools.json").json()


def test_before_a_start_the_check_is_known_for_the_inputs_as_they_are(server, tmp_path):
    """D693: the check's verdict is kept against a digest of the inputs; an edit makes it unknown
    again, and the configurator's save is previewed before it writes."""
    from flux_cli.main import main

    app, _ = server
    assert main(["example", "sweep", "sw", "--dir", str(tmp_path)]) == 0
    bob = _client(app, "bob", "another long secret")
    files = [("files", (f"sw/{p.name}", p.read_bytes())) for p in (tmp_path / "sw").iterdir() if p.is_file()]
    assert bob.post("/api/apps", data={"name": "sw"}, files=files, headers=H).status_code == 200
    pre = bob.get("/api/apps/sw/preflight").json()
    assert pre["changed"] and not pre["checked"] and pre["ok"] is None and pre["options"] is None, pre
    r = bob.post("/api/apps/sw/check", headers=H).json()
    assert r["ok"], r["output"]
    pre = bob.get("/api/apps/sw/preflight").json()
    assert pre["checked"] and pre["ok"] is True and pre["when"], pre
    assert bob.post("/api/apps/sw/files", files=[("files", ("extra.txt", b"x"))], headers=H).status_code == 200
    assert not bob.get("/api/apps/sw/preflight").json()["checked"], "an added file: the check is unknown again"
    doc = bob.get("/api/apps/sw/document").json()["document"]
    before = bob.get("/api/apps/sw/file", params={"path": doc}).text
    p = bob.post("/api/apps/sw/document/preview", json={"text": "statement: other\n", "kept": []}, headers=H).json()
    assert p["before"] == before and "statement: other" in p["after"] and p["document"] == doc
    assert bob.get("/api/apps/sw/file", params={"path": doc}).text == before, "a preview writes nothing"


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


def _fake_start(app, user, name):
    """A live start of the loop `name`: a sleeping process in its place, its log the loop's."""
    import subprocess
    import sys

    from flux_web.runs import loop_files

    store = app.state.store
    d = store.data / "users" / user / "apps" / name
    files = loop_files(d)
    files["log"].parent.mkdir(exist_ok=True)
    files["log"].write_text("line one\nERROR two\n")
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    rid = store.add_run(store.user(name=user), name, str(d / "out" / "x.db"), str(files["log"]), ["x"], {})
    store.set_run(rid, pid=proc.pid)
    return proc, rid, d


def test_a_loop_started_from_the_web_runs_stops_and_resumes(server, tmp_path):
    """D689: a loop is running or not; a start resumes it from its record; its one log keeps
    every start, marked; its state, journal, turns, results and report are the loop's."""
    from flux_cli.main import main
    from flux_loop.journal import read_events

    app, _ = server
    assert main(["example", "sweep", "sw", "--dir", str(tmp_path)]) == 0
    bob = _client(app, "bob", "another long secret")
    files = [("files", (f"sw/{p.name}", p.read_bytes())) for p in (tmp_path / "sw").iterdir() if p.is_file()]
    assert bob.post("/api/apps", data={"name": "sw"}, files=files, headers=H).status_code == 200
    assert bob.get("/api/apps/sw/state").json()["running"] is False and bob.get("/api/apps").json()[0]["last_active"] is None
    for i in range(2):                                  # the second start resumes the first's record
        r = bob.post("/api/apps/sw/start", json={"passes": 1}, headers=H)
        assert r.status_code == 200 and "resumes" in r.json()["ok"], r.text
        assert bob.post("/api/apps/sw/start", json={"passes": 1}, headers=H).status_code == 409, "running already"
        deadline = time.time() + 240
        while time.time() < deadline and bob.get("/api/apps/sw/state").json()["running"]:
            time.sleep(1)
        st = bob.get("/api/apps/sw/state").json()
        assert st["running"] is False and not st["failed"] and st["events"] and st["campaign"], st
    log = bob.get("/api/apps/sw/log/raw").text
    assert log.count("── started ") == 2 and "resumed" in log, "one log, each start marked, the second resumed"
    run = app.state.runs.latest(app.state.store.user(name="bob"), "sw")
    events, _ = read_events(app.state.runs.events_path(run))
    assert any(e["ev"] == "start" for e in events)
    res = bob.get("/api/apps/sw/results").json()
    assert res["rows"] and "time_ms" in res["rows"][0]["metrics"] and res["answer"], res
    assert bob.get("/api/apps/sw/report").status_code == 200
    sm = bob.get("/api/apps").json()[0]["summary"]                  # D693: the list's line
    assert sm["designs"] == len(res["designs"]) > 0 and sm["accepted"] == res["counts"]["accepted"], sm
    pre = bob.get("/api/apps/sw/preflight").json()
    assert pre["changed"] is False and pre["options"]["passes"] == 1, "the last start's inputs and options"
    ada = _client(app, "ada", "correct horse battery")
    assert ada.get("/api/apps/sw/state", params={"owner": "bob"}).status_code == 200, "an admin reads every loop"
    assert any(a["owner"] == "bob" and a["name"] == "sw" for a in ada.get("/api/admin/apps").json())
    assert bob.post("/api/apps/sw/stop", json={"now": True}, headers=H).json()["ok"] == "not running"


def test_notes_reach_a_running_loop_through_its_inbox(server):
    import json as _json

    app, _ = server
    bob = _client(app, "bob", "another long secret")
    bob.post("/api/apps", data={"name": "x"}, files=[("files", ("x.problem.yaml", b"statement: s\n"))], headers=H)
    assert bob.post("/api/apps/x/notes", json={"text": "early"}, headers=H).status_code == 409, "not running"
    proc, rid, d = _fake_start(app, "bob", "x")
    try:
        assert bob.post("/api/apps/x/notes", json={"text": "try carry-select"}, headers=H).status_code == 200
        line = _json.loads((d / "runs" / "inbox.jsonl").read_text())
        assert line["text"] == "try carry-select" and line["by"] == "bob"
        assert [n["text"] for n in bob.get("/api/apps/x/notes").json()] == ["try carry-select"]
        ada = _client(app, "ada", "correct horse battery")
        assert ada.post("/api/apps/x/notes", json={"text": "x"}, headers=H).status_code == 404, "only the owner steers"
    finally:
        proc.kill()
        proc.wait()


def test_a_user_can_neither_see_nor_use_another_users_loops(server):
    """Loops are per user (D687): every route that names another user's loop answers as if it did
    not exist, or refuses; only an admin reads them, and may stop them."""
    app, _ = server
    store = app.state.store
    store.add_user("cy", "a third long secret")
    bob = _client(app, "bob", "another long secret")
    bob.post("/api/apps", data={"name": "x"}, files=[("files", ("x.problem.yaml", b"statement: s\n"))], headers=H)
    proc, _rid, _d = _fake_start(app, "bob", "x")
    try:
        cy = _client(app, "cy", "a third long secret")
        assert cy.get("/api/apps").json() == [] and cy.get("/api/loops").json() == []
        for path in ("/api/apps/x", "/api/apps/x/files", "/api/apps/x/document", "/api/apps/x/state", "/api/apps/x/turns",
                     "/api/apps/x/results", "/api/apps/x/report", "/api/apps/x/notes", "/api/apps/x/log/raw", "/api/apps/x/workbench"):
            assert cy.get(path).status_code in (400, 404), path
            assert cy.get(path, params={"owner": "bob"}).status_code == 403, path
        assert cy.get("/api/apps/x/file", params={"path": "x.problem.yaml"}).status_code == 400
        for method, path, kw in (("post", "/api/apps/x/start", {"json": {"passes": 1}}), ("post", "/api/apps/x/check", {}),
                                 ("post", "/api/apps/x/stop", {"json": {"now": True}}), ("post", "/api/apps/x/notes", {"json": {"text": "hi"}}),
                                 ("put", "/api/apps/x/document", {"json": {"text": ""}}),
                                 ("put", "/api/apps/x/file", {"params": {"path": "x.problem.yaml"}, "json": {"text": "id: y"}}),
                                 ("delete", "/api/apps/x", {})):
            r = getattr(cy, method)(path, headers=H, **kw)
            assert r.status_code in (400, 404), (path, r.status_code)
        assert cy.post("/api/apps/x/stop", params={"owner": "bob"}, json={"now": True}, headers=H).status_code == 403
        assert bob.get("/api/apps/x/state").json()["running"] is True, "untouched"
        ada = _client(app, "ada", "correct horse battery")
        assert ada.get("/api/apps/x/state", params={"owner": "bob"}).json()["running"] is True
        assert ada.put("/api/apps/x/file", params={"path": "notes.txt", "owner": "bob"}, json={"text": "z"},
                       headers=H).status_code == 200, "an admin edits anyone's loop too (D812)"
        assert ada.post("/api/apps/x/stop", params={"owner": "bob"}, json={"now": True}, headers=H).status_code == 200
    finally:
        proc.kill()
        proc.wait()


def test_the_workbench_the_log_download_and_an_open_question(server):
    """D688-D689: the workbench listed with first lines; the loop's whole log as a file; an
    agent's question in the loop's state until it is answered or its time is up."""
    import json as _json
    import time as _time

    app, _ = server
    bob = _client(app, "bob", "another long secret")
    bob.post("/api/apps", data={"name": "x"}, files=[("files", ("x.problem.yaml", b"statement: s\n")),
                                                     ("files", ("workbench/notes/adders.md", b"# Carry-select wins above 3 GHz\n")),
                                                     ("files", ("workbench/tools/fit.py", b'"""Fit a cubic per segment."""\n'))], headers=H)
    wb = bob.get("/api/apps/x/workbench").json()
    assert {(w["kind"], w["first"]) for w in wb} == {("notes", "Carry-select wins above 3 GHz"), ("tools", "Fit a cubic per segment.")}
    proc, _rid, d = _fake_start(app, "bob", "x")
    try:
        rdir = d / "rundir"
        rdir.mkdir()
        (d / "out").mkdir(exist_ok=True)
        (d / "out" / "x.db.runs.json").write_text(_json.dumps({"x": str(rdir)}))
        r = bob.get("/api/apps/x/log/raw")
        assert r.text == "line one\nERROR two\n" and "attachment" in r.headers["content-disposition"]
        assert bob.get("/api/apps/x/state").json()["question"] is None
        q = {"question": "Ripple or carry-select?", "wait_s": 300, "asked": _time.time()}
        (rdir / "events.jsonl").write_text(_json.dumps({"t": _time.time(), "ev": "mark", "name": "question", "why": _json.dumps(q)}) + "\n")
        assert bob.get("/api/apps/x/state").json()["question"]["question"] == "Ripple or carry-select?"
        assert bob.get("/api/loops").json()[0]["question"], "what the notifications watch"
        bob.post("/api/apps/x/notes", json={"text": "carry-select"}, headers=H)
        assert bob.get("/api/apps/x/state").json()["question"] is None, "answered"
    finally:
        proc.kill()
        proc.wait()


def test_a_name_typed_on_a_phone_is_the_same_name(server):
    """D699: a phone capitalises the first letter and puts a space after a word: the name is the
    same; the password is as typed; a name differing only in case cannot be added."""
    app, _ = server
    for typed in ("bob", "Bob", " BOB ", "bob "):
        c = TestClient(app)
        r = c.post("/api/login", json={"name": typed, "password": "another long secret"}, headers=H)
        assert r.status_code == 200 and r.json()["name"] == "bob", typed
    c = TestClient(app)
    assert c.post("/api/login", json={"name": "bob", "password": "another long secret "}, headers=H).status_code == 401
    with pytest.raises(ValueError):
        app.state.store.add_user("BOB", "yet another secret")
    app.state.store.set_user("Bob", password="a changed secret")
    assert app.state.store.login("bob", "a changed secret")


def test_the_turns_are_read_as_they_grow_and_one_from_its_place(server, tmp_path):
    """D779: the turn list parses only what was added since the last look; a line being written
    waits; turn `k` is read from its byte offset, whole."""
    import json as _json

    app, _ = server
    bob = _client(app, "bob", "another long secret")
    bob.post("/api/apps", data={"name": "x"}, files=[("files", ("x.problem.yaml", b"statement: s\n"))], headers=H)
    proc, _rid, d = _fake_start(app, "bob", "x")
    try:
        rundir = tmp_path / "run"
        rundir.mkdir()
        (d / "out").mkdir(exist_ok=True)
        (d / "out" / "x.db.runs.json").write_text(_json.dumps({"c1": str(rundir)}))
        turns = rundir / "turns.jsonl"
        long = "y" * 5000
        turns.write_text("".join(_json.dumps({"kind": "turn", "prompt": f"p{i} " + long, "hops": [1, 2]}) + "\n" for i in range(3)))
        got = bob.get("/api/apps/x/turns").json()
        assert [t["k"] for t in got["turns"]] == [1, 2, 3] and got["total"] == 3
        assert got["turns"][0]["prompt"].endswith("...") and got["turns"][0]["hops"] == 2, "summed up"
        with turns.open("a") as fh:
            fh.write(_json.dumps({"kind": "turn", "prompt": "p3"}) + "\n" + '{"kind": "turn", "prom')   # the last one half written
        got = bob.get("/api/apps/x/turns").json()
        assert [t["k"] for t in got["turns"]] == [1, 2, 3, 4], "the half-written line waits"
        one = bob.get("/api/apps/x/turns?k=2").json()["turns"]
        assert one[0]["k"] == 2 and one[0]["prompt"] == "p1 " + long and one[0]["hops"] == [1, 2], "whole, from its place"
        assert bob.get("/api/apps/x/turns?k=9").json()["turns"] == []
    finally:
        proc.kill()
