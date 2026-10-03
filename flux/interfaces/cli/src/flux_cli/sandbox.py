"""The run's sandbox (D680, D682): `flux task run` and `flux ask` re-launch themselves in a
container -- rootless Podman when installed, else Docker (`FLUX_SANDBOX_ENGINE`) -- so an agent's or a document's code cannot touch the rest of the machine.

The container is the host seen read-only: the system directories and `/nix/store` at their
real paths (the same binaries run: nix tools, OpenCode, Claude Code), the flux source, the
executables on PATH, and the program a `FLUX_<AGENT>_BIN` names (the file alone, D804). Writable: the record's folder, the problem's `out/` and `workbench/` (a sub-loop's,
its parent's: D805), the
places the command writes to (`--out`, `--json`, `flux ask --dir`), and the application's own
cache (D681): `~/.cache/flux/apps/<id>/`, shared by its runs, with `tmp/` (scratch, traces,
agents' directories); and HOME (D744): the user's own Flux home,
writable, at `/home/flux` -- their agents' configuration, logins and sessions, kept from run to
run and refreshed in place. Another application's traces and caches, another user's home, the
real home (`~/.ssh`, other repositories) and the Docker socket are not there.

Network: the host's (`FLUX_SANDBOX_NET=open`, the default), or only the hosts an allowlist names
(`FLUX_SANDBOX_ALLOW=localai.example.org,api.anthropic.com,10.0.0.0/8`): the container has no
network, and a proxy on the host (a Unix socket) forwards to allowed hosts only -- through the
host's own proxy when it has one (D722); the container's loopback is its own, never proxied.

Certificates (D722): the host's trust store is the container's -- `/etc/ssl`, `/etc/pki`,
`/usr/local/share/ca-certificates` (under `/usr`), the files the host's `SSL_CERT_FILE`,
`NODE_EXTRA_CA_CERTS`, ... name -- and Node/Bun agents (OpenCode, Claude Code), which carry their own roots, are given the
system bundle as `NODE_EXTRA_CA_CERTS`: a corporate proxy's root CA is trusted inside as outside.

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

__all__ = ["HOME_IN", "IMAGE", "agent_programs", "app_dir", "container_argv", "container_env", "enabled", "engine", "engine_cli", "flux_home", "in_sandbox",
           "launch", "mounts_for", "relay_proxy", "seed_home"]

#: A glibc base: the host's own libraries are mounted over it; only its shape is used.
IMAGE = os.environ.get("FLUX_SANDBOX_IMAGE", "debian:stable-slim")
SYSTEM = ("/usr", "/bin", "/sbin", "/lib", "/lib32", "/lib64", "/nix/store")
#: From /etc, what the tools read; Docker manages resolv.conf, hosts and hostname itself.
ETC = ("passwd", "group", "nsswitch.conf", "ssl", "pki", "ca-certificates", "ca-certificates.conf", "ld.so.cache",
       "ld.so.conf", "ld.so.conf.d", "localtime", "timezone", "alternatives", "gai.conf", "mime.types",
       "protocols", "services", "os-release", "lsb-release")
#: Environment the container never gets: the host's sessions and other services' secrets.
_DROP = ("HOME", "FLUX_SANDBOX_HOME", "FLUX_SANDBOX_TIMEOUT", "SSH_AUTH_SOCK", "SSH_AGENT_PID", "GPG_AGENT_INFO", "DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY",
         "DBUS_SESSION_BUS_ADDRESS", "XDG_RUNTIME_DIR", "DOCKER_HOST", "KRB5CCNAME", "VSCODE_IPC_HOOK_CLI",
         # D716: the network's rules and the refusals file are the proxy's, outside: not the run's to read
         "FLUX_SANDBOX_ALLOW", "FLUX_SANDBOX_NET", "FLUX_SANDBOX_REFUSALS")
_SECRETISH = ("TOKEN", "SECRET", "PASSWORD", "AWS_", "GITHUB_", "GH_", "AZURE_", "GOOGLE_APPLICATION")
#: HOME inside (D744): the user's Flux home, at a path of its own -- this machine's folders under
#: its own HOME (PATH folders, the flux source) are mounted at their own paths, and would otherwise
#: make mount points in the user's home.
HOME_IN = "/home/flux"
#: On one's own machine, the Flux home starts with the agents' configuration and logins of the
#: real home, where it lacks them. Not `~/.config/flux`: the host has read flux.env already and
#: passes its settings in, so the key file itself stays outside.
SEED_OWN = (".config/opencode", ".claude.json", ".claude/.credentials.json", ".claude/settings.json",
            ".local/share/opencode/auth.json", ".codex/auth.json", ".codex/config.toml",
            # D765: a corporate OpenCode's own parts beside its login (its plugins' tools and assets, its state)
            ".local/share/opencode/mapper", ".local/share/opencode/icons", ".local/share/opencode/mock-tools",
            ".local/share/opencode/request-utils", ".local/share/opencode/images", ".local/state/opencode/kv2.json")
PROXY_PORT = 18080
#: D722: certificates the host's tools are told of, wherever they are (a corporate CA in a home)
CA_VARS = ("SSL_CERT_FILE", "SSL_CERT_DIR", "NODE_EXTRA_CA_CERTS", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE",
           "GIT_SSL_CAINFO", "NIX_SSL_CERT_FILE", "PIP_CERT")
#: the system bundle, by distribution: Debian/Ubuntu/Arch, RHEL/Fedora, SUSE, Alpine
BUNDLES = ("/etc/ssl/certs/ca-certificates.crt", "/etc/pki/tls/certs/ca-bundle.crt", "/etc/ssl/ca-bundle.pem",
           "/etc/ssl/cert.pem")


def in_sandbox() -> bool:
    return os.environ.get("FLUX_SANDBOXED") == "1"


def enabled(args: Any) -> bool:
    if in_sandbox() or getattr(args, "no_sandbox", False):
        return False
    return os.environ.get("FLUX_SANDBOX", "1").lower() not in ("0", "off", "no", "false")


def _home() -> Path:
    return Path(os.environ.get("HOME") or Path.home())


def flux_home(args: Any = None, command: str = "") -> Path:
    """The HOME a run gets (D744): `flux login --home DIR`'s; `FLUX_SANDBOX_HOME` (`flux serve`:
    the home of the user who starts it); else this machine user's Flux home,
    `~/.local/share/flux/home`, started from the real home's agent files (`SEED_OWN`)."""
    if command == "login":
        h = Path(args.home)
    elif os.environ.get("FLUX_SANDBOX_HOME"):
        h = Path(os.environ["FLUX_SANDBOX_HOME"])
    else:
        h = Path(os.environ.get("XDG_DATA_HOME") or _home() / ".local" / "share") / "flux" / "home"
        h.mkdir(parents=True, exist_ok=True)
        seed_home(h, _home(), SEED_OWN)
    h.mkdir(parents=True, exist_ok=True)
    os.chmod(h, 0o700)
    return h.resolve()


def seed_home(home: Path, src: Path, rels: Any) -> list[str]:
    """`home` started from `src` (D744): each path copied where `home` lacks it -- a folder merged
    file by file, a link as a link -- never over what is there, so what the user changed or
    logged into stays theirs. Returns the paths copied. A path that leaves the home is skipped."""
    done: list[str] = []

    def merge(a: Path, b: Path, rel: str) -> None:
        if b.is_symlink() or (b.exists() and not (a.is_dir() and not a.is_symlink() and b.is_dir())):
            return
        if a.is_symlink():
            b.parent.mkdir(parents=True, exist_ok=True)
            b.symlink_to(os.readlink(a))
            done.append(rel)
        elif a.is_dir():
            b.mkdir(parents=True, exist_ok=True)
            for c in sorted(a.iterdir()):
                merge(c, b / c.name, f"{rel}/{c.name}")
        elif a.is_file():
            b.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(a, b)                                 # its mode and time: a program stays one, a key stays 0600
            done.append(rel)

    for rel in rels or ():
        r = str(rel).strip().removeprefix("~/").strip("/")
        if not r or r.startswith("/") or ".." in Path(r).parts:
            continue
        a = src / r
        if a.exists() or a.is_symlink():
            try:
                merge(a, home / r, r)
            except OSError:
                pass                                           # what cannot be read is not copied
    return done


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
    elif command in ("login", "agent test"):
        ident = command.replace(" ", "-")
    else:
        where = Path(getattr(args, "dir", None) or os.getcwd()).resolve()
        ident = f"ask-{where.name}"
    # `flux serve` names it per user (D683): two users' applications of one id stay apart
    ident = os.environ.get("FLUX_SANDBOX_APP") or ident
    key = re.sub(r"[^A-Za-z0-9_.-]+", "_", ident)[:80] or "unnamed"
    d = _cache() / "apps" / key
    for sub in ("tmp",):
        (d / sub).mkdir(parents=True, exist_ok=True)
    return d


def _top(doc: Path) -> Path:
    """The folder a run of `doc` reads and writes under: the document's own, or for a sub-loop in
    a folder its parent's -- the parent's document, knowledge and `out/` (D802), `workbench/` (D805)."""
    try:
        from flux_loop import load_task

        wb = load_task(str(doc)).workbench
        return Path(wb).parent if wb else doc.parent
    except Exception:  # noqa: BLE001 -- a document the run itself will refuse: its own folder
        return doc.parent


def mounts_for(args: Any, command: str) -> tuple[list[str], list[str]]:
    """(read-only, writable) host paths the command needs, each mounted at its own path."""
    # a merged-/usr host's /bin, /lib, ... are links into /usr: /usr covers them, and the root
    # directory holds the same links
    ro: list[str] = ([p for p in SYSTEM if _exists(p) and not Path(p).is_symlink()]
                     + [f"/etc/{e}" for e in ETC if _exists(f"/etc/{e}")])
    rw: list[str] = []
    for var in CA_VARS:                                       # D722: one the system mounts miss
        v = os.environ.get(var, "")
        if v and Path(v).is_absolute() and _exists(v) and not any(v == m or v.startswith(m + "/") for m in ro):
            ro.append(v)
    root = os.environ.get("FLUX_ROOT")
    if root:
        ro.append(root)
    ro.append(os.getcwd())
    # D714: the Python running flux -- a pip venv, its interpreter, an editable install's source --
    # wherever it lives; without it a `pip install` user's flux cannot import itself inside
    for d in (sys.prefix, sys.base_prefix, *sys.path):
        if d and _exists(d) and Path(d).is_dir() and not any(d == s or d.startswith(s + "/") for s in SYSTEM):
            ro.append(str(Path(d).resolve()))
    for d in os.environ.get("PATH", "").split(os.pathsep):   # every executable directory the system mounts miss
        if d and _exists(d) and not any(d == s or d.startswith(s + "/") for s in SYSTEM) and d != "/usr/local/bin":
            ro.append(d)
            for f in Path(d).iterdir():                         # a link's target (a tool installed elsewhere)
                if f.is_symlink():
                    target = f.resolve()
                    if target.exists() and not any(str(target).startswith(s) for s in SYSTEM):
                        ro.append(str(target.parent))
    for prog in agent_programs().values():                   # D804: the program an admin names, the file alone
        if not any(prog.startswith(m + "/") for m in (*SYSTEM, *ro)):
            ro.append(prog)
    app = app_dir(args, command)
    rw += [str(app / "tmp")]                                  # the application's own, nothing shared (D681)
    for flag in ("db", "out", "json"):
        v = getattr(args, flag, None)
        if v and v not in (":memory:", "-"):
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
        top = _top(doc)
        ro.append(str(top))                                  # a sub-loop's: its parent's folder, read through it
        # D735: the papers the run reads -- the shared library where FLUX_LIBRARY moved it (the
        # loop's own `library/` is under its folder, mounted already)
        lib = os.environ.get("FLUX_LIBRARY")
        if lib and _exists(lib):
            ro.append(str(Path(lib).resolve()))
        for sub in ("out", "workbench"):                     # the run's own, under the problem (D805: a sub-loop's, its parent's)
            (top / sub).mkdir(exist_ok=True)
            rw.append(str(top / sub))
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


def agent_programs() -> dict[str, str]:
    """FLUX_<AGENT>_BIN -> the program it names, its links followed (D804): mounted read-only at
    that path, the file alone -- the agents ship as one native file each, and the folder beside it
    (Flux's own settings with the model's key, the real home) stays outside -- and named so inside,
    where the link itself is not."""
    out = {}
    for k, v in os.environ.items():
        if k.startswith("FLUX_") and k.endswith("_BIN") and v:
            f = Path(os.path.expanduser(v))
            if f.is_absolute() and f.is_file():
                out[k] = str(f.resolve())
    return out


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


def run_dir(name: str) -> Path:
    """The run's own folder on this machine's runtime disk (0700): its variables' file, the
    proxy's socket. Removed when the run ends."""
    d = Path(os.environ.get("XDG_RUNTIME_DIR") or "/tmp") / f"flux-sandbox-{os.getuid()}" / name
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(d, 0o700)
    return d


def container_env(cmd: list[str]) -> dict[str, str]:
    """The variables a container command gives (its `--env-file`, then `-e NAME`, from this
    process's environment): what a test or an admin reads, not what `ps` shows."""
    out: dict[str, str] = {}
    for flag, val in zip(cmd, cmd[1:]):
        if flag == "--env-file":
            for line in Path(val).read_text().splitlines():
                k, _, v = line.partition("=")
                out[k] = v
        elif flag == "-e":
            k, eq, v = val.partition("=")
            out[k] = v if eq else os.environ.get(k, "")
    return out


def container_argv(argv: list[str], args: Any, command: str, name: str, proxy_dir: str | None,
                   eng: str | None = None) -> list[str]:
    eng = eng or engine()
    ro, rw = mounts_for(args, command)
    app = app_dir(args, command)
    home = flux_home(args, command)                           # D744: the user's own, writable, kept
    cli = engine_cli(eng)
    # scratch on the container's own /tmp: with TMPDIR on any directory mounted from the host,
    # Yosys's abc step hangs (both engines, D682); the traces stay in the application's cache
    size = os.environ.get("FLUX_SANDBOX_TMP_SIZE")
    cmd = [*cli, "run", "--rm", "--name", name, "--read-only",
           "--tmpfs", "/tmp:exec,mode=1777" + (f",size={size}" if size else ""),
           # D744: this machine's own HOME path is scratch inside -- its folders mounted on it (PATH,
           # the loops' caches) are at their places, and a tool that writes beside its install
           # (OpenCode's `~/.opencode/.gitignore`, found walking up from a loop's cache) can
           "--tmpfs", f"{_home()}:exec,mode=0700",
           "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
           "--pids-limit", os.environ.get("FLUX_SANDBOX_PIDS", "4096"),
           "--workdir", os.getcwd(), "--label", "flux.sandbox=1"]
    if eng == "podman" and (os.environ.get("FLUX_SANDBOX_TIMEOUT") or "").isdigit():
        cmd += ["--timeout", os.environ["FLUX_SANDBOX_TIMEOUT"]]   # D768: ended by Podman itself, its client gone or not
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
    if proxy_dir:
        # D717: a name lookup is asked of the relay inside (127.0.0.1:53), which asks the proxy --
        # a program that ignores HTTP(S)_PROXY still looks its host up, and that is seen and audited
        resolv = Path(proxy_dir) / "resolv.conf"
        try:
            resolv.write_text("nameserver 127.0.0.1\noptions attempts:1 timeout:2\n")
            cmd += ["--sysctl", "net.ipv4.ip_unprivileged_port_start=53", "-v", f"{resolv}:/etc/resolv.conf:ro"]
        except OSError:
            pass                                              # no lookups seen; the proxy still filters
    cmd += ["-v", f"{home}:{HOME_IN}"]                            # HOME: the user's Flux home
    for p in ro:
        cmd += ["-v", f"{p}:{p}:ro"]
    for p in rw:
        cmd += ["-v", f"{p}:{p}"]
    env = _env()
    env.update(FLUX_SANDBOXED="1", FLUX_SANDBOX_NAME=name, HOME=HOME_IN, FLUX_SANDBOX_CLI=json.dumps(cli))
    env.update(agent_programs())                              # D804: the programs at the paths mounted
    env.update(OPENCODE_SKIP_SAFE_CHECK="1")                  # D714: the container is the safety; OpenCode's own check refuses it
    if not env.get("NODE_EXTRA_CA_CERTS"):                    # D722: Node/Bun trust their own roots only; the host's too
        bundle = next((b for b in (env.get("SSL_CERT_FILE", ""), *BUNDLES) if b and Path(b).is_file()), None)
        if bundle:
            env["NODE_EXTRA_CA_CERTS"] = bundle
    env.update(TMPDIR="/tmp", TMP="/tmp", TEMP="/tmp", FLUX_TMPDIR="/tmp",
               FLUX_TRACE_ROOT=str(app / "tmp" / "flux-traces"), XDG_CACHE_HOME=f"{HOME_IN}/.cache")
    if proxy_dir:
        cmd += ["-v", f"{proxy_dir}:{proxy_dir}"]
        env.update(FLUX_SANDBOX_PROXY=str(Path(proxy_dir) / "proxy.sock"))
        for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
            env[k] = f"http://127.0.0.1:{PROXY_PORT}"
        # D722: the container's loopback is its own (an agent's local server, its event stream);
        # through the proxy it would be refused, or be the host's
        env["NO_PROXY"] = env["no_proxy"] = "localhost,127.0.0.1,::1"
    # D745: never a value on the command line -- `ps` shows it to every user of the machine (the
    # model's key was there). One line each in a file of the run's own (0600); a value of
    # several lines (which a file of variables cannot hold) by name, from this process's environment
    lines = []
    for k, v in env.items():
        if "\n" in v or "\r" in v:
            if os.environ.get(k) == v:
                cmd += ["-e", k]
        else:
            lines.append(f"{k}={v}")
    env_file = run_dir(name) / "env"
    fd = os.open(env_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write("\n".join(lines) + "\n")
    cmd += ["--env-file", str(env_file)]
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
    mine = run_dir(name)                                      # a local filesystem: a home on sshfs/NFS cannot hold a socket
    if allow or strict:                                       # D698: an empty allowlist refuses all, never opens
        from .sandbox_proxy import AllowProxy

        proxy_dir = str(mine)
        proxy = AllowProxy(str(Path(proxy_dir) / "proxy.sock"), allow, log=os.environ.get("FLUX_SANDBOX_REFUSALS"),
                           about={"app": os.environ.get("FLUX_SANDBOX_APP", ""), "command": command, "container": name})
        proxy.start()
    # D714: the flux that is running, by its own interpreter -- not whichever `flux` PATH finds
    # first, nor `sys.argv[0]`, which under `python -m` is a source file, not a program
    cmd = container_argv([sys.executable, "-m", "flux_cli", *argv], args, command, name, proxy_dir, eng)
    # D720: the run's log says where it runs, not how its network is limited nor how to leave the sandbox
    print(f"flux {command}: in the {eng} sandbox {name}", file=sys.stderr, flush=True)
    try:
        return subprocess.call(cmd)
    except KeyboardInterrupt:
        subprocess.run([*engine_cli(eng), "kill", "--signal", "INT", name], capture_output=True)
        return 130
    finally:
        if proxy is not None:
            proxy.stop()
        shutil.rmtree(mine, ignore_errors=True)              # the variables' file with it


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
    threading.Thread(target=_dns_relay, args=(sock_path,), daemon=True, name="flux-sandbox-dns").start()


def _dns_name(q: bytes) -> str | None:
    """The name a DNS query asks for (its first question), or None."""
    at, labels = 12, []
    try:
        while q[at]:
            n = q[at]
            if n & 0xC0:
                return None
            labels.append(q[at + 1:at + 1 + n].decode("ascii", "replace"))
            at += 1 + n
    except IndexError:
        return None
    return ".".join(labels).lower() or None


def _dns_relay(sock_path: str) -> None:
    """127.0.0.1:53 inside (D717): each name looked up is asked of the proxy -- refused and
    recorded when the allowlist does not name it -- and answered with no address: a direct
    connection cannot leave anyway, only the proxy's can. REFUSED for a refused name, SERVFAIL
    for an allowed one (go through the proxy)."""
    import socket

    srv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        srv.bind(("127.0.0.1", 53))
    except OSError:
        return                                              # another flux relays, or the port is not ours
    said: dict[str, bool] = {}
    while True:
        try:
            q, addr = srv.recvfrom(1500)
        except OSError:
            return
        if len(q) < 12:
            continue
        name = _dns_name(q)
        ok = said.get(name or "")
        if name and ok is None:
            ok = False
            try:
                up = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                up.settimeout(10)
                up.connect(sock_path)
                up.sendall(f"FLUX-LOOKUP {name} HTTP/1.1\r\n\r\n".encode())
                ok = b" 204 " in up.recv(256)
                up.close()
            except OSError:
                pass
            said[name] = ok
        rcode = 2 if ok else 5
        flags = 0x8000 | (q[2] & 0x01) << 8 | 0x0080 | rcode       # a response, RD echoed, RA, the code
        try:
            srv.sendto(q[:2] + flags.to_bytes(2, "big") + q[4:6] + b"\x00\x00\x00\x00\x00\x00" + q[12:], addr)
        except OSError:
            pass
