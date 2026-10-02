"""What the server's machine holds up (D695): the machine (CPUs, load, memory, disks), the
sandbox containers (CPU, memory, PIDs, each with its loop by the `flux.app` label), and the disk
each loop takes -- its inputs, record, log, workbench and its sandbox cache
(`~/.cache/flux/apps/<user>.<app>/`). A cache no loop owns any more is said, and can go.

Sizes are walked, so they are kept for a minute. Nothing here runs a loop's code."""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import time
from pathlib import Path
from typing import Any

__all__ = ["cache_root", "caches", "clean", "containers", "dir_size", "kill_container", "loop_disk", "machine"]

_SIZES: dict[str, tuple[float, int]] = {}
_TTL = 60.0


def dir_size(path: str | Path, *, fresh: bool = False) -> int:
    """Bytes under `path` (links not followed), kept for a minute."""
    p = str(path)
    now = time.time()
    got = _SIZES.get(p)
    if got and not fresh and now - got[0] < _TTL:
        return got[1]
    total = 0
    stack = [p]
    while stack:
        d = stack.pop()
        try:
            with os.scandir(d) as it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            stack.append(e.path)
                        else:
                            total += e.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
        except OSError:
            continue
    _SIZES[p] = (now, total)
    return total


def cache_root() -> Path:
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "flux" / "apps"


def _key(user: str, app: str) -> str:
    """The sandbox's key of a loop's cache, as `flux_cli.sandbox.app_dir` makes it."""
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", f"{user}.{app}")[:80] or "unnamed"


def machine(paths: dict[str, str]) -> dict[str, Any]:
    """CPUs, load, memory and the free space where the server's data, the caches and the sandbox
    storage live."""
    out: dict[str, Any] = {"cpus": os.cpu_count() or 1, "load": list(os.getloadavg()) if hasattr(os, "getloadavg") else []}
    mem: dict[str, int] = {}
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            k, _, v = line.partition(":")
            if k in ("MemTotal", "MemAvailable"):
                mem[k] = int(v.split()[0]) * 1024
    except OSError:
        pass
    out["memory"] = {"total": mem.get("MemTotal"), "available": mem.get("MemAvailable")}
    disks: list[dict[str, Any]] = []
    for label, p in paths.items():
        try:
            u = shutil.disk_usage(p)
            dev = os.stat(p).st_dev
        except OSError:
            continue
        disks.append({"label": label, "path": p, "total": u.total, "used": u.used, "free": u.free,
                      "same_as": next((d["label"] for d in disks if d.get("_dev") == dev), None), "_dev": dev})
    for d in disks:
        d.pop("_dev", None)
    out["disks"] = disks
    return out


_UNITS = {"b": 1, "kb": 1e3, "mb": 1e6, "gb": 1e9, "tb": 1e12, "kib": 1024, "mib": 1024**2, "gib": 1024**3, "tib": 1024**4}


def _bytes(text: str) -> float | None:
    m = re.match(r"\s*([\d.]+)\s*([A-Za-z]*)", text or "")
    if not m:
        return None
    return float(m.group(1)) * _UNITS.get(m.group(2).lower() or "b", 1)


def _pct(text: Any) -> float | None:
    try:
        return float(str(text).strip().rstrip("%"))
    except ValueError:
        return None


def containers() -> dict[str, Any]:
    """The sandbox's containers (`flux.sandbox=1`), running or left behind, with their usage."""
    try:
        from flux_cli.sandbox import engine, engine_cli
    except ImportError:
        return {"engine": None, "containers": [], "error": "no sandbox here"}
    eng = engine()
    if not shutil.which(eng):
        return {"engine": eng, "containers": [], "error": f"{eng} is not installed"}
    cli = engine_cli(eng)
    try:
        r = subprocess.run([*cli, "ps", "-a", "--filter", "label=flux.sandbox=1", "--format", "json" if eng == "podman" else "{{json .}}"],
                           capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"engine": eng, "containers": [], "error": str(exc)}
    if r.returncode != 0:
        return {"engine": eng, "containers": [], "error": (r.stderr.strip().splitlines() or ["ps failed"])[-1]}
    raw = r.stdout.strip()
    rows = json.loads(raw) if raw.startswith("[") else [json.loads(x) for x in raw.splitlines() if x.strip()]
    out = []
    for c in rows or []:
        names = c.get("Names")
        name = names[0] if isinstance(names, list) and names else str(names or c.get("Name") or "")
        labels = c.get("Labels") or {}
        if isinstance(labels, str):                              # docker: "a=b,c=d"
            labels = dict(x.split("=", 1) for x in labels.split(",") if "=" in x)
        out.append({"name": name, "state": str(c.get("State") or "").lower(), "status": c.get("Status") or "",
                    "started": c.get("StartedAt") if isinstance(c.get("StartedAt"), (int, float)) else None,
                    "app": labels.get("flux.app")})
    running = [c["name"] for c in out if c["state"] == "running"]
    if running:
        try:
            s = subprocess.run([*cli, "stats", "--no-stream", "--format", "json" if eng == "podman" else "{{json .}}", *running],
                               capture_output=True, text=True, timeout=30)
            raw = s.stdout.strip()
            stats = json.loads(raw) if raw.startswith("[") else [json.loads(x) for x in raw.splitlines() if x.strip()]
        except (OSError, subprocess.TimeoutExpired, ValueError):
            stats = []
        by = {str(x.get("name") or x.get("Name") or ""): x for x in stats or []}
        for c in out:
            x = by.get(c["name"])
            if not x:
                continue
            mem = str(x.get("mem_usage") or x.get("MemUsage") or "")
            used, _, limit = mem.partition("/")
            c.update(cpu=_pct(x.get("cpu_percent") or x.get("CPUPerc")), mem=_bytes(used), mem_limit=_bytes(limit),
                     pids=int(x.get("pids") or x.get("PIDs") or 0) or None)
    return {"engine": eng, "containers": out, "error": None}


def kill_container(name: str) -> str:
    """A sandbox container stopped and removed: one left behind by a run that is gone."""
    from flux_cli.sandbox import engine, engine_cli

    if not re.fullmatch(r"flux-[0-9a-f]{6,32}", name):
        raise ValueError("not a sandbox container's name")
    cli = engine_cli(engine())
    subprocess.run([*cli, "kill", name], capture_output=True, text=True, timeout=60)
    r = subprocess.run([*cli, "rm", "-f", name], capture_output=True, text=True, timeout=60)
    return "removed" if r.returncode == 0 else (r.stderr.strip() or "not removed")


def attached() -> dict[str, int]:
    """The sandbox containers a process on this machine still runs -- the `run --name flux-…`
    client a loop, a login or a Test started and reads -- each with that client's pid."""
    out: dict[str, int] = {}
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as fh:
                argv = fh.read().split(b"\0")
        except OSError:
            continue
        if b"run" in argv and b"--name" in argv:
            i = argv.index(b"--name")
            if i + 1 < len(argv) and argv[i + 1].startswith(b"flux-"):
                out[argv[i + 1].decode("utf-8", "replace")] = int(pid)
    return out


def _ancestors(pid: int) -> set[int]:
    seen: set[int] = set()
    while pid > 1 and pid not in seen:
        seen.add(pid)
        try:
            with open(f"/proc/{pid}/status") as fh:
                pid = int(next(ln.split()[1] for ln in fh if ln.startswith("PPid:")))
        except (OSError, StopIteration, ValueError):
            break
    return seen


#: Containers the server itself waits on (a login, an agent's Test): a server that restarted no
#: longer reads them, though their client lives on. A loop's run outlives a restart on purpose.
SERVERS_OWN = (".login", ".agent-test")


def reap(grace_s: float = 120.0, now: float | None = None) -> list[dict[str, Any]]:
    """D768: the sandbox containers no process runs any more -- a client killed with the server, a
    login or Test whose request is gone -- stopped and removed, after `grace_s` of their life (a
    client being started is not mistaken for one gone); a login's or Test's whose client is not this
    server's own (one before a restart) with its client. Those removed."""
    now = time.time() if now is None else now
    got = containers()
    if got.get("error"):
        return []
    held = attached()
    gone = []
    for c in got["containers"]:
        if c["state"] != "running":
            continue
        if c.get("started") is not None and now - float(c["started"]) < grace_s:
            continue
        client = held.get(c["name"])
        if client is not None:
            if not str(c.get("app") or "").endswith(SERVERS_OWN) or os.getpid() in _ancestors(client):
                continue
            try:                                          # a former server's: its client, and what started it
                os.killpg(os.getpgid(client), signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                pass
        try:
            said = kill_container(c["name"])
        except (ValueError, OSError, subprocess.TimeoutExpired):
            continue
        gone.append({**c, "said": said})
    return gone


def loop_disk(app_dir: Path, user: str, app: str) -> dict[str, Any]:
    """A loop's bytes: its inputs, record (`out/`), log and inbox (`runs/`), workbench, and cache."""
    parts = {"record": app_dir / "out", "log": app_dir / "runs", "workbench": app_dir / "workbench"}
    got = {k: dir_size(p) for k, p in parts.items()}
    got["inputs"] = max(0, dir_size(app_dir) - sum(got.values()))
    cache = cache_root() / _key(user, app)
    got["cache"] = dir_size(cache) if cache.is_dir() else 0
    got["total"] = sum(got.values())
    got["cache_key"] = cache.name
    return got


def caches(loops: set[tuple[str, str]], users: set[str]) -> list[dict[str, Any]]:
    """Every sandbox cache and whose it is: a web loop's, a loop gone (its user's, or a user gone),
    or not the web's (a `flux task run` of this machine's user)."""
    root = cache_root()
    keys = {_key(u, a): (u, a) for u, a in loops}
    out = []
    try:
        entries = sorted(p for p in root.iterdir() if p.is_dir())
    except OSError:
        return []
    for p in entries:
        if p.name in keys:
            kind, owner = "loop", keys[p.name]
        else:
            user = p.name.split(".", 1)[0] if "." in p.name else None
            if user in users:
                kind, owner = "gone", (user, p.name.split(".", 1)[1])
            else:
                kind, owner = "other", None
        try:
            mtime = max((c.stat().st_mtime for c in p.iterdir()), default=p.stat().st_mtime)
        except OSError:
            mtime = None
        out.append({"key": p.name, "kind": kind, "user": owner[0] if owner else None, "app": owner[1] if owner else None,
                    "size": dir_size(p), "touched": mtime})
    return out


def clean(key: str, what: str) -> int:
    """Free a cache's space; the bytes freed. `tools`: its XDG cache (tools rebuild what they
    need). `scratch`: the agents' working folders of past passes (a trace folder's dated
    subfolders), the journal and transcript kept. `all`: the whole cache."""
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", key) or key in (".", ".."):
        raise ValueError("not a cache's key")
    root = cache_root() / key
    if not root.is_dir():
        raise ValueError(f"no cache {key}")
    before = dir_size(root, fresh=True)
    if what == "all":
        shutil.rmtree(root, ignore_errors=True)
    elif what == "tools":
        for c in (root / "cache").glob("*"):
            if c.is_dir() and not c.is_symlink():
                shutil.rmtree(c, ignore_errors=True)
            else:
                c.unlink(missing_ok=True)
    elif what == "scratch":
        for run in (root / "tmp" / "flux-traces").glob("*"):
            for sub in run.iterdir() if run.is_dir() else []:
                if sub.is_dir() and not sub.is_symlink() and re.fullmatch(r"\d{8}T\d{6}.*", sub.name):
                    shutil.rmtree(sub, ignore_errors=True)
    else:
        raise ValueError("clean: tools, scratch or all")
    after = dir_size(root, fresh=True) if root.exists() else 0
    for k in [k for k in _SIZES if k.startswith(str(root))]:
        _SIZES.pop(k, None)
    return max(0, before - after)
