"""D734: internal and external users. An internal user's runs inherit the server's (and the
machine's) model, agent and environment settings; an external user's bring their own, take their
home files from a home of their own, and log their agents in from the web into it."""

from __future__ import annotations

import os
import stat
import sys
import time
import types

import pytest
from fastapi.testclient import TestClient

from flux_cli import sandbox
from flux_web import create_app
from flux_web.agents import KINDS, Agent
from flux_web.logins import Logins, logged_in
from flux_web.runs import home_ready, run_env, sandbox_env
from flux_web.store import Store

H = {"X-Flux": "1"}


def _store(tmp_path):
    store = Store(tmp_path / "data")
    store.add_user("ada", "correct horse battery", "admin")
    store.add_user("ian", "ian has a long secret", "internal")
    store.add_user("eve", "eve has a long secret", "external")
    store.add_user("old", "old has a long secret", "user")
    return store


def test_the_kinds_of_user(tmp_path):
    store = _store(tmp_path)
    kinds = {u.name: u.role for u in store.users()}
    assert kinds == {"ada": "admin", "ian": "internal", "eve": "external", "old": "internal"}, "the old 'user' is internal"
    with pytest.raises(ValueError):
        store.add_user("x", "a long password", "guest")
    store.set_user("ian", role="external")
    assert store.user(name="ian").external
    home = store.home_of(store.user(name="eve"))
    assert home == store.data / "users" / "eve" / "home" and stat.S_IMODE(home.stat().st_mode) == 0o700


def test_an_external_users_run_has_nothing_of_the_servers_but_its_programs(tmp_path, monkeypatch):
    store = _store(tmp_path)
    for k, v in {"FLUX_REMOTE_BASE_URL": "https://machine.example/v1", "FLUX_REMOTE_API_KEY": "machine-key",
                 "ANTHROPIC_API_KEY": "machine-claude", "OLLAMA_BASE_URL": "http://localhost:11434"}.items():
        monkeypatch.setenv(k, v)
    store.set_server_setting("FLUX_REMOTE_MODEL", "server-model")
    corp = tmp_path / "opt/corp/bin/opencode"
    corp.parent.mkdir(parents=True)
    corp.write_text("#!/bin/sh\necho corp\n")
    corp.chmod(0o755)
    store.server_set("agents", {"opencode": {"bin": str(corp)}})
    store.set_env("global", "TEAM_TOKEN", "server-secret", secret=True)
    eve, ian = store.user(name="eve"), store.user(name="ian")
    store.set_setting(eve, "FLUX_REMOTE_BASE_URL", "https://eve.example/v1")
    store.set_setting(eve, "FLUX_REMOTE_API_KEY", "eve-key")
    store.set_env(f"user:{eve.id}", "EVE_VAR", "mine")
    e = run_env(store, eve, "x")
    assert e["FLUX_REMOTE_BASE_URL"] == "https://eve.example/v1" and e["FLUX_REMOTE_API_KEY"] == "eve-key"
    assert "ANTHROPIC_API_KEY" not in e and "OLLAMA_BASE_URL" not in e and "TEAM_TOKEN" not in e, "nothing of the machine's or the server's"
    assert e.get("FLUX_REMOTE_MODEL") != "server-model" and e["EVE_VAR"] == "mine"
    assert e["FLUX_OPENCODE_BIN"] == str(corp), "the admin's program: what runs, not whose account"
    assert e["FLUX_SANDBOX_HOME"] == str(store.data / "users" / "eve" / "home")
    i = run_env(store, ian, "x")
    assert i["ANTHROPIC_API_KEY"] == "machine-claude" and i["TEAM_TOKEN"] == "server-secret"
    assert i["FLUX_SANDBOX_HOME"] == str(store.data / "users" / "ian" / "home"), "D744: an internal user has a home too"
    sandbox_env(e, False, {})                                   # on the host: their agents use their home
    assert e["HOME"] == e["FLUX_SANDBOX_HOME"]


def test_a_home_starts_with_the_admins_list_and_never_anyones_login(tmp_path, monkeypatch):
    """D744: a home is started from the server account's home -- the admin's list, the agents'
    configuration by default -- where it lacks them; a login is never among them."""
    server = tmp_path / "server-home"
    (server / ".config/opencode").mkdir(parents=True)
    (server / ".config/opencode/opencode.json").write_text('{"provider": "corp"}')
    (server / ".local/share/opencode").mkdir(parents=True)
    (server / ".local/share/opencode/auth.json").write_text("the server's login")
    (server / ".gitconfig").write_text("[user]")
    monkeypatch.setenv("HOME", str(server))
    store = _store(tmp_path)
    eve = store.user(name="eve")
    home = home_ready(store, eve)
    assert (home / ".config/opencode/opencode.json").read_text() == '{"provider": "corp"}'
    assert not (home / ".local/share/opencode/auth.json").exists() and not (home / ".gitconfig").exists()
    store.server_set("sandbox", {"home_seed": [".gitconfig"]})
    (home / ".gitconfig").write_text("eve's own")
    home_ready(store, eve)
    assert (home / ".gitconfig").read_text() == "eve's own", "never over what is there"


def test_a_login_runs_in_a_terminal_its_link_shown_its_answer_typed(tmp_path, monkeypatch):
    home = tmp_path / "eve-home"
    fake = tmp_path / "fake-login.py"
    fake.write_text("import os, sys\nprint('Open https://login.example/device?c=AB12 and paste the code:', flush=True)\n"
                    "code = sys.stdin.readline().strip()\nos.makedirs(os.path.expanduser('~/.codex'), exist_ok=True)\n"
                    "open(os.path.expanduser('~/.codex/auth.json'), 'w').write(code)\nprint('logged in as eve')\n")
    lg = Logins()
    env = {**os.environ, "FLUX_SANDBOX": "0"}
    lg.start("eve", "codex", home, [sys.executable, str(fake)], env)
    with pytest.raises(ValueError):
        lg.start("eve", "codex", home, [sys.executable, str(fake)], env)       # one at a time
    for _ in range(100):
        if "paste the code" in lg.state("eve").get("text", ""):
            break
        time.sleep(0.05)
    assert "https://login.example/device?c=AB12" in lg.state("eve")["text"]
    lg.send("eve", "the-code", "enter")
    for _ in range(100):
        if not lg.state("eve")["running"]:
            break
        time.sleep(0.05)
    st = lg.state("eve")
    assert st["rc"] == 0 and "logged in as eve" in st["text"]
    assert (home / ".codex/auth.json").read_text() == "the-code" and logged_in(home, {"codex": Agent("codex", "codex")})["codex"]


def test_a_printed_token_is_kept_as_a_setting_and_never_shown(tmp_path):
    """D748: `claude setup-token` prints its year-long token and keeps nothing; the login takes
    it from the output into the user's settings and masks it -- also when it arrives in pieces."""
    home = tmp_path / "ian-home"
    fake = tmp_path / "fake-setup-token.py"
    fake.write_text("import sys, time\nsys.stdout.write('Your token:\\nsk-ant-oat01-abcdefghij'); sys.stdout.flush()\n"
                    "time.sleep(0.3)\nprint('KLMNOPQRSTUVWXYZ_0123-xyz')\nprint('Store this token securely.')\n")
    kept = {}
    lg = Logins()
    lg.start("ian", "claude", home, [sys.executable, str(fake)], {**os.environ, "FLUX_SANDBOX": "0"},
             on_secret=lambda name, value: kept.__setitem__(name, value), printed=(KINDS["claude"]["printed"], "FLUX_CLAUDE_OAUTH_TOKEN"))
    for _ in range(100):
        if not lg.state("ian")["running"]:
            break
        time.sleep(0.05)
    text = lg.state("ian")["text"]
    assert kept == {"FLUX_CLAUDE_OAUTH_TOKEN": "sk-ant-oat01-abcdefghijKLMNOPQRSTUVWXYZ_0123-xyz"}
    assert "sk-ant" not in text and "abcdefghij" not in text, "never shown, not even its first piece"
    assert "saved to your settings as FLUX_CLAUDE_OAUTH_TOKEN" in text and "Store this token securely." in text
    assert not logged_in(home, {"claude": Agent("claude", "claude")})["claude"], "Claude Code's settings file alone is not a login"


def test_every_user_logs_in_and_the_server_is_not_offered_to_an_external_one(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    store = _store(tmp_path)
    store.set_server_setting("FLUX_REMOTE_MODEL", "server-model")
    from web_agents import install

    install(monkeypatch, tmp_path)                                  # D807: offered where installed
    store.server_set("agents", {"codex": {"login": "codex login --device-auth"}})
    app = create_app(tmp_path / "data", sandbox=False)
    def client(n, pw):
        c = TestClient(app)
        assert c.post("/api/login", json={"name": n, "password": pw}, headers=H).status_code == 200
        return c
    eve, ian = client("eve", "eve has a long secret"), client("ian", "ian has a long secret")
    assert [a["id"] for a in ian.get("/api/logins").json()["agents"]] == ["opencode", "claude", "codex"], "an internal user logs in too"
    lg = eve.get("/api/logins").json()
    assert [a["id"] for a in lg["agents"]] == ["opencode", "claude", "codex"]
    assert {a["id"]: a["command"] for a in lg["agents"]}["codex"] == "codex login --device-auth", "the admin's command"
    assert "FLUX_REMOTE_MODEL" not in eve.get("/api/settings").json()["server"]
    assert ian.get("/api/settings").json()["server"]["FLUX_REMOTE_MODEL"] == "server-model"
    assert eve.put("/api/settings", json={"values": {"FLUX_CODEX_LOGIN": "x"}}, headers=H).status_code == 400, "the admin's only"


def test_an_agent_is_enabled_for_a_user_by_a_passed_test(tmp_path, monkeypatch):
    """D751: a start, an authoring agent or an ask that needs an agent is refused until the
    user who starts it has tested that agent and it passed; a new login is tested again."""
    from flux_loop import TaskSpec
    from flux_loop.agent_check import agents_used

    doc = {"id": "x",
           "statement": "s",
           "language": "python",
           "objectives": [],
           "flow": {"generate": {"by": "codex"},
                    "critique": {"by": {"preset": "claude", "timeout_s": 60}},
                    "test": {"test": ["true"]}}}
    assert agents_used(TaskSpec.from_dict(doc)) == ["codex", "claude"]
    from web_agents import install

    install(monkeypatch, tmp_path)                                  # D807: offered where installed
    store = _store(tmp_path)
    app = create_app(tmp_path / "data", sandbox=False)
    ian = TestClient(app)
    assert ian.post("/api/login", json={"name": "ian", "password": "ian has a long secret"}, headers=H).status_code == 200
    ian.post("/api/apps", data={"name": "x"}, files=[("files", ("x.problem.yaml",
             b"statement: s\nlanguage: python\nflow: {generate: {by: codex}, test: {test: ['true']}}\n"))], headers=H)
    r = ian.post("/api/apps/x/start", json={"passes": 1}, headers=H)
    assert r.status_code == 409 and "Codex not set up for ian" in r.json()["detail"]
    assert ian.post("/api/apps/x/asks", json={"question": "why?", "author": "claude"}, headers=H).status_code == 409
    store.server_set("agent-test:ian:codex", {"ok": True})
    assert [a["tested"] for a in ian.get("/api/logins").json()["agents"] if a["id"] == "codex"] == [{"ok": True}]
    r = ian.post("/api/apps/x/start", json={"passes": 1}, headers=H)
    assert r.status_code != 409 or "set up" not in r.text, "tested: no longer refused for it"


def test_a_login_that_ends_well_is_tested_at_once(tmp_path, monkeypatch):
    """D768: the Test runs on the server when a login ends with 0 -- the page need not be open; a
    login that failed or was stopped is not tested."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    store = _store(tmp_path)
    app = create_app(tmp_path / "data", sandbox=False)
    ian = TestClient(app)
    assert ian.post("/api/login", json={"name": "ian", "password": "ian has a long secret"}, headers=H).status_code == 200

    from web_agents import install

    install(monkeypatch, tmp_path)                                  # D807: offered where installed

    def login(line):
        store.server_set("agents", {"codex": {"login": line}})
        assert ian.post("/api/logins/codex", headers=H).status_code == 200
        for _ in range(300):
            if not ian.get("/api/logins/session").json()["running"]:
                return
            time.sleep(0.1)
        raise AssertionError("the login did not end")

    login("sh -c 'exit 3'")
    time.sleep(1.0)
    assert store.server_get("agent-test:ian:codex") is None, "a failed login: not tested"
    login("sh -c 'mkdir -p .codex && echo {} > .codex/auth.json'")
    for _ in range(600):
        got = store.server_get("agent-test:ian:codex")
        if got and got.get("when"):
            break
        time.sleep(0.2)
    else:
        raise AssertionError("no Test after the login")
    assert got["agent"] == "codex" and got["steps"], got
    assert not {a["id"]: a for a in ian.get("/api/logins").json()["agents"]}["codex"]["testing"], "done: no longer testing"


def test_a_shared_loop_runs_on_its_owners_agents(tmp_path, monkeypatch):
    """D769: whoever starts a shared loop, it runs as its owner's -- their home, their logins, their
    agents' Tests; the one who starts it needs no login of their own."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    store = _store(tmp_path)
    app = create_app(tmp_path / "data", sandbox=False)

    def client(n, pw):
        c = TestClient(app)
        assert c.post("/api/login", json={"name": n, "password": pw}, headers=H).status_code == 200
        return c

    ian, old = client("ian", "ian has a long secret"), client("old", "old has a long secret")
    ian.post("/api/apps", data={"name": "x"}, files=[("files", ("x.problem.yaml",
             b"statement: s\nlanguage: python\nflow: {generate: {by: codex}, test: {test: ['true']}}\n"))], headers=H)
    assert ian.put("/api/apps/x/shares", json={"user": "old", "perm": "edit"}, headers=H).status_code == 200
    r = old.post("/api/apps/x/start?owner=ian", json={"passes": 1}, headers=H)
    assert r.status_code == 409 and "Codex not set up for ian" in r.json()["detail"], r.text
    store.server_set("agent-test:ian:codex", {"ok": True})
    r = old.post("/api/apps/x/start?owner=ian", json={"passes": 1}, headers=H)
    assert "set up" not in r.text, "the owner's Test is what counts, not the starter's"
    i = run_env(store, store.user(name="ian"), "x")
    assert i["FLUX_SANDBOX_HOME"] == str(store.data / "users" / "ian" / "home"), "the owner's home: their logins"


def test_an_agents_test_is_asked_again_each_day_and_a_failure_holds_its_runs(tmp_path, monkeypatch):
    """D807: a day after an agent's last test it is tested again -- a login it refreshes stays
    alive; one that no longer answers is said to its user and their loops wait for it."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    from web_agents import install

    install(monkeypatch, tmp_path)
    store = _store(tmp_path)
    app = create_app(tmp_path / "data", sandbox=False)
    day = 24 * 3600
    store.server_set("agent-test:ian:codex", {"ok": True, "when": time.time() - day / 2})
    assert app.state.retest_due() == [], "not due yet"
    store.server_set("agent-test:ian:codex", {"ok": True, "when": time.time() - day - 60})
    store.server_set("agent-test:eve:claude", {"ok": True, "when": time.time() - day - 30})
    assert app.state.retest_due() == [("ian", "codex")], "the oldest first, one at a time"
    assert app.state.retest_due() == [], "while one runs, no other"
    for _ in range(600):
        got = store.server_get("agent-test:ian:codex")
        if got.get("steps"):
            break
        time.sleep(0.2)
    else:
        raise AssertionError("no daily test")
    assert not got["ok"], "a stand-in program with no login does not answer"
    notes = store.server_get("notices:ian") or []
    assert any("daily test failed" in n["text"] for n in notes), notes
    for _ in range(50):
        if app.state.retest_due() == [("eve", "claude")]:
            break
        time.sleep(0.1)
    else:
        raise AssertionError("the next one due was not tested")
