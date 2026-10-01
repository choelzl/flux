"""D698: what every sandbox gets from the admin -- PATH directories (and the login PATH), home
files mounted or copied in, the network -- and the allowlist proxy checking a name against IP and
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


def test_extra_home_paths_are_mounted_or_copied_and_never_leave_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".config/mycode").mkdir(parents=True)
    (home / ".config/mycode/settings.json").write_text("{}")
    (home / ".mycode/creds").mkdir(parents=True)
    (home / ".mycode/creds/token.json").write_text('{"t": 1}')
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("FLUX_SANDBOX_HOME_RO", ".config/mycode, ../../etc, /etc")
    monkeypatch.setenv("FLUX_SANDBOX_HOME_COPY", "~/.mycode/creds")
    assert ".config/mycode" in sandbox.home_ro() and not any("etc" in r for r in sandbox.home_ro())
    args = types.SimpleNamespace(file=str(tmp_path / "x.problem.yaml"), db=None, out=None, json=None)
    (tmp_path / "x.problem.yaml").write_text("id: x\nstatement: s\n")
    cmd = sandbox.container_argv(["flux"], args, "task run", "flux-t", None, "docker")
    vols = [c for c, prev in zip(cmd[1:], cmd) if prev == "-v"]
    assert f"{home}/.config/mycode:{home}/.config/mycode:ro" in vols
    app = sandbox.app_dir(args, "task run")
    assert (app / "home/.mycode/creds/token.json").read_text() == '{"t": 1}', "a folder of credentials, copied whole"


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
        def __init__(self, path, allow):
            seen["allow"] = list(allow)

        def start(self):
            pass

        def stop(self):
            pass

    monkeypatch.setattr(sp, "AllowProxy", Proxy)
    (tmp_path / "x.problem.yaml").write_text("id: x\nstatement: s\n")
    args = types.SimpleNamespace(file=str(tmp_path / "x.problem.yaml"), db=None, out=None, json=None)
    sandbox.launch(["task", "run", "x"], args, "task run")
    cmd = seen["cmd"]
    assert cmd[cmd.index("--network") + 1] == "none" and seen["allow"] == [], "a proxy that allows nothing"


def test_the_admins_sandbox_settings_reach_each_run(tmp_path, monkeypatch):
    extra = tmp_path / "tools/bin"
    extra.mkdir(parents=True)
    env = {"PATH": "/usr/bin", "FLUX_SANDBOX": "1", "FLUX_REMOTE_BASE_URL": "https://llm.example:8443/v1", "FLUX_SANDBOX_ALLOW": "evil.example"}
    said = machine_env(env, {"path": [str(extra), "/nope"], "home_ro": [".config/mycode"], "home_copy": [".mycode/creds"],
                             "network": "allowlist", "allow": ["10.0.0.0/8"], "users_add": False, "endpoints": True},
                       {"allow": ["huggingface.co"]}, ["asked.example"])
    assert env["PATH"] == f"{extra}:/usr/bin", "a directory that does not exist is left out"
    assert env["FLUX_SANDBOX_HOME_RO"] == ".config/mycode" and env["FLUX_SANDBOX_HOME_COPY"] == ".mycode/creds"
    assert env["FLUX_SANDBOX_NET"] == "allowlist" and env["FLUX_SANDBOX_ALLOW"] == "10.0.0.0/8,huggingface.co,llm.example"
    assert "asked.example" in said or "asked.example" not in env["FLUX_SANDBOX_ALLOW"], "a user may not add: their hosts are not taken"
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
    good = {"network": "allowlist", "allow": ["*.example.org", "10.0.0.0/8", "localhost"], "path": ["/opt/x/bin"], "home_ro": ["~/.config/mycode"]}
    assert bob.put("/api/admin/sandbox", json=good, headers=H).status_code == 403
    r = ada.put("/api/admin/sandbox", json=good, headers=H)
    assert r.status_code == 200 and r.json()["config"]["home_ro"] == [".config/mycode"]
    for bad in ({"network": "closed"}, {"allow": ["http://x.example/"]}, {"path": ["relative/bin"]}, {"home_copy": ["../../etc/shadow"]}):
        assert ada.put("/api/admin/sandbox", json=bad, headers=H).status_code == 400, bad
    got = ada.get("/api/admin/sandbox").json()
    assert got["config"]["network"] == "allowlist" and isinstance(got["login_path"], list)
    bob.post("/api/apps", data={"name": "x"}, files=[("files", ("x.problem.yaml", b"id: x\nstatement: s\n"))], headers=H)
    assert bob.get("/api/apps/x/preflight").json()["network"]["allow"] == ["*.example.org", "10.0.0.0/8", "localhost"]
    assert ada.put("/api/apps/x/advanced", params={"owner": "bob"}, json={"allow": ["hf.co", "nope nope"]}, headers=H).status_code == 400
    assert ada.put("/api/apps/x/advanced", params={"owner": "bob"}, json={"allow": ["hf.co"]}, headers=H).json()["advanced"]["allow"] == ["hf.co"]


@pytest.fixture(autouse=True)
def _no_ambient(monkeypatch):
    for k in ("FLUX_SANDBOX_HOME_RO", "FLUX_SANDBOX_HOME_COPY", "FLUX_SANDBOX_ALLOW", "FLUX_SANDBOX_NET"):
        monkeypatch.delenv(k, raising=False)
    yield
    os.environ.pop("FLUX_SANDBOX_APP", None)
