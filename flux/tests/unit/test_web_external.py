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
    store.set_server_setting("FLUX_OPENCODE_BIN", "/opt/corp/bin/opencode")
    store.set_env("global", "TEAM_TOKEN", "server-secret", secret=True)
    eve, ian = store.user(name="eve"), store.user(name="ian")
    store.set_setting(eve, "FLUX_REMOTE_BASE_URL", "https://eve.example/v1")
    store.set_setting(eve, "FLUX_REMOTE_API_KEY", "eve-key")
    store.set_env(f"user:{eve.id}", "EVE_VAR", "mine")
    e = run_env(store, eve, "x")
    assert e["FLUX_REMOTE_BASE_URL"] == "https://eve.example/v1" and e["FLUX_REMOTE_API_KEY"] == "eve-key"
    assert "ANTHROPIC_API_KEY" not in e and "OLLAMA_BASE_URL" not in e and "TEAM_TOKEN" not in e, "nothing of the machine's or the server's"
    assert e.get("FLUX_REMOTE_MODEL") != "server-model" and e["EVE_VAR"] == "mine"
    assert e["FLUX_OPENCODE_BIN"] == "/opt/corp/bin/opencode", "the admin's program: what runs, not whose account"
    assert e["FLUX_SANDBOX_HOME"] == str(store.data / "users" / "eve" / "home")
    i = run_env(store, ian, "x")
    assert i["ANTHROPIC_API_KEY"] == "machine-claude" and i["TEAM_TOKEN"] == "server-secret"
    assert i["FLUX_SANDBOX_HOME"] == str(store.data / "users" / "ian" / "home"), "D744: an internal user has a home too"
    assert run_env(store, eve, "x", home_for=ian)["FLUX_SANDBOX_HOME"] == i["FLUX_SANDBOX_HOME"], "whoever starts it lends their logins"
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
    assert (home / ".codex/auth.json").read_text() == "the-code" and logged_in(home)["codex"]


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
             on_secret=lambda name, value: kept.__setitem__(name, value))
    for _ in range(100):
        if not lg.state("ian")["running"]:
            break
        time.sleep(0.05)
    text = lg.state("ian")["text"]
    assert kept == {"CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat01-abcdefghijKLMNOPQRSTUVWXYZ_0123-xyz"}
    assert "sk-ant" not in text and "abcdefghij" not in text, "never shown, not even its first piece"
    assert "saved to your settings as CLAUDE_CODE_OAUTH_TOKEN" in text and "Store this token securely." in text
    assert not logged_in(home)["claude"], "Claude Code's settings file alone is not a login"


def test_every_user_logs_in_and_the_server_is_not_offered_to_an_external_one(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    store = _store(tmp_path)
    store.set_server_setting("FLUX_REMOTE_MODEL", "server-model")
    store.set_server_setting("FLUX_CODEX_LOGIN", "codex login --device-auth")
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
