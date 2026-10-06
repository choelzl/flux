"""D923: readiness is what runs -- one effective environment for the detection, the Test and the
turn; a Test in a loop's context (its variables) satisfies that loop's gate; a stored Test carries
the identity of the configuration it tested and stops counting when it changes. Synthetic values,
a stand-in Codex: no provider is contacted."""

from __future__ import annotations

import json
import os
import sys

import pytest
from fastapi.testclient import TestClient

from flux_web import create_app
from flux_web.runs import run_env
from flux_web.store import Store

H = {"X-Flux": "1"}
# answers only with the loop's key -- what reached it is what the turn would get
FAKE = (f"#!{sys.executable}\nimport json, os, sys\na = sys.argv[1:]\n"
        "if a[:1] == ['--version']: print('codex-cli 0.0-test')\n"
        "elif a[:2] == ['login', 'status']: print('key ' + ('present' if os.environ.get('OPENAI_API_KEY') else 'absent'))\n"
        "else:\n    sys.stdin.read()\n"
        "    ok = os.environ.get('OPENAI_API_KEY') == 'synthetic-loop-key'\n"
        "    print(json.dumps({'type': 'thread.started', 'thread_id': 't'}))\n"
        "    print(json.dumps({'type': 'item.completed', 'item': {'id': 'i', 'type': 'agent_message', 'text': 'FLUX-OK' if ok else 'no key'}}))\n")


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
    store.add_user("ian", "ian has a long secret", "internal")
    app = create_app(tmp_path / "data", sandbox=False)
    c = TestClient(app)
    assert c.post("/api/login", json={"name": "ian", "password": "ian has a long secret"}, headers=H).status_code == 200
    c.post("/api/apps", data={"name": "x"}, files=[("files", ("x.problem.yaml",
           b"statement: s\nlanguage: python\nflow: {generate: {by: codex}, test: {test: ['true']}}\n"))], headers=H)
    return c, store


def test_a_loop_only_key_passes_the_loops_gate_with_a_test_in_its_context(server):
    c, store = server
    assert c.put("/api/apps/x/env", json={"name": "OPENAI_API_KEY", "value": "synthetic-loop-key", "secret": True}, headers=H).status_code == 200
    got = c.post("/api/agents/codex/test", headers=H).json()
    assert not got["ok"] and got["identity"] and got["loop"] == "", "the account's context: no key there"
    conn = next(s for s in got["steps"] if s["step"] == "connection")
    assert "asking it anyway" in conn["said"], "nothing detected is not 'cannot work': a live Test asks"
    r = c.post("/api/apps/x/start", json={"passes": 1}, headers=H)
    assert r.status_code == 409 and "Codex not set up for ian" in r.json()["detail"]
    codex = next(a for a in c.get("/api/logins").json()["agents"] if a["id"] == "codex")
    assert codex["verified"]["state"] == "failed" and [x["loop"] for x in codex["loops"]] == ["x"], "the loop runs it otherwise: said"
    got = c.post("/api/agents/codex/test?loop=x", headers=H).json()
    assert got["ok"] and got["loop"] == "x", got
    status = next(s for s in got["steps"] if s["step"] == "status")
    assert "key present" in status["said"], "A2: its status asked with the environment its turn gets"
    conn = next(s for s in got["steps"] if s["step"] == "connection")
    assert "OPENAI_API_KEY" in conn["said"] and "synthetic-loop-key" not in json.dumps(got)
    r = c.post("/api/apps/x/start", json={"passes": 1}, headers=H)
    assert "set up" not in r.text, "the loop's own Test counts for the loop"
    codex = next(a for a in c.get("/api/logins").json()["agents"] if a["id"] == "codex")
    assert codex["loops"][0]["state"] == "ready"
    # A4: the configuration changes -> the Test no longer counts, said as such
    assert c.put("/api/apps/x/env", json={"name": "OPENAI_API_KEY", "value": "synthetic-other-key", "secret": True}, headers=H).status_code == 200
    r = c.post("/api/apps/x/start", json={"passes": 1}, headers=H)
    assert r.status_code == 409 and "changed since its Test" in r.json()["detail"], r.text
    codex = next(a for a in c.get("/api/logins").json()["agents"] if a["id"] == "codex")
    assert codex["loops"][0]["state"] == "changed"


def test_a_changed_endpoint_is_changed_since_the_test(server):
    c, store = server
    ian = store.user(name="ian")
    store.set_setting(ian, "FLUX_CODEX_API_KEY", "synthetic-account-key")
    got = c.post("/api/agents/codex/test", headers=H).json()
    assert got["identity"]
    store.server_set("agent-test:ian:codex", {**got, "ok": True})           # as a passed Test of this configuration
    codex = next(a for a in c.get("/api/logins").json()["agents"] if a["id"] == "codex")
    assert codex["verified"]["state"] == "ready" and codex["connection"]["mechanism"] == "key"
    assert codex["connection"]["source"] == "yours" and codex["fields"]["key"] == {"source": "yours", "name": "FLUX_CODEX_API_KEY"}
    store.set_setting(ian, "FLUX_CODEX_BASE_URL", "https://other.invalid/v1")
    codex = next(a for a in c.get("/api/logins").json()["agents"] if a["id"] == "codex")
    assert codex["verified"]["state"] == "changed", "A4: a new endpoint is not what was tested"
    assert "other.invalid" not in c.get("/api/logins").text and "synthetic-account-key" not in c.get("/api/logins").text


def test_this_loops_variables_win_over_an_accounts_agent_key(server):
    """A3: the precedence the loop's Settings say is the one that runs."""
    c, store = server
    ian = store.user(name="ian")
    store.set_setting(ian, "FLUX_CODEX_API_KEY", "synthetic-account-key")
    c.put("/api/apps/x/env", json={"name": "OPENAI_API_KEY", "value": "synthetic-loop-key", "secret": True}, headers=H)
    pack = json.loads(run_env(store, ian, "x")["FLUX_CODEX_ENV"])
    assert pack["OPENAI_API_KEY"] == "synthetic-loop-key"
    assert json.loads(run_env(store, ian)["FLUX_CODEX_ENV"])["OPENAI_API_KEY"] == "synthetic-account-key"


def test_opencode_accepts_a_provider_key_it_will_forward(tmp_path, monkeypatch):
    """A2: a provider's key given to OpenCode on purpose (its own set, a variable for every agent) is a connection."""
    from flux_loop.agent_check import check_agent

    fake = tmp_path / "opencode"
    fake.write_text("#!/bin/sh\necho opencode 1.0\n")
    fake.chmod(0o755)
    env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path), "FLUX_OPENCODE_BIN": str(fake)}
    got = check_agent("opencode", env={**env, "ANTHROPIC_API_KEY": "synthetic", "FLUX_SHARED_VARS": "ANTHROPIC_API_KEY"})
    assert got["ok"] and got["connection"]["mechanism"] == "key", got
    got = check_agent("opencode", env={**env, "ANTHROPIC_API_KEY": "synthetic"})
    assert not got["ok"], "another kind's key, not given to it: not its connection"


def test_the_authoring_picker_says_installed_and_whether_its_connection_was_tested(server):
    """D924 (A6): /api/agents offers an installed agent with its verification -- untested, then failed --
    so the picker says so before a draft is spent; it is not disabled for it."""
    c, store = server
    codex = next(a for a in c.get("/api/agents").json() if a["id"] == "codex")
    assert codex["available"] and codex["verified"] == "untested" and codex["connection"] == "none"
    c.post("/api/agents/codex/test", headers=H)
    codex = next(a for a in c.get("/api/agents").json() if a["id"] == "codex")
    assert codex["available"] and codex["verified"] == "failed"
