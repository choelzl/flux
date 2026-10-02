"""D698: what every sandbox gets from the admin -- PATH directories (and the login PATH), home
files every home starts with (D744), the network -- and the allowlist proxy checking a name against IP and
CIDR rules by the addresses it resolves to."""

from __future__ import annotations

import os
import types

import pytest
from fastapi.testclient import TestClient

from flux_cli import sandbox
from flux_cli.sandbox_proxy import permitted
from flux_web import create_app
from flux_web.runs import machine_env
from flux_web.store import Store

H = {"X-Flux": "1"}


def test_a_name_passes_an_ip_rule_by_what_it_resolves_to_and_is_reached_there():
    assert permitted("api.example.org", 443, ["example.org"]) == "api.example.org", "a name rule: as it is"
    assert permitted("localhost", 80, ["127.0.0.0/8"]) == "127.0.0.1", "resolved, checked, connected by the address"
    assert permitted("localhost", 80, ["10.0.0.0/8"]) is None
    assert permitted("10.1.2.3", 80, ["10.0.0.0/8"]) == "10.1.2.3" and permitted("11.1.2.3", 80, ["10.0.0.0/8"]) is None
    assert permitted("localhost", 80, ["example.org"]) is None, "no IP rule: a name is not resolved"


def test_a_runs_home_is_the_users_own_writable_and_nothing_of_the_machines(tmp_path, monkeypatch):
    """D744: HOME is the Flux home of whoever starts the run, writable at /home/flux -- no
    read-only mounts of the server account's agent files, no copies."""
    home, mine = tmp_path / "server-home", tmp_path / "eve-home"
    (home / ".config/opencode").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("FLUX_SANDBOX_HOME", str(mine))
    args = types.SimpleNamespace(file=str(tmp_path / "x.problem.yaml"), db=None, out=None, json=None)
    (tmp_path / "x.problem.yaml").write_text("statement: s\n")
    cmd = sandbox.container_argv(["flux"], args, "task run", "flux-t", None, "docker")
    vols = [c for c, prev in zip(cmd[1:], cmd) if prev == "-v"]
    envs = sandbox.container_env(cmd)
    assert f"{mine.resolve()}:/home/flux" in vols and envs["HOME"] == "/home/flux"
    assert not any(v.startswith(f"{home}/.config") for v in vols), "nothing of the server account's home"
    assert "FLUX_SANDBOX_HOME" not in envs, "the host's path stays outside"
    assert not (sandbox.app_dir(args, "task run") / "home").exists(), "no per-loop home any more"
    login = types.SimpleNamespace(home=str(mine), cmd=["--", "opencode", "auth", "login"])
    cmd = sandbox.container_argv(["flux", "login"], login, "login", "flux-l", None, "docker")
    assert f"{mine.resolve()}:/home/flux" in [c for c, prev in zip(cmd[1:], cmd) if prev == "-v"], "a login writes the same home"


def test_on_ones_own_machine_the_flux_home_starts_from_the_real_one_once(tmp_path, monkeypatch):
    """D744: with no `FLUX_SANDBOX_HOME`, a run's home is ~/.local/share/flux/home, started from
    the real home's agent files; what changed in it is never copied over again."""
    real = tmp_path / "real"
    (real / ".claude").mkdir(parents=True)
    (real / ".claude/.credentials.json").write_text("t1")
    (real / ".config/opencode").mkdir(parents=True)
    (real / ".config/opencode/opencode.json").write_text("{}")
    (real / ".ssh").mkdir()
    (real / ".ssh/id_ed25519").write_text("never")
    monkeypatch.setenv("HOME", str(real))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.delenv("FLUX_SANDBOX_HOME", raising=False)
    h = sandbox.flux_home()
    assert h == (real / ".local/share/flux/home").resolve()
    assert (h / ".claude/.credentials.json").read_text() == "t1" and (h / ".config/opencode/opencode.json").is_file()
    assert not (h / ".ssh").exists(), "only the agents' files"
    (h / ".claude/.credentials.json").write_text("refreshed inside")
    (real / ".claude/.credentials.json").write_text("t2")
    sandbox.flux_home()
    assert (h / ".claude/.credentials.json").read_text() == "refreshed inside", "a refresh stays"


def test_a_home_is_started_without_overwriting_and_never_outside(tmp_path):
    src, home = tmp_path / "src", tmp_path / "home"
    (src / ".tool/sub").mkdir(parents=True)
    (src / ".tool/a").write_text("a")
    (src / ".tool/sub/b").write_text("b")
    (src / ".tool/key").write_text("k")
    (src / ".tool/key").chmod(0o600)
    (src / ".tool/link").symlink_to("a")
    (home / ".tool").mkdir(parents=True)
    (home / ".tool/a").write_text("mine")
    got = sandbox.seed_home(home, src, [".tool", "../etc", "/etc", "~/.missing"])
    assert sorted(got) == [".tool/key", ".tool/link", ".tool/sub/b"], "a folder merged; what the home has stays"
    assert (home / ".tool/a").read_text() == "mine" and os.readlink(home / ".tool/link") == "a"
    assert (home / ".tool/key").stat().st_mode & 0o777 == 0o600
    assert sandbox.seed_home(home, src, [".tool"]) == [], "nothing twice"


def test_an_empty_allowlist_refuses_every_host_instead_of_opening(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("FLUX_SANDBOX_NET", "allowlist")
    monkeypatch.delenv("FLUX_SANDBOX_ALLOW", raising=False)
    monkeypatch.setattr(sandbox, "_engine_ok", lambda eng: "")
    seen = {}
    monkeypatch.setattr(sandbox.subprocess, "call", lambda cmd: seen.setdefault("cmd", cmd) and 0)
    import flux_cli.sandbox_proxy as sp

    class Proxy:                                        # no socket: what it would be told is enough
        def __init__(self, path, allow, log=None, about=None):
            seen["allow"] = list(allow)

        def start(self):
            pass

        def stop(self):
            pass

    monkeypatch.setattr(sp, "AllowProxy", Proxy)
    (tmp_path / "x.problem.yaml").write_text("statement: s\n")
    args = types.SimpleNamespace(file=str(tmp_path / "x.problem.yaml"), db=None, out=None, json=None)
    sandbox.launch(["task", "run", "x"], args, "task run")
    cmd = seen["cmd"]
    assert cmd[cmd.index("--network") + 1] == "none" and seen["allow"] == [], "a proxy that allows nothing"


def test_the_admins_sandbox_settings_reach_each_run(tmp_path, monkeypatch):
    extra = tmp_path / "tools/bin"
    extra.mkdir(parents=True)
    env = {"PATH": "/usr/bin", "FLUX_SANDBOX": "1", "FLUX_REMOTE_BASE_URL": "https://llm.example:8443/v1", "FLUX_SANDBOX_ALLOW": "evil.example"}
    said = machine_env(env, {"path": [str(extra), "/nope"], "network": "allowlist", "allow": ["10.0.0.0/8"], "users_add": False, "endpoints": True},
                       {"allow": ["huggingface.co"]}, ["asked.example"])
    assert env["PATH"] == f"{extra}:/usr/bin", "a directory that does not exist is left out"
    assert env["FLUX_SANDBOX_NET"] == "allowlist" and env["FLUX_SANDBOX_ALLOW"] == "10.0.0.0/8,huggingface.co,llm.example"
    assert "asked.example" not in env["FLUX_SANDBOX_ALLOW"], "a user may not add: their hosts are not taken"
    assert said == "network: an allowlist of 3 entries" and "10.0.0" not in said, "D716: the log says how many, never which"
    env = {"PATH": "/usr/bin", "FLUX_SANDBOX": "1"}
    machine_env(env, {"network": "allowlist", "allow": ["a.example"], "users_add": True}, {}, ["b.example"])
    assert env["FLUX_SANDBOX_ALLOW"] == "a.example,b.example"
    env = {"PATH": "/usr/bin", "FLUX_SANDBOX": "1"}
    assert machine_env(env, {}, {}, []) == "" and "FLUX_SANDBOX_ALLOW" not in env and "FLUX_SANDBOX_NET" not in env, "open by default"
    env = {"PATH": "/usr/bin", "FLUX_SANDBOX": "0"}
    assert machine_env(env, {"network": "allowlist", "allow": ["a.example"]}, {}, []) == "" and "FLUX_SANDBOX_NET" not in env


def test_only_an_admin_sets_the_sandbox_and_bad_entries_are_refused(tmp_path):
    store = Store(tmp_path / "data")
    store.add_user("ada", "correct horse battery", "admin")
    store.add_user("bob", "another long secret")
    app = create_app(tmp_path / "data", sandbox=True)

    def client(n, p):
        c = TestClient(app)
        c.post("/api/login", json={"name": n, "password": p}, headers=H)
        return c

    ada, bob = client("ada", "correct horse battery"), client("bob", "another long secret")
    from flux_web.runs import HOME_SEED

    assert ada.get("/api/admin/sandbox").json()["config"]["home_seed"] == list(HOME_SEED), \
        "D765: the form shows the homes' own default (a corporate OpenCode's parts), so saving it keeps them"
    good = {"network": "allowlist", "allow": ["*.example.org", "10.0.0.0/8", "localhost"], "path": ["/opt/x/bin"], "home_seed": ["~/.config/mycode"]}
    assert bob.put("/api/admin/sandbox", json=good, headers=H).status_code == 403
    r = ada.put("/api/admin/sandbox", json=good, headers=H)
    assert r.status_code == 200 and r.json()["config"]["home_seed"] == [".config/mycode"]
    for bad in ({"network": "closed"}, {"allow": ["http://x.example/"]}, {"path": ["relative/bin"]}, {"home_seed": ["../../etc/shadow"]}):
        assert ada.put("/api/admin/sandbox", json=bad, headers=H).status_code == 400, bad
    got = ada.get("/api/admin/sandbox").json()
    assert got["config"]["network"] == "allowlist" and isinstance(got["login_path"], list)
    bob.post("/api/apps", data={"name": "x"}, files=[("files", ("x.problem.yaml", b"statement: s\n"))], headers=H)
    net = bob.get("/api/apps/x/preflight").json()["network"]
    assert net["network"] == "allowlist" and "allow" not in net, "D716: a user learns it is limited, not by which hosts"
    assert ada.put("/api/apps/x/advanced", params={"owner": "bob"}, json={"allow": ["hf.co", "nope nope"]}, headers=H).status_code == 400
    assert ada.put("/api/apps/x/advanced", params={"owner": "bob"}, json={"allow": ["hf.co"]}, headers=H).json()["advanced"]["allow"] == ["hf.co"]


@pytest.fixture(autouse=True)
def _no_ambient(monkeypatch):
    for k in ("FLUX_SANDBOX_ALLOW", "FLUX_SANDBOX_NET"):
        monkeypatch.delenv(k, raising=False)
    yield
    os.environ.pop("FLUX_SANDBOX_APP", None)
