"""The run's sandbox (D680): `flux task run` and `flux ask` re-launch themselves in a Docker
container, so an agent's or a document's code cannot touch the rest of the machine.

The container is the host seen read-only: the system directories and `/nix/store` at their
real paths (the same binaries run: nix tools, OpenCode, Claude Code), the flux source, the
executables on PATH. Writable: the record's folder, the problem's `out/` and `workbench/`, the
places the command writes to (`--out`, `--json`, `flux ask --dir`), and the application's own
cache (D681): `~/.cache/flux/apps/<id>/`, shared by its runs, with `tmp/` (scratch, traces,
agents' directories), `home/` (HOME: the agents' sessions, kept across runs, their
configuration read-only, their credentials copied in) and `cache/` (XDG_CACHE_HOME). Another
application's traces, sessions and caches, the real home (`~/.ssh`, other repositories) and
the Docker socket are not there.

Network: the host's (`FLUX_SANDBOX_NET=open`, the default), or only the hosts an allowlist names
(`FLUX_SANDBOX_ALLOW=localai.example.org,api.anthropic.com,10.0.0.0/8`): the container has no
network, and a proxy on the host (a Unix socket) forwards to allowed hosts only.

On by default; `--no-sandbox` or `FLUX_SANDBOX=0` runs on the host.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

__all__ = ["IMAGE", "enabled", "in_sandbox", "launch", "mounts_for", "relay_proxy"]

#: A glibc base: the host's own libraries are mounted over it; only its shape is used.
IMAGE = os.environ.get("FLUX_SANDBOX_IMAGE", "debian:stable-slim")
SYSTEM = ("/usr", "/bin", "/sbin", "/lib", "/lib32", "/lib64", "/nix/store")
#: From /etc, what the tools read; Docker manages resolv.conf, hosts and hostname itself.
ETC = ("passwd", "group", "nsswitch.conf", "ssl", "ca-certificates", "ca-certificates.conf", "ld.so.cache",
       "ld.so.conf", "ld.so.conf.d", "localtime", "timezone", "alternatives", "gai.conf", "mime.types",
       "protocols", "services", "os-release", "lsb-release")
#: Environment the container never gets: the host's sessions and other services' secrets.
_DROP = ("HOME", "SSH_AUTH_SOCK", "SSH_AGENT_PID", "GPG_AGENT_INFO", "DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY",
         "DBUS_SESSION_BUS_ADDRESS", "XDG_RUNTIME_DIR", "DOCKER_HOST", "KRB5CCNAME", "VSCODE_IPC_HOOK_CLI")
_SECRETISH = ("TOKEN", "SECRET", "PASSWORD", "AWS_", "GITHUB_", "GH_", "AZURE_", "GOOGLE_APPLICATION")
#: The agents' configuration, read-only, and their credentials, copied (a refresh inside stays inside).
#: Not `~/.config/flux`: the host has read flux.env already and passes its settings in, so the
#: key file itself stays outside (only the key's value travels, in the environment, as agents need it).
_HOME_RO = (".config/opencode", ".opencode")
_HOME_COPY = (".claude/.credentials.json", ".claude.json", ".local/share/opencode/auth.json")
PROXY_PORT = 18080


def in_sandbox() -> bool:
    return os.environ.get("FLUX_SANDBOXED") == "1"


def enabled(args: Any) -> bool:
    if in_sandbox() or getattr(args, "no_sandbox", False):
        return False
    return os.environ.get("FLUX_SANDBOX", "1").lower() not in ("0", "off", "no", "false")


def _home() -> Path:
    return Path(os.environ.get("HOME") or Path.home())


def _cache() -> Path:
    return Path(os.environ.get("XDG_CACHE_HOME") or _home() / ".cache") / "flux"


def _exists(p: str | Path) -> bool:
    return Path(p).exists()


def app_dir(args: Any, command: str) -> Path:
    """The application's own cache (D681): `apps/<id>`, shared by all its runs; `flux ask` keys
    on its directory's name."""
    import re

    if command == "task run":
        doc = Path(args.file).resolve()
        try:
            from flux_loop import load_task

            ident = load_task(str(doc)).id
        except Exception:  # noqa: BLE001 -- a document the run itself will refuse: its file name
            ident = doc.name.split(".")[0]
        where = doc.parent
    else:
        where = Path(getattr(args, "dir", None) or os.getcwd()).resolve()
        ident = f"ask-{where.name}"
    key = re.sub(r"[^A-Za-z0-9_.-]+", "_", ident)[:80] or "unnamed"
    d = _cache() / "apps" / key
    for sub in ("tmp", "home", "cache"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    return d


def mounts_for(args: Any, command: str) -> tuple[list[str], list[str]]:
    """(read-only, writable) host paths the command needs, each mounted at its own path."""
    ro: list[str] = [p for p in SYSTEM if _exists(p)] + [f"/etc/{e}" for e in ETC if _exists(f"/etc/{e}")]
    rw: list[str] = []
    root = os.environ.get("FLUX_ROOT")
    if root:
        ro.append(root)
    ro.append(os.getcwd())
    for d in os.environ.get("PATH", "").split(os.pathsep):   # every executable directory the system mounts miss
        if d and _exists(d) and not any(d == s or d.startswith(s + "/") for s in SYSTEM) and d != "/usr/local/bin":
            ro.append(d)
            for f in Path(d).iterdir():                         # a link's target (a tool installed elsewhere)
                if f.is_symlink():
                    target = f.resolve()
                    if target.exists() and not any(str(target).startswith(s) for s in SYSTEM):
                        ro.append(str(target.parent))
    app = app_dir(args, command)
    rw += [str(app / "tmp"), str(app / "cache")]              # the application's own, nothing shared (D681)
    for flag in ("db", "out", "json"):
        v = getattr(args, flag, None)
        if v and v != ":memory:":
            p = Path(v).resolve().parent
            p.mkdir(parents=True, exist_ok=True)
            rw.append(str(p))
    for flag in ("plan", "replies", "author_replies"):
        v = getattr(args, flag, None)
        if v and _exists(v):
            ro.append(str(Path(v).resolve().parent))
    for f in list(getattr(args, "file", None) or []) if command == "ask" else []:
        if _exists(f):
            ro.append(str(Path(f).resolve().parent))
    for s in getattr(args, "skill", None) or []:
        if _exists(s):
            ro.append(str(Path(s).resolve()))
    if command == "task run":
        doc = Path(args.file).resolve()
        ro.append(str(doc.parent))
        for sub in ("out", "workbench"):                     # the run's own, under the problem
            (doc.parent / sub).mkdir(exist_ok=True)
            rw.append(str(doc.parent / sub))
    if command == "ask":
        d = Path(getattr(args, "dir", None) or os.getcwd()).resolve()
        d.mkdir(parents=True, exist_ok=True)
        rw.append(str(d))
    seen: set[str] = set()
    ro = [p for p in ro if not (p in seen or seen.add(p))]
    rw = [p for p in rw if not (p in seen or seen.add(p))]
    return ro, rw


def _sandbox_home(app: Path) -> Path:
    """The container's HOME, the application's, kept across its runs: the agents' sessions."""
    sh = app / "home"
    sh.mkdir(parents=True, exist_ok=True)
    home = _home()
    for rel in _HOME_RO:
        if (home / rel).exists():
            (sh / rel).mkdir(parents=True, exist_ok=True)       # the mount point, made by us, not by docker as root
    for rel in _HOME_COPY:
        src = home / rel
        if src.is_file():
            dst = sh / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
            dst.chmod(0o600)
    return sh


def _env() -> dict[str, str]:
    out = {}
    for k, v in os.environ.items():
        if k in _DROP or (any(s in k.upper() for s in _SECRETISH) and not k.startswith("FLUX_")):
            continue
        out[k] = v
    return out


def _allowlist() -> list[str]:
    raw = os.environ.get("FLUX_SANDBOX_ALLOW", "")
    return [h.strip() for h in raw.replace(";", ",").split(",") if h.strip()]


def docker_argv(argv: list[str], args: Any, command: str, name: str, proxy_dir: str | None) -> list[str]:
    ro, rw = mounts_for(args, command)
    app = app_dir(args, command)
    home, sh = _home(), _sandbox_home(app)
    for p in ro + rw:                                         # mount points under HOME: made by us, not by docker as root
        if p.startswith(str(home) + "/"):
            (sh / Path(p).relative_to(home)).mkdir(parents=True, exist_ok=True)
    cmd = ["docker", "run", "--rm", "--name", name, "--user", f"{os.getuid()}:{os.getgid()}",
           "--read-only", "--tmpfs", "/tmp:exec,mode=1777", "--tmpfs", "/run", "--cap-drop", "ALL",
           "--security-opt", "no-new-privileges", "--pids-limit", os.environ.get("FLUX_SANDBOX_PIDS", "4096"),
           "--workdir", os.getcwd(), "--label", "flux.sandbox=1"]
    if sys.stdin.isatty():
        cmd += ["-it"]
    else:
        cmd += ["-i"]
    for lim, flag in (("FLUX_SANDBOX_MEMORY", "--memory"), ("FLUX_SANDBOX_CPUS", "--cpus")):
        if os.environ.get(lim):
            cmd += [flag, os.environ[lim]]
    cmd += ["--network", "none" if proxy_dir else "host"]
    cmd += ["-v", f"{sh}:{home}"]                              # HOME: the sandbox's own
    for p in ro:
        cmd += ["-v", f"{p}:{p}:ro"]
    for rel in _HOME_RO:
        if (home / rel).exists():
            cmd += ["-v", f"{home / rel}:{home / rel}:ro"]
    for p in rw:
        cmd += ["-v", f"{p}:{p}"]
    env = _env()
    env.update(FLUX_SANDBOXED="1", FLUX_SANDBOX_NAME=name, HOME=str(home))
    tmp = str(app / "tmp")                                    # scratch and traces: the application's
    env.update(TMPDIR=tmp, TMP=tmp, TEMP=tmp, FLUX_TMPDIR=tmp, FLUX_TRACE_ROOT=str(app / "tmp" / "flux-traces"),
               XDG_CACHE_HOME=str(app / "cache"))
    if proxy_dir:
        cmd += ["-v", f"{proxy_dir}:{proxy_dir}"]
        env.update(FLUX_SANDBOX_PROXY=str(Path(proxy_dir) / "proxy.sock"))
        for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
            env[k] = f"http://127.0.0.1:{PROXY_PORT}"
        env["NO_PROXY"] = env["no_proxy"] = ""
    for k, v in env.items():
        cmd += ["-e", f"{k}={v}"]
    # PID 1: tini from the store (Docker's --init lives under /sbin, which is the host's here)
    init = shutil.which("tini")
    return cmd + [IMAGE, *([init, "-g", "--"] if init else []), *argv]


def _docker_ok() -> str:
    """"" when Docker answers, else why not."""
    if not shutil.which("docker"):
        return "docker is not installed"
    try:
        r = subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"docker did not answer ({exc})"
    return "" if r.returncode == 0 else (r.stderr.strip().splitlines() or ["docker info failed"])[-1]


def launch(argv: list[str], args: Any, command: str) -> int:
    """Run `flux <argv>` in the sandbox and return its exit code."""
    why = _docker_ok()
    if why:
        print(f"flux {command}: the sandbox needs Docker: {why}. `--no-sandbox` (or FLUX_SANDBOX=0) runs on "
              f"this machine directly.", file=sys.stderr)
        return 2
    name = f"flux-{uuid.uuid4().hex[:10]}"
    allow = _allowlist() if os.environ.get("FLUX_SANDBOX_NET", "open") == "allowlist" or _allowlist() else []
    proxy = None
    proxy_dir = None
    if allow:
        from .sandbox_proxy import AllowProxy

        # a local filesystem: a home on sshfs/NFS cannot hold a socket
        runtime = os.environ.get("XDG_RUNTIME_DIR") or "/tmp"
        proxy_dir = str(Path(runtime) / f"flux-sandbox-{os.getuid()}" / name)
        Path(proxy_dir).mkdir(parents=True, exist_ok=True, mode=0o700)
        proxy = AllowProxy(str(Path(proxy_dir) / "proxy.sock"), allow)
        proxy.start()
    exe = shutil.which("flux") or sys.argv[0]
    cmd = docker_argv([exe, *argv], args, command, name, proxy_dir)
    print(f"flux {command}: in the sandbox {name} (network: {'allowlist ' + ','.join(allow) if allow else 'open'}; "
          f"--no-sandbox to run on the host)", file=sys.stderr, flush=True)
    try:
        return subprocess.call(cmd)
    except KeyboardInterrupt:
        subprocess.run(["docker", "kill", "--signal", "INT", name], capture_output=True)
        return 130
    finally:
        if proxy is not None:
            proxy.stop()
            shutil.rmtree(proxy_dir, ignore_errors=True)


def relay_proxy() -> None:
    """Inside an allowlisted sandbox: 127.0.0.1:PROXY_PORT relayed to the host's proxy socket, so
    every HTTP(S)_PROXY client (urllib, Node, Bun) reaches the allowed hosts and nothing else."""
    sock_path = os.environ.get("FLUX_SANDBOX_PROXY")
    if not sock_path or not in_sandbox():
        return
    import socket
    import threading

    def pipe(a: socket.socket, b: socket.socket) -> None:
        try:
            while data := a.recv(65536):
                b.sendall(data)
        except OSError:
            pass
        finally:
            for s in (a, b):
                try:
                    s.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

    def serve() -> None:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            srv.bind(("127.0.0.1", PROXY_PORT))
        except OSError:
            return                                          # another flux in this sandbox relays already
        srv.listen(64)
        while True:
            client, _ = srv.accept()
            up = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                up.connect(sock_path)
            except OSError:
                client.close()
                continue
            for a, b in ((client, up), (up, client)):
                threading.Thread(target=pipe, args=(a, b), daemon=True).start()

    threading.Thread(target=serve, daemon=True, name="flux-sandbox-relay").start()
