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


def test_a_copied_home_path_is_never_hidden_by_a_read_only_mount(tmp_path, monkeypatch):
    """D706: a PATH directory inside a copied folder is not mounted (the copy holds it); one above
    a copied folder is, with the copy mounted again on top of it."""
    home = tmp_path / "home"
    for d in (".mycode/creds/bin", ".tools/bin", ".tools/creds", "other/bin"):
        (home / d).mkdir(parents=True)
    (home / ".mycode/creds/token.json").write_text("{}")
    (home / ".tools/creds/key").write_text("k")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("FLUX_SANDBOX_HOME_COPY", ".mycode/creds,.tools/creds")
    monkeypatch.setenv("FLUX_SANDBOX_HOME_RO", ".mycode/creds/bin")
    monkeypatch.setenv("PATH", os.pathsep.join([f"{home}/.mycode/creds/bin", f"{home}/.tools", f"{home}/other/bin",
                                                os.environ["PATH"]]))
    args = types.SimpleNamespace(file=str(tmp_path / "x.problem.yaml"), db=None, out=None, json=None)
    (tmp_path / "x.problem.yaml").write_text("id: x\nstatement: s\n")
    cmd = sandbox.container_argv(["flux"], args, "task run", "flux-t", None, "docker")
    vols = [c for c, prev in zip(cmd[1:], cmd) if prev == "-v"]
    sh = sandbox.app_dir(args, "task run") / "home"
    assert not any(v.startswith(f"{home}/.mycode/creds") for v in vols), "inside the copy: not mounted over it"
    assert f"{home}/.tools:{home}/.tools:ro" in vols, "a folder above a copy: still mounted"
    assert f"{sh}/.tools/creds:{home}/.tools/creds" in vols, "... and the copy again on top of it"
    assert f"{home}/other/bin:{home}/other/bin:ro" in vols
    path = next(c for c, prev in zip(cmd[1:], cmd) if prev == "-e" and c.startswith("PATH=")).split(os.pathsep)
    assert f"{home}/.mycode/creds/bin" in path[0].split("=", 1)[1:] + path, "only its mount goes: it stays on PATH, found in the copy"
    assert (sh / ".mycode/creds/token.json").is_file() and (sh / ".tools/creds/key").read_text() == "k"


def test_a_copied_folder_with_links_is_copied_again_every_run(tmp_path, monkeypatch):
    """D707: the second run's copy replaced the first's link with "file exists"; a link is copied
    as a link and never written through, a changed type replaced, what only the copy has kept."""
    home = tmp_path / "home"
    (home / ".mycode/creds/sub").mkdir(parents=True)
    (home / ".mycode/creds/token").write_text("t1")
    (home / ".mycode/creds/current").symlink_to("token")
    (home / ".mycode/creds/sub/x").write_text("x")
    outside = tmp_path / "host-file"
    outside.write_text("host")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("FLUX_SANDBOX_HOME_COPY", ".mycode/creds")
    app = tmp_path / "app"
    sh = sandbox._sandbox_home(app)
    c = sh / ".mycode/creds"
    (c / "session.json").write_text("{}")                    # the agent's own, in the copy
    (c / "token").unlink()
    (c / "token").symlink_to(outside)                         # a link where the host has a file
    (home / ".mycode/creds/token").write_text("t2")
    (home / ".mycode/creds/sub").rename(home / ".mycode/creds/was-sub")
    (home / ".mycode/creds/sub").write_text("now a file")
    sandbox._sandbox_home(app)                                # the second run
    assert (c / "token").read_text() == "t2" and not (c / "token").is_symlink()
    assert outside.read_text() == "host", "never written through a link"
    assert os.readlink(c / "current") == "token" and (c / "sub").read_text() == "now a file"
    assert (c / "session.json").is_file() and (c / "was-sub/x").read_text() == "x"
    tool, key = home / ".mycode/creds/tool", home / ".mycode/creds/key"
    tool.write_text("#!/bin/sh\n")
    tool.chmod(0o755)
    key.write_text("k")
    key.chmod(0o600)
    sandbox._sandbox_home(app)
    assert (c / "tool").stat().st_mode & 0o7777 == 0o755 and (c / "key").stat().st_mode & 0o7777 == 0o600, "each its own mode"


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
    good = {"network": "allowlist", "allow": ["*.example.org", "10.0.0.0/8", "localhost"], "path": ["/opt/x/bin"], "home_ro": ["~/.config/mycode"]}
    assert bob.put("/api/admin/sandbox", json=good, headers=H).status_code == 403
    r = ada.put("/api/admin/sandbox", json=good, headers=H)
    assert r.status_code == 200 and r.json()["config"]["home_ro"] == [".config/mycode"]
    for bad in ({"network": "closed"}, {"allow": ["http://x.example/"]}, {"path": ["relative/bin"]}, {"home_copy": ["../../etc/shadow"]}):
        assert ada.put("/api/admin/sandbox", json=bad, headers=H).status_code == 400, bad
    got = ada.get("/api/admin/sandbox").json()
    assert got["config"]["network"] == "allowlist" and isinstance(got["login_path"], list)
    bob.post("/api/apps", data={"name": "x"}, files=[("files", ("x.problem.yaml", b"id: x\nstatement: s\n"))], headers=H)
    net = bob.get("/api/apps/x/preflight").json()["network"]
    assert net["network"] == "allowlist" and "allow" not in net, "D716: a user learns it is limited, not by which hosts"
    assert ada.put("/api/apps/x/advanced", params={"owner": "bob"}, json={"allow": ["hf.co", "nope nope"]}, headers=H).status_code == 400
    assert ada.put("/api/apps/x/advanced", params={"owner": "bob"}, json={"allow": ["hf.co"]}, headers=H).json()["advanced"]["allow"] == ["hf.co"]


@pytest.fixture(autouse=True)
def _no_ambient(monkeypatch):
    for k in ("FLUX_SANDBOX_HOME_RO", "FLUX_SANDBOX_HOME_COPY", "FLUX_SANDBOX_ALLOW", "FLUX_SANDBOX_NET"):
        monkeypatch.delenv(k, raising=False)
    yield
    os.environ.pop("FLUX_SANDBOX_APP", None)


def test_a_copied_program_running_in_another_run_is_replaced_not_written_over(tmp_path, monkeypatch):
    """D725: a loop and its asks share one home; the second run's copy of a program the first
    is running (OpenCode) must not open it for writing (ETXTBSY, or on a newer kernel the
    program changed under itself): a new file takes its place, and a file already the same is
    left alone."""
    import shutil as _sh
    import subprocess as _sp

    src = tmp_path / "host" / "bin" / "agent"
    src.parent.mkdir(parents=True)
    import sys as _sys

    _sh.copyfile(os.path.realpath(_sys.executable), src)                # a real program, as OpenCode's binary is
    src.chmod(0o755)
    dst = tmp_path / "app" / "bin" / "agent"
    sandbox._copy_over(src.parent, dst.parent)
    running = _sp.Popen([str(dst), "-c", "import time; time.sleep(30)"])
    import time as _t

    _t.sleep(0.5)
    try:
        before = dst.stat().st_ino
        src.write_bytes(src.read_bytes() + b"\0")                           # the host's program updated
        sandbox._copy_over(src.parent, dst.parent)                         # was: OSError 26, text file busy
        # (a kernel that no longer says ETXTBSY lets the write through instead: the running program's own file changes under it)
        assert dst.stat().st_ino != before, "a new file in its place, the running one untouched"
        assert dst.read_bytes() == src.read_bytes() and os.access(dst, os.X_OK)
        assert running.poll() is None, "the running copy runs on"
        ino = dst.stat().st_ino
        sandbox._copy_over(src.parent, dst.parent)
        assert dst.stat().st_ino == ino, "unchanged: not copied again"
        assert sorted(p.name for p in dst.parent.iterdir()) == ["agent"], "no temporary left behind"
    finally:
        running.kill()
