"""D945: an added agent renamed -- its settings (the server's and every user's, a key still
decrypting), variables, tests and a default agent naming it move to the new name; the loops whose
documents name it are listed first and are not rewritten; a built-in agent keeps its name."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from flux_web import create_app
from flux_web.store import Store

H = {"X-Flux": "1"}

DOC = ("statement: make x\nlanguage: python\nflow:\n  generate: nga\n  test:\n    test: '{python} {home}/check.py {artifact}'\n"
       "  measure:\n    bench: {command: '{python} {home}/bench.py {artifact}', metrics: [time_ms]}\n"
       "objectives:\n  - {metric: time_ms, direction: minimize}\n")


@pytest.fixture()
def server(tmp_path, monkeypatch):
    monkeypatch.delenv("FLUX_AGENTS", raising=False)
    from web_agents import install

    install(monkeypatch, tmp_path)
    store = Store(tmp_path / "data")
    store.add_user("ada", "correct horse battery", "admin")
    store.add_user("bob", "another long secret")
    return create_app(tmp_path / "data", sandbox=False), store


def _client(app, name, password):
    c = TestClient(app)
    assert c.post("/api/login", json={"name": name, "password": password}, headers=H).status_code == 200
    return c


def test_an_added_agent_renamed_takes_its_settings_variables_and_tests_along(server, tmp_path):
    app, store = server
    ada, bob = _client(app, "ada", "correct horse battery"), _client(app, "bob", "another long secret")
    prog = tmp_path / "corp" / "nga"
    prog.parent.mkdir()
    prog.write_text("#!/bin/sh\necho nga 3\n")
    prog.chmod(0o755)
    assert ada.post("/api/admin/agents", json={"name": "nga", "kind": "opencode", "label": "NGA", "bin": str(prog)},
                    headers=H).status_code == 200
    files = [("files", ("x.problem.yaml", DOC.encode())), ("files", ("check.py", b"print('0 failing')\n")),
             ("files", ("bench.py", b"print('time_ms=1')\n"))]
    assert bob.post("/api/apps", data={"name": "x"}, files=files, headers=H).status_code == 200
    u = store.user(name="bob")
    store.set_server_setting("FLUX_NGA_MODEL", "big")
    store.set_setting(u, "FLUX_NGA_API_KEY", "sk-bob")
    store.set_setting(u, "FLUX_DEFAULT_AGENT", "nga")
    store.server_set("env:agent:nga", {"A": "1"})
    store.server_set(f"env:agent:nga:user:{u.id}", {"B": "2"})
    store.server_set("agent-test:bob:nga", {"ok": True})

    dry = ada.post("/api/admin/agents/nga/rename", json={"name": "corp", "dry_run": True}, headers=H)
    assert dry.status_code == 200 and dry.json()["loops"] == ["bob/x (x.problem.yaml)"], dry.text
    assert "nga" in (store.server_get("agents") or {}), "a dry run changes nothing"

    r = ada.post("/api/admin/agents/nga/rename", json={"name": "corp"}, headers=H)
    assert r.status_code == 200 and r.json()["loops"] == ["bob/x (x.problem.yaml)"], r.text
    agents = store.server_get("agents")
    assert "corp" in agents and "nga" not in agents
    assert store.server_settings().get("FLUX_CORP_MODEL") == "big" and "FLUX_NGA_MODEL" not in store.server_settings()
    mine = store.settings(u, reveal=True)
    assert mine.get("FLUX_CORP_API_KEY") == "sk-bob", "the key moved and still decrypts"
    assert mine.get("FLUX_DEFAULT_AGENT") == "corp" and "FLUX_NGA_API_KEY" not in mine
    assert store.server_get("env:agent:corp") == {"A": "1"} and store.server_get("env:agent:nga") is None
    assert store.server_get(f"env:agent:corp:user:{u.id}") == {"B": "2"}
    assert store.server_get("agent-test:bob:corp") == {"ok": True} and store.server_get("agent-test:bob:nga") is None
    assert "nga" in (tmp_path / "data" / "users" / "bob" / "apps" / "x" / "x.problem.yaml").read_text(), "documents are not rewritten"
    assert bob.get("/api/apps/x/document").json().get("error"), "the loop naming nga says so"
    assert any(e["action"] == "agent renamed" and "nga -> corp" in e["detail"] for e in store.audit_log())


def test_a_rename_refused_for_built_in_unknown_bad_or_taken_names(server, tmp_path):
    app, _store = server
    ada = _client(app, "ada", "correct horse battery")
    for name in ("nga", "acme"):
        assert ada.post("/api/admin/agents", json={"name": name, "kind": "opencode"}, headers=H).status_code == 200
    assert ada.post("/api/admin/agents/codex/rename", json={"name": "cx"}, headers=H).status_code == 409
    assert ada.post("/api/admin/agents/nope/rename", json={"name": "cx"}, headers=H).status_code == 404
    assert ada.post("/api/admin/agents/nga/rename", json={"name": "Bad Name"}, headers=H).status_code == 400
    assert ada.post("/api/admin/agents/nga/rename", json={"name": "acme"}, headers=H).status_code == 400
    bob = _client(app, "bob", "another long secret")
    assert bob.post("/api/admin/agents/nga/rename", json={"name": "cx"}, headers=H).status_code in (401, 403)
