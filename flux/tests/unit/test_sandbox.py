"""The run's sandbox (D680): what the container sees and may write, what it is not given, and
the allowlist proxy. Docker itself is exercised live (docs/decisions.md D680)."""

from __future__ import annotations

import http.server
import json
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
    doc.write_text("")
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
    assert str(tmp_path / "rec") in rw and str(app / "tmp") in rw and str(app / "cache") not in rw
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
    monkeypatch.setenv("FLUX_SANDBOX_ALLOW", "secret-host.example")
    monkeypatch.setenv("FLUX_SANDBOX_NET", "allowlist")
    monkeypatch.setenv("FLUX_SANDBOX_REFUSALS", "/data/network-refused.jsonl")
    cmd = sandbox.container_argv(["flux", "task", "run", "x"], _args(tmp_path), "task run", "flux-t", None, "docker")
    env = sandbox.container_env(cmd)
    assert "SSH_AUTH_SOCK" not in env and "GITHUB_TOKEN" not in env and env["FLUX_REMOTE_API_KEY"] == "k"
    assert env["FLUX_SANDBOXED"] == "1" and env["FLUX_SANDBOX_NAME"] == "flux-t"
    assert not {"FLUX_SANDBOX_ALLOW", "FLUX_SANDBOX_NET", "FLUX_SANDBOX_REFUSALS"} & set(env), "D716: the proxy's, outside"
    assert env["OPENCODE_SKIP_SAFE_CHECK"] == "1", "D714: OpenCode inside the sandbox"
    vols = [c for c, prev in zip(cmd[1:], cmd) if prev == "-v"]
    app = sandbox.app_dir(_args(tmp_path), "task run")
    import os as _os

    assert f"{Path(_os.environ['FLUX_SANDBOX_HOME']).resolve()}:/home/flux" in vols and env["HOME"] == "/home/flux", \
        "D744: HOME is the user's Flux home"
    assert env["TMPDIR"] == "/tmp" and env["FLUX_TRACE_ROOT"] == str(app / "tmp" / "flux-traces"), \
        "scratch on the container's own /tmp (abc hangs on a mounted one), traces in the cache"
    assert env["XDG_CACHE_HOME"] == "/home/flux/.cache", "D760: the user's own cache: a login kept there is their runs' too"
    assert not any("docker.sock" in v for v in vols) and not any(v.startswith(f"{Path.home()}/.config/flux") for v in vols)
    for flag in ("--read-only", "--rm", "no-new-privileges", "ALL"):
        assert flag in cmd
    assert cmd[cmd.index("--network") + 1] == "host"
    assert cmd[cmd.index("--user") + 1] == f"{os.getuid()}:{os.getgid()}"
    boxed = sandbox.container_argv(["flux"], _args(tmp_path), "task run", "flux-t", "/run/x", "docker")
    assert boxed[boxed.index("--network") + 1] == "none" and sandbox.container_env(boxed)["HTTPS_PROXY"] == "http://127.0.0.1:18080"
    labels = [c for c, prev in zip(cmd[1:], cmd) if prev == "--label"]
    assert labels == ["flux.sandbox=1"], "a run of this machine's user: no loop's label"
    assert "GITHUB_TOKEN" not in env
    monkeypatch.setenv("HF_TOKEN", "hf")
    monkeypatch.setenv("FLUX_SANDBOX_PASS", "HF_TOKEN")                # D697: set on the web on purpose
    passed = sandbox.container_argv(["flux"], _args(tmp_path), "task run", "flux-t", None, "docker")
    penv = sandbox.container_env(passed)
    assert penv["HF_TOKEN"] == "hf" and "GITHUB_TOKEN" not in penv
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
    log = tmp_path / "refused.jsonl"
    proxy = AllowProxy(path, ["127.0.0.1"], log=str(log), about={"app": "bob.add8", "command": "task run"})
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
        ask("example.com")
        ask("evil.example")
        def lookup(name: str) -> bytes:                                     # D717: as the relay inside asks
            s = socket.socket(socket.AF_UNIX)
            s.connect(path)
            s.sendall(f"FLUX-LOOKUP {name} HTTP/1.1\r\n\r\n".encode())
            return s.recv(256)

        assert b" 403 " in lookup("direct.blocked.example") and b" 204 " in lookup("localhost"), "refused, and allowed by its address"
        lines = [json.loads(x) for x in log.read_text().splitlines() if '"lookup"' not in x]       # D708: for the admin's audit
        assert [(x["host"], x["port"], x["app"], x["command"]) for x in lines] == [
            ("example.com", port, "bob.add8", "task run"), ("evil.example", port, "bob.add8", "task run")], "once per host and port"
        looked = [json.loads(x) for x in log.read_text().splitlines() if '"lookup"' in x]
        assert [(x["host"], x["how"]) for x in looked] == [("direct.blocked.example", "lookup")]
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
    env = sandbox.container_env(cmd)
    import json as _json

    assert _json.loads(env["FLUX_SANDBOX_CLI"]) == cli
    monkeypatch.setenv("FLUX_SANDBOX_ENGINE", "docker")
    assert sandbox.engine() == "docker"


def test_a_path_asked_both_ways_is_writable(monkeypatch, tmp_path):
    """D704: `flux ask --dir <here>` run from <here> -- the working folder (read) and the
    document's folder (write) are the same: it is mounted writable, once."""
    import types

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.chdir(tmp_path)
    ro, rw = sandbox.mounts_for(types.SimpleNamespace(dir=str(tmp_path), file=[], skill=[]), "ask")
    assert str(tmp_path) in rw and str(tmp_path) not in ro


def test_a_sub_loop_reads_through_its_parent_and_writes_the_parents_out_and_workbench(monkeypatch, tmp_path):
    """D802, D805: `flux task run nlu/ops/recip` loads through the parent and writes the parent's
    `out/` (the record) and `workbench/`: the parent's folder is the one mounted, not the child's."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    top = tmp_path / "nlu"
    (top / "ops" / "recip").mkdir(parents=True)
    (top / "problem.yaml").write_text("statement: the operators\nlanguage: text\nsubtasks: [ops/recip]\n"
                                      "flow:\n  test: \"true\"\n")
    child = top / "ops" / "recip" / "problem.yaml"
    child.write_text("statement: one operator\n")
    ro, rw = sandbox.mounts_for(_args(tmp_path, file=str(child)), "task run")
    assert str(top.resolve()) in ro
    assert str(top.resolve() / "out") in rw and str(top.resolve() / "workbench") in rw
    assert not (top / "ops" / "recip" / "out").exists() and not (top / "ops" / "recip" / "workbench").exists()


def test_an_admins_agent_program_is_mounted_alone_and_named_inside(monkeypatch, tmp_path):
    """D804: FLUX_CLAUDE_BIN names a program outside PATH -- through a link that sits beside the
    model's key: the program itself is mounted read-only, the file alone (never the folder beside
    it, nor the link's), and inside the variable names it at the path mounted."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    real = tmp_path / "ext" / "native-binary" / "claude"
    real.parent.mkdir(parents=True)
    real.write_text("#!/bin/sh\n")
    real.chmod(0o755)
    (real.parent / "other").write_text("x")
    conf = tmp_path / "conf" / "flux"
    conf.mkdir(parents=True)
    (conf / "localai.key").write_text("k")
    (conf / "claude").symlink_to(real)
    monkeypatch.setenv("FLUX_CLAUDE_BIN", str(conf / "claude"))
    monkeypatch.setenv("FLUX_CODEX_BIN", str(tmp_path / "missing"))        # not there: nothing mounted
    ro, rw = sandbox.mounts_for(_args(tmp_path), "task run")
    assert str(real) in ro
    assert not any(p in (str(conf), str(conf / "claude"), str(real.parent), str(tmp_path / "missing")) for p in ro + rw)
    cmd = sandbox.container_argv(["flux"], _args(tmp_path), "task run", "flux-t", None, eng="podman")
    assert f"{real}:{real}:ro" in cmd
    assert sandbox.container_env(cmd)["FLUX_CLAUDE_BIN"] == str(real)


def test_the_running_python_is_mounted_and_runs_flux(monkeypatch, tmp_path):
    """D714: a `pip install` user's flux -- a venv, its interpreter, an editable install's source,
    anywhere on the disk -- is visible inside, and the container runs this flux by its interpreter."""
    import sys

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    venv, src = tmp_path / "venv", tmp_path / "checkout" / "src"
    venv.mkdir()
    src.mkdir(parents=True)
    monkeypatch.setattr(sys, "prefix", str(venv))
    monkeypatch.setattr(sys, "path", [*sys.path, str(src), str(tmp_path / "gone")])
    ro, _ = sandbox.mounts_for(_args(tmp_path), "task run")
    assert str(venv) in ro and str(src) in ro and str(tmp_path / "gone") not in ro
    assert not any(p.startswith("/usr/") for p in ro), ("the system mounts cover their own", [p for p in ro if p.startswith("/usr/")])
    seen = {}
    monkeypatch.setattr(sandbox, "_engine_ok", lambda eng: "")
    monkeypatch.setattr(sandbox.subprocess, "call", lambda cmd: seen.setdefault("cmd", cmd) and 0)
    sandbox.launch(["task", "run", "x"], _args(tmp_path), "task run")
    assert seen["cmd"][-6:] == [sys.executable, "-m", "flux_cli", "task", "run", "x"], \
        "not sys.argv[0]: under `python -m` it is a source file, not a program"


def test_a_name_looked_up_inside_is_asked_of_the_proxy(monkeypatch, tmp_path):
    """D717: under an allowlist the container resolves through the relay at 127.0.0.1:53, which
    reads the name a query asks for."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    proxy_dir = tmp_path / "px"
    proxy_dir.mkdir()
    cmd = sandbox.container_argv(["flux"], _args(tmp_path), "task run", "flux-t", str(proxy_dir), "podman")
    assert f"{proxy_dir}/resolv.conf:/etc/resolv.conf:ro" in cmd and "net.ipv4.ip_unprivileged_port_start=53" in cmd
    assert (proxy_dir / "resolv.conf").read_text().startswith("nameserver 127.0.0.1")
    open_cmd = sandbox.container_argv(["flux"], _args(tmp_path), "task run", "flux-t", None, "podman")
    assert not any("resolv.conf" in c for c in open_cmd), "an open network keeps the engine's resolver"
    q = b"\x12\x34\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00" + b"\x04evil\x07Example\x03com\x00" + b"\x00\x01\x00\x01"
    assert sandbox._dns_name(q) == "evil.example.com" and sandbox._dns_name(b"\x00" * 5) is None


def test_loopback_stays_inside_and_the_hosts_certificates_go_in(monkeypatch, tmp_path):
    """D722: a Bun agent's own local server is not sent to the host proxy (403 there); the
    host's trust store -- a corporate CA a variable names, the system bundle for Node/Bun -- is
    the container's."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    corp = tmp_path / "certs" / "corp.pem"
    corp.parent.mkdir()
    corp.write_text("-----BEGIN CERTIFICATE-----\n")
    bundle = tmp_path / "bundle.crt"
    bundle.write_text("x")
    monkeypatch.setenv("SSL_CERT_FILE", str(corp))
    monkeypatch.delenv("NODE_EXTRA_CA_CERTS", raising=False)
    monkeypatch.setattr(sandbox, "BUNDLES", ("/nonexistent/ca.crt", str(bundle)))
    ro, _ = sandbox.mounts_for(_args(tmp_path), "task run")
    assert str(corp) in ro and "/etc/ssl" in ro
    boxed = sandbox.container_argv(["flux"], _args(tmp_path), "task run", "flux-t", "/run/x", "docker")
    env = sandbox.container_env(boxed)
    assert set(env["NO_PROXY"].split(",")) >= {"localhost", "127.0.0.1", "::1"} and env["no_proxy"] == env["NO_PROXY"]
    assert env["NODE_EXTRA_CA_CERTS"] == str(corp) and env["SSL_CERT_FILE"] == str(corp), "the bundle the host names"
    monkeypatch.delenv("SSL_CERT_FILE")
    plain = sandbox.container_argv(["flux"], _args(tmp_path), "task run", "flux-t", None, "docker")
    assert sandbox.container_env(plain)["NODE_EXTRA_CA_CERTS"] == str(bundle), "else the system's"
    monkeypatch.setenv("NODE_EXTRA_CA_CERTS", str(corp))
    own = sandbox.container_argv(["flux"], _args(tmp_path), "task run", "flux-t", None, "docker")
    assert sandbox.container_env(own)["NODE_EXTRA_CA_CERTS"] == str(corp), "the host's own choice kept"


def test_behind_a_corporate_proxy_allowed_hosts_go_through_it(tmp_path):
    """D722: the allowlist proxy reaches an allowed host through the host's own proxy, with its
    credentials; a host the host's NO_PROXY names is reached directly; a refusal upstream is
    passed back."""
    seen: list[bytes] = []

    def corp(conn: socket.socket) -> None:
        head = b""
        while b"\r\n\r\n" not in head:
            head += conn.recv(4096)
        seen.append(head)
        if b"evil-upstream" in head:
            conn.sendall(b"HTTP/1.1 407 Proxy Authentication Required\r\nContent-Length: 0\r\n\r\n")
            conn.close()
            return
        conn.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
        while data := conn.recv(4096):                                      # the tunnel: an echo
            conn.sendall(data)
        conn.close()

    up = socket.socket()
    up.bind(("127.0.0.1", 0))
    up.listen(8)
    threading.Thread(target=lambda: [threading.Thread(target=corp, args=(up.accept()[0],), daemon=True).start()
                                     for _ in iter(int, 1)], daemon=True).start()
    d = tempfile.mkdtemp(dir=os.environ.get("XDG_RUNTIME_DIR") or "/tmp")
    path = os.path.join(d, "p.sock")
    proxies = {"https": f"http://bob:p%40ss@127.0.0.1:{up.getsockname()[1]}", "no": "direct.example"}
    proxy = AllowProxy(path, ["api.example.com", "evil-upstream.example", "direct.example"], proxies=proxies)
    try:
        proxy.start()
    except PermissionError:
        pytest.skip("this environment forbids Unix sockets")

    def connect(host: str) -> tuple[socket.socket, bytes]:
        s = socket.socket(socket.AF_UNIX)
        s.settimeout(10)
        s.connect(path)
        s.sendall(f"CONNECT {host}:443 HTTP/1.1\r\nHost: {host}:443\r\n\r\n".encode())
        return s, s.recv(4096)

    try:
        s, reply = connect("api.example.com")
        assert reply.startswith(b"HTTP/1.1 200")
        s.sendall(b"ping")
        assert s.recv(4096) == b"ping", "the tunnel runs through the host's proxy"
        s.close()
        assert seen[0].startswith(b"CONNECT api.example.com:443 HTTP/1.1")
        assert b"Proxy-Authorization: Basic Ym9iOnBAc3M=" in seen[0], "bob:p@ss, unquoted"
        _, refused = connect("evil-upstream.example")
        assert refused.startswith(b"HTTP/1.1 407"), "the host proxy's refusal, as it said it"
        _, direct = connect("direct.example")                              # NO_PROXY: tried directly (no such host)
        assert direct.startswith(b"HTTP/1.1 502") and len(seen) == 2 and "direct.example" in proxy.unreached
        _, out = connect("example.com")
        assert out.startswith(b"HTTP/1.1 403") and len(seen) == 2, "the allowlist first: a refused host never goes upstream"
    finally:
        proxy.stop()
        up.close()


def test_no_value_is_on_the_containers_command_line(monkeypatch, tmp_path):
    """D745: `ps` shows a process's arguments to every user of the machine; the model's key was
    one of them (`-e FLUX_REMOTE_API_KEY=...`). The values are in a file of the run's own (0600)."""
    import stat

    monkeypatch.setenv("FLUX_REMOTE_API_KEY", "the-secret-key")
    monkeypatch.setenv("FLUX_MULTI", "one\ntwo")
    cmd = sandbox.container_argv(["flux"], _args(tmp_path), "task run", "flux-k", None, "podman")
    assert not any("the-secret-key" in c for c in cmd), "no value on the command line"
    env_file = cmd[cmd.index("--env-file") + 1]
    assert stat.S_IMODE(Path(env_file).stat().st_mode) == 0o600
    assert stat.S_IMODE(Path(env_file).parent.stat().st_mode) == 0o700
    env = sandbox.container_env(cmd)
    assert env["FLUX_REMOTE_API_KEY"] == "the-secret-key" and env["HOME"] == "/home/flux"
    assert "FLUX_MULTI" in cmd and env["FLUX_MULTI"] == "one\ntwo", "several lines: by name, from the environment"


def test_only_out_and_workbench_are_kept_the_rest_read_only(monkeypatch, tmp_path):
    """D764 (D763 undone): where the run works -- its application's folder -- is read-only, no layer;
    what lasts goes to `out/` and `workbench/`, scratch to the container's own /tmp."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.chdir(tmp_path)
    cmd = sandbox.container_argv(["flux"], _args(tmp_path), "task run", "flux-t", None, "podman")
    vols = [c for c, prev in zip(cmd[1:], cmd) if prev == "-v"]
    here, p = str(Path.cwd()), str(tmp_path / "p")
    assert f"{here}:{here}:ro" in vols and not any(v.endswith(":O") for v in vols)
    assert f"{p}/out:{p}/out" in vols and f"{p}/workbench:{p}/workbench" in vols
    assert any(c.startswith("/tmp:") for c in cmd), "scratch: the container's own /tmp"
