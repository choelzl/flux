"""D696: the model settings of runs, set by the admin for the server and by each user for
themselves, per group (Flux's own model, and each agent's own, D807); what reaches a run; and
the loop's own files the configurator edits."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from flux_web import create_app
from flux_web.runs import run_env
from flux_web.store import Store

H = {"X-Flux": "1"}


@pytest.fixture()
def server(tmp_path, monkeypatch):
    for k in ("FLUX_REMOTE_BASE_URL", "FLUX_REMOTE_MODEL", "FLUX_REMOTE_API_KEY", "FLUX_REMOTE_API_KEY_FILE", "OPENCODE_CONFIG_CONTENT",
              "FLUX_CLAUDE_ARGS", "FLUX_CODEX_ARGS", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "FLUX_LLM_REMOTE"):
        monkeypatch.delenv(k, raising=False)
    from web_agents import install

    install(monkeypatch, tmp_path)                                  # D807: offered where installed
    store = Store(tmp_path / "data")
    store.add_user("ada", "correct horse battery", "admin")
    store.add_user("bob", "another long secret")
    return create_app(tmp_path / "data", sandbox=False), store


def _client(app, name, password):
    c = TestClient(app)
    assert c.post("/api/login", json={"name": name, "password": password}, headers=H).status_code == 200
    return c


def own(env, agent):
    """What a run hands one agent alone (D807)."""
    return json.loads(env.get(f"FLUX_{agent.upper()}_ENV") or "{}")


def test_a_run_gets_the_servers_models_unless_its_user_names_their_own(server, monkeypatch):
    app, store = server
    ada, bob = _client(app, "ada", "correct horse battery"), _client(app, "bob", "another long secret")
    assert bob.put("/api/admin/settings", json={"values": {"FLUX_REMOTE_MODEL": "m"}}, headers=H).status_code == 403
    r = ada.put("/api/admin/settings", json={"values": {"FLUX_REMOTE_BASE_URL": "https://llm.example/v1", "FLUX_REMOTE_MODEL": "qwen",
                                                         "FLUX_REMOTE_API_KEY": "server-key", "FLUX_CLAUDE_MODEL": "opus",
                                                         "FLUX_CLAUDE_API_KEY": "sk-server", "FLUX_CODEX_MODEL": "gpt-x"}}, headers=H)
    assert r.status_code == 200 and r.json()["values"]["FLUX_REMOTE_API_KEY"] == "set", "a key is never sent back"
    assert "server-key" not in ada.get("/api/admin/settings").text and "server-key" not in bob.get("/api/settings").text
    seen = bob.get("/api/settings").json()
    assert seen["server"]["FLUX_REMOTE_MODEL"] == "qwen" and [g["id"] for g in seen["groups"]] == ["model", "agent", "opencode", "claude", "codex", "other"]
    assert ada.put("/api/admin/settings", json={"values": {"FLUX_CODEX_BASE_URL": "not a url"}}, headers=H).status_code == 400
    assert ada.put("/api/admin/settings", json={"values": {"ANTHROPIC_API_KEY": "x"}}, headers=H).status_code == 400, \
        "an agent's key is its own setting (or a variable)"
    monkeypatch.setenv("FLUX_REMOTE_API_KEY_FILE", "/srv/flux/key")
    bob_u = store.user(name="bob")
    env = run_env(store, bob_u)
    assert env["FLUX_REMOTE_BASE_URL"] == "https://llm.example/v1" and env["FLUX_REMOTE_API_KEY"] == "server-key"
    assert env["FLUX_LLM_REMOTE"] == "1" and "FLUX_REMOTE_API_KEY_FILE" not in env, "the web's key wins over the file"
    oc = json.loads(own(env, "opencode")["OPENCODE_CONFIG_CONTENT"])
    assert oc["model"] == "flux/qwen" and oc["provider"]["flux"]["options"]["baseURL"] == "https://llm.example/v1"
    assert oc["provider"]["flux"]["options"]["apiKey"] == "{env:FLUX_AGENT_API_KEY}" and own(env, "opencode")["FLUX_AGENT_API_KEY"] == "server-key", \
        "OpenCode: Flux's model when it has none of its own; the key by variable, not in the config"
    assert env["FLUX_CLAUDE_ARGS"] == "--model opus" and env["FLUX_CODEX_ARGS"] == "--model gpt-x"
    assert own(env, "claude")["ANTHROPIC_API_KEY"] == "sk-server" and "ANTHROPIC_API_KEY" not in env, "Claude Code's alone"
    assert "ANTHROPIC_API_KEY" not in own(env, "opencode") and "OPENCODE_CONFIG_CONTENT" not in env
    # bob names his own endpoint for Flux's model: none of the server's values of that group
    assert bob.put("/api/settings", json={"values": {"FLUX_REMOTE_BASE_URL": "https://mine.example/v1", "FLUX_REMOTE_MODEL": "small"}}, headers=H).status_code == 200
    env = run_env(store, bob_u)
    assert env["FLUX_REMOTE_BASE_URL"] == "https://mine.example/v1" and "FLUX_REMOTE_API_KEY" not in env and "FLUX_REMOTE_API_KEY_FILE" not in env
    assert own(env, "claude")["ANTHROPIC_API_KEY"] == "sk-server", "another group keeps the server's"
    oc = json.loads(own(env, "opencode")["OPENCODE_CONFIG_CONTENT"])
    assert oc["model"] == "flux/small" and "apiKey" not in oc["provider"]["flux"]["options"]
    # OpenCode's own settings over Flux's model's
    store.set_server_setting("FLUX_OPENCODE_BASE_URL", "https://oc.example/v1")
    store.set_server_setting("FLUX_OPENCODE_MODEL", "coder")
    store.set_server_setting("FLUX_OPENCODE_API_KEY", "oc-key")
    env = run_env(store, store.user(name="ada"))
    oc = json.loads(own(env, "opencode")["OPENCODE_CONFIG_CONTENT"])
    assert oc["model"] == "flux/coder" and oc["provider"]["flux"]["options"]["baseURL"] == "https://oc.example/v1"
    assert own(env, "opencode")["FLUX_AGENT_API_KEY"] == "oc-key"


def test_each_agent_has_its_own_settings_and_variables_and_an_added_one_its_kind(server, tmp_path):
    """D807: an admin adds `nga`, an OpenCode of its own, by its program; it has a tab of its own
    -- endpoint, model, key -- and variables of its own, the server's and a user's; one variable
    for every agent is an ordinary variable. An agent whose program is not found is not offered."""
    app, store = server
    ada, bob = _client(app, "ada", "correct horse battery"), _client(app, "bob", "another long secret")
    prog = tmp_path / "corp" / "nga"
    prog.parent.mkdir()
    prog.write_text("#!/bin/sh\necho nga 3\n")
    prog.chmod(0o755)
    assert ada.post("/api/admin/agents", json={"name": "model", "kind": "opencode"}, headers=H).status_code == 400
    assert ada.post("/api/admin/agents", json={"name": "Nga", "kind": "opencode"}, headers=H).status_code == 400
    assert ada.post("/api/admin/agents", json={"name": "nga", "kind": "cursor"}, headers=H).status_code == 400
    assert bob.post("/api/admin/agents", json={"name": "nga", "kind": "opencode"}, headers=H).status_code == 403
    assert ada.post("/api/admin/agents", json={"name": "ghost", "kind": "claude"}, headers=H).status_code == 200
    assert ada.post("/api/admin/agents", json={"name": "nga", "kind": "opencode", "label": "NGA", "bin": str(prog)},
                    headers=H).status_code == 200
    assert ada.post("/api/admin/agents", json={"name": "nga", "kind": "opencode"}, headers=H).status_code == 400, "once"
    listed = {a["id"]: a for a in ada.get("/api/admin/agents").json()["agents"]}
    assert listed["nga"]["found"] == str(prog) and listed["ghost"]["found"] == "", "the admin sees every agent"
    offered = [a["id"] for a in bob.get("/api/agents").json()]
    assert offered == ["opencode", "claude", "codex", "nga", "model"], "ghost has no program: not offered"
    assert [a["id"] for a in bob.get("/api/logins").json()["agents"]] == ["opencode", "claude", "codex", "nga"]
    seen = bob.get("/api/settings").json()
    assert [g["id"] for g in seen["groups"]] == ["model", "agent", "opencode", "claude", "codex", "nga", "other"]
    assert ada.put("/api/admin/settings", json={"values": {"FLUX_NGA_BASE_URL": "https://corp.example/v1", "FLUX_NGA_MODEL": "big",
                                                           "FLUX_NGA_API_KEY": "corp-key"}}, headers=H).status_code == 200
    assert ada.put("/api/admin/agents/nga/env", json={"name": "NGA_REGION", "value": "eu"}, headers=H).status_code == 200
    assert bob.put("/api/agents/nga/env", json={"name": "ANTHROPIC_API_KEY", "value": "for-nga", "secret": True}, headers=H).status_code == 200
    assert bob.put("/api/agents/nga/env", json={"name": "OPENCODE_CONFIG_CONTENT", "value": "{}"}, headers=H).status_code == 200, \
        "an agent's own configuration is its own variable"
    assert bob.put("/api/env", json={"name": "OPENCODE_CONFIG_CONTENT", "value": "{}"}, headers=H).status_code == 400, "not everyone's"
    assert bob.put("/api/agents/nga/env", json={"name": "FLUX_NGA_ENV", "value": "x"}, headers=H).status_code == 400
    assert bob.put("/api/env", json={"name": "SHARED_KEY", "value": "everyone"}, headers=H).status_code == 200
    seen = bob.get("/api/settings").json()
    assert [x["name"] for x in seen["agent_env"]["nga"]["mine"]] == ["ANTHROPIC_API_KEY", "OPENCODE_CONFIG_CONTENT"]
    assert seen["agent_env"]["nga"]["server"] == [{"name": "NGA_REGION", "value": "eu", "secret": False}]
    assert "for-nga" not in bob.get("/api/settings").text and "corp-key" not in bob.get("/api/settings").text
    env = run_env(store, store.user(name="bob"))
    nga = own(env, "nga")
    assert json.loads(env["FLUX_AGENTS"]) == {"nga": "opencode"} and env["FLUX_NGA_BIN"] == str(prog)
    assert nga["NGA_REGION"] == "eu" and nga["ANTHROPIC_API_KEY"] == "for-nga" and nga["OPENCODE_CONFIG_CONTENT"] == "{}"
    assert "for-nga" not in env.get("FLUX_OPENCODE_ENV", "") and "ANTHROPIC_API_KEY" not in env
    assert env["SHARED_KEY"] == "everyone" and env["FLUX_SHARED_VARS"] == "SHARED_KEY"
    assert bob.put("/api/agents/nga/env", json={"name": "OPENCODE_CONFIG_CONTENT", "value": None}, headers=H).status_code == 200
    oc = json.loads(own(run_env(store, store.user(name="bob")), "nga")["OPENCODE_CONFIG_CONTENT"])
    assert oc["model"] == "flux/big" and oc["provider"]["flux"]["options"]["baseURL"] == "https://corp.example/v1"
    assert ada.delete("/api/admin/agents/claude", headers=H).status_code == 409, "a built-in agent stays"
    assert ada.delete("/api/admin/agents/nga", headers=H).status_code == 200
    assert "FLUX_NGA_ENV" not in run_env(store, store.user(name="bob")) and "FLUX_NGA_BASE_URL" not in store.server_settings()
    assert [a["id"] for a in bob.get("/api/agents").json()] == ["opencode", "claude", "codex", "model"]


def test_without_web_settings_a_run_keeps_the_machines_own(server, monkeypatch):
    app, store = server
    monkeypatch.setenv("FLUX_REMOTE_API_KEY_FILE", "/srv/flux/key")
    env = run_env(store, store.user(name="bob"))
    assert env["FLUX_REMOTE_API_KEY_FILE"] == "/srv/flux/key" and "OPENCODE_CONFIG_CONTENT" not in env and "FLUX_CLAUDE_ARGS" not in env


def test_the_configurator_lists_edits_and_deletes_the_loops_own_files(server):
    app, _store = server
    bob = _client(app, "bob", "another long secret")
    files = [("files", ("x.problem.yaml", b"statement: s\n")), ("files", ("check.py", b"print(1)\n")), ("files", ("lib/util.py", b"u\n"))]
    assert bob.post("/api/apps", data={"name": "x"}, files=files, headers=H).status_code == 200
    assert bob.put("/api/apps/x/file", params={"path": "bench.sh"}, json={"text": "echo t=1\n"}, headers=H).status_code == 200
    got = {f["path"]: f["document"] for f in bob.get("/api/apps/x/inputs").json()}
    assert got == {"x.problem.yaml": True, "check.py": False, "lib/util.py": False, "bench.sh": False}
    assert bob.delete("/api/apps/x/file", params={"path": "lib/util.py"}, headers=H).status_code == 200
    assert "lib/util.py" not in {f["path"] for f in bob.get("/api/apps/x/inputs").json()}
    assert bob.delete("/api/apps/x/file", params={"path": "x.problem.yaml"}, headers=H).status_code == 400, "not the document"
    assert bob.delete("/api/apps/x/file", params={"path": "../../x"}, headers=H).status_code == 400
    assert bob.delete("/api/apps/x/file", params={"path": "runs/loop.log"}, headers=H).status_code == 400
