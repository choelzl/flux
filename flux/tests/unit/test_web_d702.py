"""D702: the login lockout per name and address, notices of sharing, leaving a shared loop, shared
loops in what the bell watches, and the coding agents told their model by the web's settings."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from flux_web import create_app
from flux_web.runs import run_env
from flux_web.store import Store

H = {"X-Flux": "1"}


@pytest.fixture()
def server(tmp_path, monkeypatch):
    for k in ("FLUX_CLAUDE_ARGS", "FLUX_CODEX_ARGS", "FLUX_CLAUDE_BIN", "FLUX_CODEX_BIN"):
        monkeypatch.delenv(k, raising=False)
    store = Store(tmp_path / "data")
    for n in ("ada", "bob", "cy"):
        store.add_user(n, f"{n} has a long secret", "admin" if n == "ada" else "user")
    return create_app(tmp_path / "data", sandbox=False), store


def _c(app, n):
    c = TestClient(app)
    assert c.post("/api/login", json={"name": n, "password": f"{n} has a long secret"}, headers=H).status_code == 200
    return c


def test_failures_from_one_place_do_not_lock_the_name_everywhere(server):
    _app, store = server
    for _ in range(5):
        assert store.login("bob", "wrong", "10.0.0.66") is None
    assert store.login("bob", "bob has a long secret", "10.0.0.66") is None, "locked from that address"
    assert store.login("bob", "bob has a long secret", "10.0.0.7"), "not from another"
    for i in range(store.LOCK_FROM_ALL):
        store.login("cy", "wrong", f"10.1.{i}.1")
    assert store.login("cy", "cy has a long secret", "10.9.9.9") is None, "fifty from everywhere lock it everywhere"


def test_sharing_tells_the_user_who_may_leave_and_the_bell_watches_shared_loops(server):
    app, store = server
    bob, cy = _c(app, "bob"), _c(app, "cy")
    bob.post("/api/apps", data={"name": "x"}, files=[("files", ("x.problem.yaml", b"statement: s\n"))], headers=H)
    bob.put("/api/apps/x/shares", json={"user": "cy", "perm": "watch"}, headers=H)
    bob.put("/api/apps/x/shares", json={"user": "cy", "perm": "watch"}, headers=H)       # the same again: no second notice
    told = cy.get("/api/notices").json()
    assert [n["text"] for n in told] == ["bob shared x with you to watch"] and told[0]["href"] == "#/u/bob/app/x"
    assert cy.get("/api/notices").json() == [], "a notice is told once"
    assert {(l.get("owner"), l["app"]) for l in cy.get("/api/loops").json()} == {("bob", "x")}
    assert cy.delete("/api/apps/x/shares/me", params={"owner": "bob"}, headers=H).status_code == 200
    assert store.shares("bob", "x") == {} and [n["text"] for n in bob.get("/api/notices").json()] == ["cy left x"]
    assert cy.delete("/api/apps/x/shares/me", params={"owner": "bob"}, headers=H).status_code == 404
    bob.put("/api/apps/x/shares", json={"user": "cy", "perm": "edit"}, headers=H)
    bob.put("/api/apps/x/shares", json={"user": "cy", "perm": None}, headers=H)
    assert [n["text"] for n in cy.get("/api/notices").json()] == ["bob shared x with you to edit", "bob stopped sharing x with you"]


def test_claude_code_and_codex_are_launched_with_the_webs_model(server, monkeypatch):
    """Neither is installed here: what a run would launch is checked, from the web's settings to the argv."""
    from flux_loop.agent import agent_spec

    _app, store = server
    from web_agents import install

    install(monkeypatch, store.data.parent)                        # D807: offered where installed
    store.set_server_setting("FLUX_CLAUDE_MODEL", "claude-sonnet-5")
    store.set_server_setting("FLUX_CODEX_MODEL", "gpt-x")
    env = run_env(store, store.user(name="bob"))
    for k in ("FLUX_CLAUDE_ARGS", "FLUX_CODEX_ARGS", "FLUX_CLAUDE_BIN", "FLUX_CODEX_BIN"):
        monkeypatch.setenv(k, env[k])
    claude = agent_spec("claude").argv
    codex = agent_spec("codex").argv
    assert claude[claude.index("--model") + 1] == "claude-sonnet-5" and claude.index("--model") < len(claude) - 1
    assert codex[codex.index("--model") + 1] == "gpt-x" and codex[-1] == "-", "before the prompt read from stdin"
