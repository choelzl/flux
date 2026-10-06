"""D935: the admin's Test of an agent's server configuration counts for every user who inherits it
(the same configuration identity, D923); a user with settings of their own tests their own.
Synthetic values, a stand-in Codex: no provider is contacted."""

from __future__ import annotations

import os
import sys

import pytest
from fastapi.testclient import TestClient

from flux_web import create_app
from flux_web.store import Store

H = {"X-Flux": "1"}
# answers only with the server's key
FAKE = (f"#!{sys.executable}\nimport json, os, sys\na = sys.argv[1:]\n"
        "if a[:1] == ['--version']: print('codex-cli 0.0-test')\n"
        "elif a[:2] == ['login', 'status']: print('key ' + ('present' if os.environ.get('OPENAI_API_KEY') else 'absent'))\n"
        "else:\n    sys.stdin.read()\n"
        "    ok = os.environ.get('OPENAI_API_KEY') == 'synthetic-server-key'\n"
        "    print(json.dumps({'type': 'thread.started', 'thread_id': 't'}))\n"
        "    print(json.dumps({'type': 'item.completed', 'item': {'id': 'i', 'type': 'agent_message', 'text': 'FLUX-OK' if ok else 'no key'}}))\n")
DOC = b"statement: s\nlanguage: python\nflow: {generate: {by: codex}, test: {test: ['true']}}\n"


@pytest.fixture()
def server(tmp_path, monkeypatch):
    for k in list(os.environ):
        if k.startswith(("OPENAI_", "ANTHROPIC_", "FLUX_CODEX_", "FLUX_CLAUDE_", "FLUX_OPENCODE_", "OPENCODE_", "CODEX_")):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    fake = tmp_path / "codex"
    fake.write_text(FAKE)
    fake.chmod(0o755)
    monkeypatch.setenv("FLUX_CODEX_BIN", str(fake))
    store = Store(tmp_path / "data")
    store.add_user("ada", "ada has a long secret", "admin")
    store.add_user("ian", "ian has a long secret", "internal")
    store.add_user("bob", "bob has a long secret", "internal")
    app = create_app(tmp_path / "data", sandbox=False)
    out = {}
    for name in ("ada", "ian", "bob"):
        c = TestClient(app)
        assert c.post("/api/login", json={"name": name, "password": f"{name} has a long secret"}, headers=H).status_code == 200
        c.post("/api/apps", data={"name": "x"}, files=[("files", ("x.problem.yaml", DOC))], headers=H)
        out[name] = c
    return out, store


def _codex(c, path="/api/logins"):
    got = c.get(path).json()
    return next(a for a in got["agents"] if a["id"] == "codex")


def test_the_admins_test_of_the_server_configuration_counts_for_everyone_who_inherits_it(server):
    cs, store = server
    ada, ian, bob = cs["ada"], cs["ian"], cs["bob"]
    store.set_server_setting("FLUX_CODEX_API_KEY", "synthetic-server-key")
    assert ian.post("/api/admin/agents/codex/test", headers=H).status_code == 403, "the admin's alone"
    assert _codex(ada, "/api/admin/agents")["server_test"]["state"] == "untested"
    r = ian.post("/api/apps/x/start", json={"passes": 1}, headers=H)
    assert r.status_code == 409 and "not set up for ian" in r.json()["detail"], "not tested by anyone yet"
    got = ada.post("/api/admin/agents/codex/test", headers=H).json()
    assert got["ok"] and got["shared"] and got["identity"], got
    st = _codex(ada, "/api/admin/agents")["server_test"]
    assert st["state"] == "ready" and st["shared"], st
    v = _codex(ian)["verified"]
    assert v["state"] == "ready" and v["by"] == "admin", "ian has no settings of his own: the admin's Test counts"
    assert "set up" not in ian.post("/api/apps/x/start", json={"passes": 1}, headers=H).text
    users = {u["user"]: u for u in _codex(ada, "/api/admin/agents")["users"]}
    assert users["ian"]["state"] == "ready" and users["ian"]["by"] == "admin"
    # bob overrides: his own configuration, his own Test
    store.set_setting(store.user(name="bob"), "FLUX_CODEX_API_KEY", "synthetic-bob-key")
    assert _codex(bob)["verified"]["state"] == "untested"
    r = bob.post("/api/apps/x/start", json={"passes": 1}, headers=H)
    assert r.status_code == 409 and "not set up for bob" in r.json()["detail"]
    # the server's configuration changes: the admin's Test no longer counts, for anyone
    store.set_server_setting("FLUX_CODEX_API_KEY", "synthetic-other-key")
    assert _codex(ada, "/api/admin/agents")["server_test"]["state"] == "changed"
    assert _codex(ian)["verified"]["state"] != "ready"
    r = ian.post("/api/apps/x/start", json={"passes": 1}, headers=H)
    assert r.status_code == 409, r.text
    assert "synthetic" not in ada.get("/api/admin/agents").text and "synthetic" not in ian.get("/api/logins").text


def test_a_server_test_without_a_server_credential_counts_for_nobody_else(server):
    """Each user's own login (no key or endpoint from the server): one Test cannot speak for all."""
    cs, store = server
    got = cs["ada"].post("/api/admin/agents/codex/test", headers=H).json()
    assert not got["shared"]
    store.server_set("agent-test-server:codex", {**got, "ok": True})        # even passed
    assert _codex(cs["ian"])["verified"]["state"] == "untested"
