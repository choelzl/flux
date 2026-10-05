"""D697: environment variables of runs -- the server's (admins), a user's, a loop's, in that
order, secrets encrypted, the sandbox's own names refused and the set names passed into it --
and a loop's advanced settings, which only an admin sets."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from flux_web import create_app
from flux_web.runs import advanced, run_env, sandbox_env
from flux_web.store import Store

H = {"X-Flux": "1"}


@pytest.fixture()
def server(tmp_path, monkeypatch):
    from web_agents import install

    install(monkeypatch, tmp_path)                                  # D807: offered where installed
    store = Store(tmp_path / "data")
    store.add_user("ada", "correct horse battery", "admin")
    store.add_user("bob", "another long secret")
    return create_app(tmp_path / "data", sandbox=True), store


def _client(app, name, password):
    c = TestClient(app)
    assert c.post("/api/login", json={"name": name, "password": password}, headers=H).status_code == 200
    return c


def test_variables_come_from_the_server_then_the_user_then_the_loop(server):
    app, store = server
    ada, bob = _client(app, "ada", "correct horse battery"), _client(app, "bob", "another long secret")
    bob.post("/api/apps", data={"name": "x"}, files=[("files", ("x.problem.yaml", b"statement: s\n"))], headers=H)
    assert bob.put("/api/admin/env", json={"name": "A", "value": "1"}, headers=H).status_code == 403
    assert ada.put("/api/admin/env", json={"name": "LEVEL", "value": "server"}, headers=H).status_code == 200
    assert ada.put("/api/admin/env", json={"name": "HF_TOKEN", "value": "hf-secret", "secret": True}, headers=H).status_code == 200
    assert bob.put("/api/env", json={"name": "LEVEL", "value": "user"}, headers=H).status_code == 200
    assert bob.put("/api/apps/x/env", json={"name": "LEVEL", "value": "loop"}, headers=H).status_code == 200
    assert bob.put("/api/apps/x/env", json={"name": "SEED", "value": "7"}, headers=H).status_code == 200
    for bad in ("FLUX_SANDBOX", "FLUX_SANDBOX_ALLOW", "PATH", "LD_PRELOAD", "FLUX_CLAUDE_ARGS", "OPENCODE_CONFIG_CONTENT",
                "FLUX_CLAUDE_ENV", "FLUX_AGENTS", "FLUX_SHARED_VARS", "FLUX_CLAUDE_API_KEY", "FLUX_REMOTE_MODEL", "1X", "A-B"):
        assert bob.put("/api/apps/x/env", json={"name": bad, "value": "0"}, headers=H).status_code == 400, bad
    seen = bob.get("/api/apps/x/env").json()
    assert {x["name"]: x["value"] for x in seen["server"]} == {"HF_TOKEN": "set", "LEVEL": "server"}, "a secret is never sent back"
    assert "hf-secret" not in bob.get("/api/env").text and "hf-secret" not in ada.get("/api/admin/env").text
    bob_u = store.user(name="bob")
    env = run_env(store, bob_u, "x")
    assert env["LEVEL"] == "loop" and env["SEED"] == "7" and env["HF_TOKEN"] == "hf-secret"
    assert {"LEVEL", "HF_TOKEN", "SEED"} <= set(env["FLUX_SANDBOX_PASS"].split(","))
    assert set(env["FLUX_SHARED_VARS"].split(",")) == {"LEVEL", "HF_TOKEN", "SEED"}, "D807: every agent gets these"
    assert run_env(store, bob_u)["LEVEL"] == "user", "without the loop: the user's over the server's"
    assert run_env(store, store.user(name="ada"))["LEVEL"] == "server"
    assert bob.put("/api/apps/x/env", json={"name": "SEED", "value": None}, headers=H).status_code == 200
    assert "SEED" not in run_env(store, bob_u, "x")
    assert bob.delete("/api/apps/x", headers=H).status_code == 200
    assert store.env("loop:bob:x") == {}, "a deleted loop's variables go with it"


def test_only_an_admin_takes_a_loop_out_of_the_sandbox_or_limits_it(server):
    app, store = server
    ada, bob = _client(app, "ada", "correct horse battery"), _client(app, "bob", "another long secret")
    bob.post("/api/apps", data={"name": "x"}, files=[("files", ("x.problem.yaml", b"statement: s\n"))], headers=H)
    assert bob.put("/api/apps/x/advanced", json={"sandbox": False}, headers=H).status_code == 403
    r = ada.put("/api/apps/x/advanced", params={"owner": "bob"}, json={"sandbox": True, "memory": "16g", "cpus": "8", "pids": 2048}, headers=H)
    assert r.status_code == 200 and r.json()["advanced"] == {"memory": "16g", "cpus": "8", "pids": 2048}
    assert ada.put("/api/apps/x/advanced", params={"owner": "bob"}, json={"memory": "lots"}, headers=H).status_code == 400
    env = {"FLUX_SANDBOX": "0", "FLUX_SANDBOX_MEMORY": "1g"}
    sandbox_env(env, True, advanced(store, "bob", "x"))
    assert env == {"FLUX_SANDBOX": "1", "FLUX_SANDBOX_MEMORY": "16g", "FLUX_SANDBOX_CPUS": "8", "FLUX_SANDBOX_PIDS": "2048"}, \
        "the server's sandbox, with the loop's limits; nothing of the environment's own"
    assert ada.put("/api/apps/x/advanced", params={"owner": "bob"}, json={"sandbox": False}, headers=H).status_code == 200
    env = {}
    sandbox_env(env, True, advanced(store, "bob", "x"))
    assert env == {"FLUX_SANDBOX": "0"}
    seen = bob.get("/api/apps/x/env").json()
    assert seen["advanced"] == {"sandbox": False} and seen["can_advance"] is False
    assert any(a["action"] == "advanced settings" for a in ada.get("/api/audit").json())
    store.set_env("global", "FLUX_X_OK", "1")
    with pytest.raises(ValueError):
        store.set_env("global", "FLUX_SANDBOX_MEMORY", "999g")


def test_a_loop_works_one_thing_at_a_time_unless_an_admin_allows_parallel_work(server):
    """D741: an admin allows parallel work per loop; how much is the document's."""
    app, _store = server
    ada, bob = _client(app, "ada", "correct horse battery"), _client(app, "bob", "another long secret")
    bob.post("/api/apps", data={"name": "x"}, files=[("files", ("x.problem.yaml", b"statement: s\n"))], headers=H)
    assert bob.put("/api/apps/x/advanced", json={"parallel": True}, headers=H).status_code == 403
    assert ada.put("/api/apps/x/advanced", params={"owner": "bob"}, json={"parallel": True}, headers=H).json()["advanced"] == {"parallel": True}
    assert ada.put("/api/apps/x/advanced", params={"owner": "bob"}, json={"parallel": False}, headers=H).json()["advanced"] == {}


def test_admin_agents_sets_each_agents_program_login_files_and_hosts(server, tmp_path, monkeypatch):
    """D756: Admin › Agents -- an agent's program, login command and arguments, the files every home
    starts with for it and the hosts it needs; only an admin; each user's readiness shown."""
    from flux_web.runs import home_ready, machine_env, run_env, sandbox_config

    app, store = server
    srv_home = tmp_path / "server-home"
    (srv_home / ".config/corp-opencode").mkdir(parents=True)
    (srv_home / ".config/corp-opencode/provider.json").write_text("{}")
    monkeypatch.setenv("HOME", str(srv_home))
    ada, bob = _client(app, "ada", "correct horse battery"), _client(app, "bob", "another long secret")
    corp = tmp_path / "opt/corp/bin/opencode"
    corp.parent.mkdir(parents=True)
    corp.write_text("#!/bin/sh\necho corp 2.0\n")
    corp.chmod(0o755)
    body = {"bin": str(corp), "login": "opencode auth login https://ai.corp.example", "args": "--agent flux",
            "home": [".config/corp-opencode"], "hosts": ["ai.corp.example"]}
    assert bob.put("/api/admin/agents/opencode", json=body, headers=H).status_code == 403
    assert ada.put("/api/admin/agents/opencode", json={**body, "home": ["../etc"]}, headers=H).status_code == 400
    assert ada.put("/api/admin/agents/opencode", json=body, headers=H).status_code == 200
    got = {a["id"]: a for a in ada.get("/api/admin/agents").json()["agents"]}
    oc = got["opencode"]
    assert oc["bin"] == str(corp) and oc["login"].endswith("ai.corp.example") and oc["found"] == str(corp) and oc["version"] == "corp 2.0"
    assert {u["user"]: u["state"] for u in oc["users"]} == {"ada": "not tested", "bob": "not tested"}
    bob_user = store.user(name="bob")
    assert (home_ready(store, bob_user) / ".config/corp-opencode/provider.json").is_file(), "every home starts with its files"
    env = run_env(store, bob_user, "x")
    assert env["FLUX_OPENCODE_BIN"] == str(corp) and env["FLUX_OPENCODE_ARGS"].startswith("--agent flux")
    store.server_set("sandbox", {"network": "allowlist", "allow": ["a.example"]})
    env = {"PATH": "/usr/bin", "FLUX_SANDBOX": "1"}
    machine_env(env, sandbox_config(store), {}, [])
    assert env["FLUX_SANDBOX_ALLOW"].split(",")[:2] == ["a.example", "ai.corp.example"], "its hosts join the allowlist"


def test_a_failed_start_says_why_in_its_logs_words(tmp_path, server):
    """D757: Overview's "Why it stopped" -- the last lines of this start's log that say what went
    wrong, without stamps nor where it ran; the last lines when none does."""
    from flux_web.runs import RunManager

    _app, store = server
    log = tmp_path / "loop.log"
    log.write_text("── started 2026-10-01 10:00:00 by bob · 1 pass(es) ──\nan old ERROR from the start before\n"
                   "── started 2026-10-02 10:00:00 by bob · 1 pass(es) ──\n"
                   "2026-10-02 10:00:01.000 flux task run: in the podman sandbox flux-abc\n"
                   "2026-10-02 10:00:02.000 roles: orchestrator=sweep\n"
                   "2026-10-02 10:00:03.000 no-such-checker not on PATH; `flux task check` lists what the task needs\n")
    rm = RunManager(store, sandbox=True)
    assert rm.failure({"log": str(log)}) == ["no-such-checker not on PATH; `flux task check` lists what the task needs"]
    log.write_text("── started x ──\nall quiet\nthe end\n")
    assert rm.failure({"log": str(log)}) == ["all quiet", "the end"], "nothing said wrong: its last lines"


def test_a_document_is_checked_before_it_is_saved(server):
    """D757: Direct edit asks whether the text loads before it writes it."""
    app, _store = server
    bob = _client(app, "bob", "another long secret")
    bob.post("/api/apps", data={"name": "x"}, files=[("files", ("x.problem.yaml", b"statement: s\n"))], headers=H)
    bad = bob.post("/api/apps/x/validate", json={"text": "id: [unclosed\nstatement: x"}, headers=H).json()
    assert not bad["ok"] and bad["error"].startswith("not YAML")
    wrong = bob.post("/api/apps/x/validate", json={"text": "statment: typo\n"}, headers=H).json()
    assert not wrong["ok"] and "statment" in wrong["error"], wrong
    good = "statement: s\nlanguage: python\nflow: {test: {test: ['true']}}\nobjectives: []\n"
    assert bob.post("/api/apps/x/validate", json={"text": good}, headers=H).json() == {"ok": True, "error": ""}


def test_a_corporate_builds_login_file_says_its_user_is_logged_in(server, tmp_path, monkeypatch):
    """D760: a build of its own (`nga auth login`) keeps its login where the admin says; Account and
    the agent's Test read it there."""
    from flux_loop.agent_check import check_agent
    from flux_web.runs import run_env

    app, store = server
    monkeypatch.setenv("HOME", str(tmp_path / "server-home"))
    ada, bob = _client(app, "ada", "correct horse battery"), _client(app, "bob", "another long secret")
    assert ada.put("/api/admin/agents/opencode", json={"login": "nga auth login", "login_files": ["/etc/passwd"]}, headers=H).status_code == 400
    fake = tmp_path / "nga"
    fake.write_text("#!/bin/sh\necho nga 1.0\n")
    fake.chmod(0o755)
    assert ada.put("/api/admin/agents/opencode", json={"bin": str(fake), "login": "nga auth login",
                                                        "login_files": [".local/share/nga/auth.json"]}, headers=H).status_code == 200
    home = store.home_of(store.user(name="bob"))
    assert not {a["id"]: a["logged_in"] for a in bob.get("/api/logins").json()["agents"]}["opencode"]
    (home / ".local/share/nga").mkdir(parents=True)
    (home / ".local/share/nga/auth.json").write_text('{"token": "x"}')
    assert {a["id"]: a["logged_in"] for a in bob.get("/api/logins").json()["agents"]}["opencode"], "its own file: logged in"
    env = run_env(store, store.user(name="bob"), "x")
    assert env["FLUX_OPENCODE_LOGIN_FILES"] == ".local/share/nga/auth.json"
    env = {k: v for k, v in env.items() if k != "OPENCODE_CONFIG_CONTENT"}
    got = check_agent("opencode", env={**env, "HOME": str(home), "FLUX_OPENCODE_BIN": str(fake)})
    login = next(s for s in got["steps"] if s["step"] == "login")
    assert login["ok"] and "nga/auth.json" in login["said"]
