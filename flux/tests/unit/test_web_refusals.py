"""D708: the hosts a loop's sandbox refused, in the admin's audit -- each run's proxy appends them
to a file the server names; the server reads what is new into the audit, under the loop's owner."""

from __future__ import annotations

import json
import os

from fastapi.testclient import TestClient

from flux_web import create_app
from flux_web.runs import run_env
from flux_web.store import Store

H = {"X-Flux": "1"}


def _line(app, host, port=443, command="task run"):
    return json.dumps({"t": 1.0, "host": host, "port": port, "app": app, "command": command, "container": "flux-x"}) + "\n"


def test_refusals_reach_the_audit_once_each_a_half_line_waits(tmp_path):
    store = Store(tmp_path / "data")
    f = store.refusals_file
    looked = json.dumps({"t": 1.0, "host": "direct.example", "port": 0, "how": "lookup", "app": "bob.add8", "command": "task run"}) + "\n"
    f.write_text(_line("bob.add8", "evil.example") + _line("dee.sw", "10.0.0.9", 80, "ask") + looked + '{"t": 2, "host": "pa')
    assert store.take_refusals() == 3
    assert ("bob", "network refused", "add8: direct.example (a name lookup, task run)") in [(r["user"], r["action"], r["detail"]) for r in store.audit_log()]
    rows = [(r["user"], r["action"], r["detail"]) for r in store.audit_log()]
    assert ("bob", "network refused", "add8: evil.example:443 (task run)") in rows
    assert ("dee", "network refused", "sw: 10.0.0.9:80 (ask)") in rows
    assert store.take_refusals() == 0, "read once"
    with open(f, "a") as fh:
        fh.write('rt.example", "port": 443, "app": "bob.add8"}\n')
    assert store.take_refusals() == 1 and "add8: part.example:443 (run)" in store.audit_log()[0]["detail"]
    f.unlink()
    f.write_text(_line("bob.add8", "again.example"))                # another file: read from its start
    assert store.take_refusals() == 1


def test_every_run_names_the_file_and_the_admins_audit_reads_it(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    store = Store(tmp_path / "data")
    store.add_user("ada", "correct horse battery", "admin")
    bob = store.add_user("bob", "another long secret")
    assert run_env(store, bob, "add8")["FLUX_SANDBOX_REFUSALS"] == str(store.refusals_file)
    app = create_app(tmp_path / "data", sandbox=False)
    c = TestClient(app)
    assert c.post("/api/login", json={"name": "ada", "password": "correct horse battery"}, headers=H).status_code == 200
    store.refusals_file.write_text(_line("bob.add8", "evil.example"))
    got = c.get("/api/audit").json()
    assert any(x["action"] == "network refused" and x["user"] == "bob" for x in got)
    c2 = TestClient(app)
    assert c2.post("/api/login", json={"name": "bob", "password": "another long secret"}, headers=H).status_code == 200
    assert c2.get("/api/audit").status_code == 403, "the admin's only"
    assert os.stat(store.refusals_file).st_size > 0


def test_the_start_dialog_names_the_admins_hosts_to_an_admin_only(tmp_path, monkeypatch):
    """D716: a user learns the network is limited, not by which hosts."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    store = Store(tmp_path / "data")
    store.add_user("ada", "correct horse battery", "admin")
    store.add_user("bob", "another long secret")
    store.server_set("sandbox", {"network": "allowlist", "allow": ["llm.internal.example", "10.0.0.0/8"], "users_add": False})
    app = create_app(tmp_path / "data", sandbox=False)
    got = {}
    for who, pw in (("ada", "correct horse battery"), ("bob", "another long secret")):
        c = TestClient(app)
        assert c.post("/api/login", json={"name": who, "password": pw}, headers=H).status_code == 200
        files = [("files", ("x.problem.yaml", b"statement: s\n"))]
        assert c.post("/api/apps", data={"name": "x"}, files=files, headers=H).status_code == 200
        got[who] = c.get("/api/apps/x/preflight").json()["network"]
    assert got["ada"]["allow"] == ["llm.internal.example", "10.0.0.0/8"]
    assert "allow" not in got["bob"] and got["bob"]["network"] == "allowlist"
