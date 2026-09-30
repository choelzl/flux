"""The run's sandbox (D680): what the container sees and may write, what it is not given, and
the allowlist proxy. Docker itself is exercised live (docs/decisions.md D680)."""

from __future__ import annotations

import http.server
import os
import socket
import tempfile
import threading
import types
from pathlib import Path

import pytest

from flux_cli import sandbox
from flux_cli.sandbox_proxy import AllowProxy, allowed


def _args(tmp_path, **kw):
    doc = tmp_path / "p" / "x.problem.yaml"
    doc.parent.mkdir(exist_ok=True)
    doc.write_text("id: x\n")
    base = dict(file=str(doc), db=str(tmp_path / "rec" / "x.db"), out=None, json=None, plan=None, replies=None,
                skill=[], no_sandbox=False)
    return types.SimpleNamespace(**{**base, **kw})


def test_on_by_default_off_by_flag_env_or_inside(monkeypatch, tmp_path):
    monkeypatch.delenv("FLUX_SANDBOXED", raising=False)
    monkeypatch.setenv("FLUX_SANDBOX", "1")
    assert sandbox.enabled(_args(tmp_path))
    assert not sandbox.enabled(_args(tmp_path, no_sandbox=True))
    monkeypatch.setenv("FLUX_SANDBOX", "0")
    assert not sandbox.enabled(_args(tmp_path))
    monkeypatch.setenv("FLUX_SANDBOX", "1")
    monkeypatch.setenv("FLUX_SANDBOXED", "1")
    assert not sandbox.enabled(_args(tmp_path)), "never a sandbox in a sandbox"


def test_the_problem_is_read_only_its_out_workbench_and_record_writable(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    args = _args(tmp_path)
    ro, rw = sandbox.mounts_for(args, "task run")
    home = str(tmp_path / "p")
    assert home in ro and f"{home}/out" in rw and f"{home}/workbench" in rw
    app = sandbox.app_dir(args, "task run")
    assert app == tmp_path / "cache" / "flux" / "apps" / "x", "the application's id"
    assert str(tmp_path / "rec") in rw and str(app / "tmp") in rw and str(app / "cache") in rw
    assert str(tmp_path / "cache" / "flux") not in rw, "nothing shared between applications (D681)"
    (tmp_path / "elsewhere").mkdir()
    assert sandbox.app_dir(_args(tmp_path / "elsewhere"), "task run") == app, "one id: one cache, whatever the run"
    assert "/nix/store" in ro and "/etc/passwd" in ro and not any(p == "/etc" for p in ro + rw)
    assert not set(ro) & set(rw)
    assert str(Path.home() / ".ssh") not in ro + rw


def test_the_container_gets_no_host_secrets_and_its_own_home(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("SSH_AUTH_SOCK", "/run/ssh.sock")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_x")
    monkeypatch.setenv("FLUX_REMOTE_API_KEY", "k")
    monkeypatch.delenv("FLUX_SANDBOX_ALLOW", raising=False)
    cmd = sandbox.container_argv(["flux", "task", "run", "x"], _args(tmp_path), "task run", "flux-t", None, "docker")
    env = {c.split("=", 1)[0]: c.split("=", 1)[1] for c, prev in zip(cmd[1:], cmd) if prev == "-e"}
    assert "SSH_AUTH_SOCK" not in env and "GITHUB_TOKEN" not in env and env["FLUX_REMOTE_API_KEY"] == "k"
    assert env["FLUX_SANDBOXED"] == "1" and env["FLUX_SANDBOX_NAME"] == "flux-t"
    vols = [c for c, prev in zip(cmd[1:], cmd) if prev == "-v"]
    app = sandbox.app_dir(_args(tmp_path), "task run")
    assert f"{app / 'home'}:{Path.home()}" in vols, "HOME is the application's"
    assert env["TMPDIR"] == "/tmp" and env["FLUX_TRACE_ROOT"] == str(app / "tmp" / "flux-traces"), \
        "scratch on the container's own /tmp (abc hangs on a mounted one), traces in the cache"
    assert env["XDG_CACHE_HOME"] == str(app / "cache")
    assert not any("docker.sock" in v for v in vols) and not any(v.startswith(f"{Path.home()}/.config/flux") for v in vols)
    for flag in ("--read-only", "--rm", "no-new-privileges", "ALL"):
        assert flag in cmd
    assert cmd[cmd.index("--network") + 1] == "host"
    assert cmd[cmd.index("--user") + 1] == f"{os.getuid()}:{os.getgid()}"
    boxed = sandbox.container_argv(["flux"], _args(tmp_path), "task run", "flux-t", "/run/x", "docker")
    assert boxed[boxed.index("--network") + 1] == "none" and "HTTPS_PROXY=http://127.0.0.1:18080" in boxed
    labels = [c for c, prev in zip(cmd[1:], cmd) if prev == "--label"]
    assert labels == ["flux.sandbox=1"], "a run of this machine's user: no loop's label"
    monkeypatch.setenv("FLUX_SANDBOX_APP", "bob.x")
    web = sandbox.container_argv(["flux"], _args(tmp_path), "task run", "flux-t", None, "docker")
    assert "flux.app=bob.x" in [c for c, prev in zip(web[1:], web) if prev == "--label"], "D695: the admin finds its loop"


def test_the_allowlist_matches_domains_ips_and_localhost():
    allow = ["localai.example.org", "anthropic.com", "10.0.0.0/8", "localhost"]
    assert allowed("localai.example.org", allow) and allowed("api.anthropic.com", allow)
    assert not allowed("example.com", allow) and not allowed("evilanthropic.com", allow)
    assert allowed("10.2.3.4", allow) and not allowed("11.0.0.1", allow) and allowed("127.0.0.1", allow)
    assert not allowed("127.0.0.1", ["localai.example.org"])


def test_the_proxy_forwards_to_allowed_hosts_and_refuses_the_rest(tmp_path):
    class Hello(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", "5")
            self.end_headers()
            self.wfile.write(b"hello")

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), Hello)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    d = tempfile.mkdtemp(dir=os.environ.get("XDG_RUNTIME_DIR") or "/tmp")    # a filesystem that holds sockets
    path = os.path.join(d, "p.sock")
    proxy = AllowProxy(path, ["127.0.0.1"])
    try:
        proxy.start()
    except PermissionError:
        pytest.skip("this environment forbids Unix sockets")

    def ask(host: str) -> bytes:
        s = socket.socket(socket.AF_UNIX)
        s.connect(path)
        s.sendall(f"GET http://{host}:{port}/x HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n".encode())
        out = b""
        while chunk := s.recv(4096):
            out += chunk
        return out

    try:
        assert ask("127.0.0.1").endswith(b"hello")
        assert b"403" in ask("example.com").split(b"\r\n")[0] and "example.com" in proxy.refused
    finally:
        proxy.stop()
        srv.shutdown()


def test_podman_rootless_from_a_bare_root_directory(monkeypatch, tmp_path):
    """D682: rootless Podman, no image: a local root directory of links and mount points, its
    state on a local disk, its own init; the run records how to reach it."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("FLUX_SANDBOX_STORAGE", str(tmp_path / "pod"))
    monkeypatch.setenv("FLUX_SANDBOX_ENGINE", "podman")
    assert sandbox.engine() == "podman"
    cmd = sandbox.container_argv(["flux", "task", "run", "x"], _args(tmp_path), "task run", "flux-p", None)
    cli = sandbox.engine_cli("podman")
    assert cmd[:len(cli)] == cli and cli[cli.index("--root") + 1] == str(tmp_path / "pod" / "podman")
    assert "--init" in cmd and "--user" not in cmd and sandbox.IMAGE not in cmd
    root = Path(cmd[cmd.index("--rootfs") + 1])
    assert root == tmp_path / "pod" / "rootfs" and (root / "bin").is_symlink() and (root / "etc").is_dir()
    assert cmd[cmd.index("--rootfs") + 2:] == ["flux", "task", "run", "x"]
    env = {c.split("=", 1)[0]: c.split("=", 1)[1] for c, prev in zip(cmd[1:], cmd) if prev == "-e"}
    import json as _json

    assert _json.loads(env["FLUX_SANDBOX_CLI"]) == cli
    monkeypatch.setenv("FLUX_SANDBOX_ENGINE", "docker")
    assert sandbox.engine() == "docker"
