"""The run's sandbox (D680, D682): `flux task run` and `flux ask` re-launch themselves in a
container -- rootless Podman when installed, else Docker (`FLUX_SANDBOX_ENGINE`) -- so an agent's or a document's code cannot touch the rest of the machine.

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

import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

__all__ = ["IMAGE", "app_dir", "container_argv", "enabled", "engine", "engine_cli", "in_sandbox", "launch", "mounts_for",
           "relay_proxy"]

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

    if command in ("task run", "task check"):
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
    # `flux serve` names it per user (D683): two users' applications of one id stay apart
    ident = os.environ.get("FLUX_SANDBOX_APP") or ident
    key = re.sub(r"[^A-Za-z0-9_.-]+", "_", ident)[:80] or "unnamed"
    d = _cache() / "apps" / key
    for sub in ("tmp", "home", "cache"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    return d


def mounts_for(args: Any, command: str) -> tuple[list[str], list[str]]:
    """(read-only, writable) host paths the command needs, each mounted at its own path."""
    # a merged-/usr host's /bin, /lib, ... are links into /usr: /usr covers them, and the root
    # directory holds the same links
    ro: list[str] = ([p for p in SYSTEM if _exists(p) and not Path(p).is_symlink()]
                     + [f"/etc/{e}" for e in ETC if _exists(f"/etc/{e}")])
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
    if command in ("task run", "task check"):
        doc = Path(args.file).resolve()
        ro.append(str(doc.parent))
        for sub in ("out", "workbench"):                     # the run's own, under the problem
            (doc.parent / sub).mkdir(exist_ok=True)
            rw.append(str(doc.parent / sub))
    if command == "consult":                                 # D705: the loop read-only, the answer's folder writable
        ro.append(str(Path(args.loop).resolve()))
        out = Path(args.out).resolve()
        out.mkdir(parents=True, exist_ok=True)
        rw.append(str(out))
    if command == "ask":
        d = Path(getattr(args, "dir", None) or os.getcwd()).resolve()
        d.mkdir(parents=True, exist_ok=True)
        rw.append(str(d))
    # D704: a path asked both ways is writable -- `flux ask --dir .` writes where it runs
    seen: set[str] = set()
    rw = [p for p in rw if not (p in seen or seen.add(p))]
    ro = [p for p in ro if not (p in seen or seen.add(p))]
    return ro, rw


def _home_list(var: str, fixed: tuple[str, ...]) -> tuple[str, ...]:
    """The fixed home paths and those `var` adds (D698: e.g. a modified agent's own config or
    credentials), comma-separated and relative to HOME; one that leaves HOME is ignored."""
    extra = []
    for rel in os.environ.get(var, "").split(","):
        rel = rel.strip()
        if rel.startswith("~/"):
            rel = rel[2:]
        if rel.startswith("/"):                               # outside HOME: not this
            continue
        rel = rel.strip("/")
        if rel and ".." not in Path(rel).parts:
            extra.append(rel)
    return tuple(dict.fromkeys((*fixed, *extra)))


def home_ro() -> tuple[str, ...]:
    return _home_list("FLUX_SANDBOX_HOME_RO", _HOME_RO)


def home_copy() -> tuple[str, ...]:
    return _home_list("FLUX_SANDBOX_HOME_COPY", _HOME_COPY)


def _sandbox_home(app: Path) -> Path:
    """The container's HOME, the application's, kept across its runs: the agents' sessions."""
    sh = app / "home"
    sh.mkdir(parents=True, exist_ok=True)
    home = _home()
    for rel in home_ro():
        if (home / rel).exists():
            mp = sh / rel                                     # the mount point, made by us, not by docker as root
            if (home / rel).is_dir():
                mp.mkdir(parents=True, exist_ok=True)
            else:
                mp.parent.mkdir(parents=True, exist_ok=True)
                mp.touch(exist_ok=True)
    for rel in home_copy():
        src = home / rel
        if src.exists() or src.is_symlink():
            _copy_over(src, sh / rel)
    return sh


def _copy_over(src: Path, dst: Path) -> None:
    """`src` copied onto `dst`, each run: what is there is replaced entry by entry -- a link
    copied as a link, never written through (it may point at the host's own file) -- and what
    only the copy has (an agent's sessions) is kept."""
    if dst.is_symlink() or (dst.exists() and dst.is_dir() != (src.is_dir() and not src.is_symlink())):
        shutil.rmtree(dst) if dst.is_dir() and not dst.is_symlink() else dst.unlink()
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.is_symlink():
        dst.symlink_to(os.readlink(src))
    elif src.is_dir():                                       # a folder of credentials: copied whole
        dst.mkdir(exist_ok=True)
        for p in src.iterdir():
            _copy_over(p, dst / p.name)
    elif src.is_file():
        shutil.copyfile(src, dst)
        dst.chmod(0o600)


def _env() -> dict[str, str]:
    # D697: the variables `flux serve` set for this run on purpose pass, whatever their names
    passed = {n for n in os.environ.get("FLUX_SANDBOX_PASS", "").split(",") if n}
    out = {}
    for k, v in os.environ.items():
        if k in _DROP or (any(s in k.upper() for s in _SECRETISH) and not k.startswith("FLUX_") and k not in passed):
            continue
        out[k] = v
    return out


def _allowlist() -> list[str]:
    raw = os.environ.get("FLUX_SANDBOX_ALLOW", "")
    return [h.strip() for h in raw.replace(";", ",").split(",") if h.strip()]


def engine() -> str:
    """`FLUX_SANDBOX_ENGINE`, else rootless Podman when installed (no root daemon: a container is
    one of your processes, D682), else Docker."""
    e = os.environ.get("FLUX_SANDBOX_ENGINE", "").strip().lower()
    if e in ("podman", "docker"):
        return e
    return "podman" if shutil.which("podman") else "docker"


def _local() -> Path:
    """The sandbox's local disk (a home on sshfs/NFS cannot hold container storage):
    `FLUX_SANDBOX_STORAGE`, else /var/tmp/flux-sandbox-<uid>. Podman's storage, its root directory."""
    return Path(os.environ.get("FLUX_SANDBOX_STORAGE") or f"/var/tmp/flux-sandbox-{os.getuid()}")


def engine_cli(eng: str) -> list[str]:
    """The engine's command, with where Podman keeps its state (the same for run, inspect, kill)."""
    if eng == "podman":
        run = Path(os.environ.get("XDG_RUNTIME_DIR") or "/tmp") / f"flux-podman-{os.getuid()}"
        return ["podman", "--root", str(_local() / "podman"), "--runroot", str(run), "--log-level", "error"]
    return ["docker"]


def _rootfs() -> Path:
    """Podman's root directory: empty but for the merged-/usr links and mount points; everything
    the run uses is mounted from the host. No image to pull."""
    root = _local() / "rootfs"
    for d in ("etc", "home", "tmp", "nix", "run", "proc", "dev", "sys", "usr", "var"):
        (root / d).mkdir(parents=True, exist_ok=True)
    for link in ("bin", "sbin", "lib", "lib32", "lib64"):
        p = root / link
        if not p.is_symlink():
            p.symlink_to(f"usr/{link}")
    return root


def container_argv(argv: list[str], args: Any, command: str, name: str, proxy_dir: str | None,
                   eng: str | None = None) -> list[str]:
    eng = eng or engine()
    ro, rw = mounts_for(args, command)
    app = app_dir(args, command)
    home, sh = _home(), _sandbox_home(app)
    # D706: a home path copied for each run is the run's copy. A read-only mount of it, or of a
    # folder inside it (a PATH directory, a link's target), would hide that copy: dropped. A
    # read-only folder above it would hide it too: the copy is mounted again on top of it.
    copied = [str(home / rel) for rel in home_copy() if (sh / rel).exists()]
    ro = [p for p in ro if not any(p == c or p.startswith(c + "/") for c in copied)]
    keep_ro = [str(home / rel) for rel in home_ro() if (home / rel).exists()
               and not any(str(home / rel) == c or str(home / rel).startswith(c + "/") for c in copied)]
    over = [c for c in copied if any(c.startswith(p + "/") for p in ro + keep_ro)]
    for p in ro + rw:                                         # mount points under HOME: made by us, not by the engine as root
        if p.startswith(str(home) + "/"):
            (sh / Path(p).relative_to(home)).mkdir(parents=True, exist_ok=True)
    cli = engine_cli(eng)
    # scratch on the container's own /tmp: with TMPDIR on any directory mounted from the host,
    # Yosys's abc step hangs (both engines, D682); the traces stay in the application's cache
    size = os.environ.get("FLUX_SANDBOX_TMP_SIZE")
    cmd = [*cli, "run", "--rm", "--name", name, "--read-only",
           "--tmpfs", "/tmp:exec,mode=1777" + (f",size={size}" if size else ""),
           "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
           "--pids-limit", os.environ.get("FLUX_SANDBOX_PIDS", "4096"),
           "--workdir", os.getcwd(), "--label", "flux.sandbox=1"]
    if os.environ.get("FLUX_SANDBOX_APP"):
        cmd += ["--label", f"flux.app={os.environ['FLUX_SANDBOX_APP']}"]    # `flux serve`'s admin finds its loop (D695)
    if eng == "docker":
        # the daemon is root: run as you; PID 1 is tini (Docker's --init lives under /sbin, the host's here)
        cmd += ["--user", f"{os.getuid()}:{os.getgid()}", "--tmpfs", "/run"]
    else:
        # rootless: root inside is you outside; Podman's own init is PID 1
        cmd += ["--init"]
    cmd += ["-it"] if sys.stdin.isatty() else ["-i"]
    for lim, flag in (("FLUX_SANDBOX_MEMORY", "--memory"), ("FLUX_SANDBOX_CPUS", "--cpus")):
        if os.environ.get(lim):
            cmd += [flag, os.environ[lim]]
    cmd += ["--network", "none" if proxy_dir else "host"]
    cmd += ["-v", f"{sh}:{home}"]                              # HOME: the application's
    for p in ro:
        cmd += ["-v", f"{p}:{p}:ro"]
    for p in keep_ro:
        cmd += ["-v", f"{p}:{p}:ro"]
    for c in over:
        cmd += ["-v", f"{sh / Path(c).relative_to(home)}:{c}"]
    for p in rw:
        cmd += ["-v", f"{p}:{p}"]
    env = _env()
    env.update(FLUX_SANDBOXED="1", FLUX_SANDBOX_NAME=name, HOME=str(home), FLUX_SANDBOX_CLI=json.dumps(cli))
    env.update(TMPDIR="/tmp", TMP="/tmp", TEMP="/tmp", FLUX_TMPDIR="/tmp",
               FLUX_TRACE_ROOT=str(app / "tmp" / "flux-traces"), XDG_CACHE_HOME=str(app / "cache"))
    if proxy_dir:
        cmd += ["-v", f"{proxy_dir}:{proxy_dir}"]
        env.update(FLUX_SANDBOX_PROXY=str(Path(proxy_dir) / "proxy.sock"))
        for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
            env[k] = f"http://127.0.0.1:{PROXY_PORT}"
        env["NO_PROXY"] = env["no_proxy"] = ""
    for k, v in env.items():
        cmd += ["-e", f"{k}={v}"]
    if eng == "podman":
        return cmd + ["--rootfs", str(_rootfs()), *argv]
    init = shutil.which("tini")
    return cmd + [IMAGE, *([init, "-g", "--"] if init else []), *argv]


def _engine_ok(eng: str) -> str:
    """"" when the engine answers, else why not."""
    if not shutil.which(eng):
        return f"{eng} is not installed"
    probe = ["info", "--format", "{{.Host.Security.Rootless}}" if eng == "podman" else "{{.ServerVersion}}"]
    try:
        r = subprocess.run([*engine_cli(eng), *probe], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"{eng} did not answer ({exc})"
    if r.returncode != 0:
        return (r.stderr.strip().splitlines() or [f"{eng} info failed"])[-1]
    if eng == "podman" and r.stdout.strip() != "true":
        return "podman is not rootless here; run flux as your own user"
    return ""


def launch(argv: list[str], args: Any, command: str) -> int:
    """Run `flux <argv>` in the sandbox and return its exit code."""
    eng = engine()
    why = _engine_ok(eng)
    if why:
        print(f"flux {command}: the sandbox needs Podman or Docker: {why}. `--no-sandbox` (or FLUX_SANDBOX=0) "
              f"runs on this machine directly.", file=sys.stderr)
        return 2
    name = f"flux-{uuid.uuid4().hex[:10]}"
    strict = os.environ.get("FLUX_SANDBOX_NET", "open") == "allowlist"
    allow = _allowlist()
    proxy = None
    proxy_dir = None
    if allow or strict:                                       # D698: an empty allowlist refuses all, never opens
        from .sandbox_proxy import AllowProxy

        # a local filesystem: a home on sshfs/NFS cannot hold a socket
        runtime = os.environ.get("XDG_RUNTIME_DIR") or "/tmp"
        proxy_dir = str(Path(runtime) / f"flux-sandbox-{os.getuid()}" / name)
        Path(proxy_dir).mkdir(parents=True, exist_ok=True, mode=0o700)
        proxy = AllowProxy(str(Path(proxy_dir) / "proxy.sock"), allow, log=os.environ.get("FLUX_SANDBOX_REFUSALS"),
                           about={"app": os.environ.get("FLUX_SANDBOX_APP", ""), "command": command, "container": name})
        proxy.start()
    exe = shutil.which("flux") or sys.argv[0]
    cmd = container_argv([exe, *argv], args, command, name, proxy_dir, eng)
    print(f"flux {command}: in the {eng} sandbox {name} (network: {('allowlist ' + ','.join(allow)) if allow else 'none (an empty allowlist)' if strict else 'open'}; "
          f"--no-sandbox to run on the host)", file=sys.stderr, flush=True)
    try:
        return subprocess.call(cmd)
    except KeyboardInterrupt:
        subprocess.run([*engine_cli(eng), "kill", "--signal", "INT", name], capture_output=True)
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
