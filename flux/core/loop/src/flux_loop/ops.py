"""Operations (D513): a running campaign the command line can see, stop at a pass boundary,
and re-attach to.

Every `run_loop` registers itself at `<trace root>/<campaign>/run.json` (pid, argv, start,
log, passes so far), which `flux status` reads. `flux stop` writes a stop request beside it
that the pass loop honours when the current pass ends (or sends SIGINT with `--now`);
`flux run -- <command>` starts a command detached with a log, and `flux attach` tails it.

The registration is the process's word, not the record's: a stale `run.json` with a dead
pid is reported as stale and never trusted.
"""

from __future__ import annotations

import json
import os
import signal
import sys
import time
from typing import Any

from .provenance import trace_root

__all__ = ["clear_stop", "register", "request_own_stop", "request_stop", "run_dir", "status", "stop_requested"]

_CURRENT: dict[str, Any] = {}          # this process's registration, for the pass loop to ask


def _pointer(db: str) -> str:
    return f"{db}.runs.json"


def run_dir(campaign_id: str, db: str | None = None) -> str:
    """Where the campaign's run registers. With the record (`db`): the directory the running
    process wrote beside it, since the trace root depends on the run's environment (D597)."""
    if db:
        where = (_read(_pointer(db)) or {}).get(campaign_id)
        if where:
            return where
    return os.path.join(trace_root(), (campaign_id or "")[:12] or "no-record")


def register(campaign_id: str, workdir: str, *, argv: list[str] | None = None, db: str | None = None) -> str:
    """This process as the campaign's runner. `FLUX_RUN_LOG` (set by `flux run`) is the log
    the registration names for `flux attach`. With the record (`db`), a pointer beside it names
    the run directory. Returns the run directory."""
    d = run_dir(campaign_id)
    os.makedirs(d, exist_ok=True)
    mine = _read(os.path.join(d, "run.json")) or {}
    if mine.get("pid") != os.getpid() or mine.get("container") != (os.environ.get("FLUX_SANDBOX_NAME") or None):
        mine = {}                          # every pass registers; the same process keeps its count and start
        # (in a sandbox every run's flux has the same pid: the container tells two runs apart, D688)
        # D695: a stop asked of an earlier run is not this one's -- a run stopped at once never
        # reached the pass boundary that clears it, and the next start would obey it
        try:
            os.remove(os.path.join(d, "stop"))
        except OSError:
            pass
    doc = {"pid": os.getpid(), "argv": list(argv if argv is not None else sys.argv), "cwd": os.getcwd(),
           "started": mine.get("started") or time.time(), "workdir": workdir,
           "log": os.environ.get("FLUX_RUN_LOG") or None,
           "container": os.environ.get("FLUX_SANDBOX_NAME") or None,   # D680: its pid is the container's
           "container_cli": json.loads(os.environ.get("FLUX_SANDBOX_CLI") or "null"),   # D682: how to reach it
           "passes": int(mine.get("passes") or 0), "last_pass_ended": mine.get("last_pass_ended"),
           "campaign": campaign_id}
    _write(os.path.join(d, "run.json"), doc)
    if db and db != ":memory:":
        try:
            _write(_pointer(db), {**(_read(_pointer(db)) or {}), campaign_id: d})
        except OSError:
            pass                               # a read-only record's folder: the trace root still works
    _CURRENT.clear()
    _CURRENT.update({"campaign": campaign_id, "dir": d})
    from flux_llm import transcript

    transcript.set_path(os.path.join(d, "turns.jsonl"))       # `flux log` reads it
    return d


def pass_ended(at_rest: bool = False) -> None:
    """One more pass done, on this process's registration."""
    d = _CURRENT.get("dir")
    if not d:
        return
    p = os.path.join(d, "run.json")
    doc = _read(p) or {}
    if doc.get("pid") != os.getpid():
        return
    doc["passes"] = int(doc.get("passes") or 0) + 1
    doc["last_pass_ended"] = time.time()
    doc["at_rest"] = bool(at_rest)
    _write(p, doc)


def stop_requested(campaign_id: str | None = None, db: str | None = None) -> str | None:
    """The reason a stop was asked for (the text `flux stop` wrote), or None. With no
    campaign: this process's own registration."""
    d = run_dir(campaign_id, db) if campaign_id else _CURRENT.get("dir")
    if not d:
        return None
    p = os.path.join(d, "stop")
    if not os.path.exists(p):
        return None
    try:
        with open(p) as f:
            return f.read().strip() or "stop requested"
    except OSError:
        return "stop requested"


def request_stop(campaign_id: str, why: str = "flux stop", db: str | None = None) -> str:
    d = run_dir(campaign_id, db)
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, "stop")
    with open(p, "w") as f:
        f.write(f"{why} ({time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())})\n")
    return p


def request_own_stop(why: str = "q in the TUI") -> bool:
    """Ask this process's own run to stop at the pass boundary; False when it registered none."""
    d = _CURRENT.get("dir")
    if not d:
        return False
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "stop"), "w") as f:
        f.write(f"{why} ({time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())})\n")
    return True


def clear_stop(campaign_id: str | None = None, db: str | None = None) -> None:
    d = run_dir(campaign_id, db) if campaign_id else _CURRENT.get("dir")
    if d:
        try:
            os.remove(os.path.join(d, "stop"))
        except OSError:
            pass


def alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def status(campaign_id: str, db: str | None = None) -> dict[str, Any]:
    """What the registration says, checked against the process table: `running`,
    `stale` (a registration whose pid is gone), or `none`."""
    d = run_dir(campaign_id, db)
    doc = _read(os.path.join(d, "run.json"))
    out: dict[str, Any] = {"campaign": campaign_id, "dir": d, "state": "none", "stop": stop_requested(campaign_id, db)}
    if not doc:
        return out
    out.update(doc)
    out["state"] = "running" if _running(doc) else "stale"
    return out


def _outside(doc: dict[str, Any]) -> bool:
    """The run is in a sandbox this process is not in (D680): its pid means nothing here."""
    return bool(doc.get("container")) and os.environ.get("FLUX_SANDBOX_NAME") != doc.get("container")


def _cli(doc: dict[str, Any]) -> list[str]:
    """The engine command the run was started with (Podman with its storage, or Docker)."""
    cli = doc.get("container_cli")
    return [str(c) for c in cli] if isinstance(cli, list) and cli else ["docker"]


def _running(doc: dict[str, Any]) -> bool:
    if not _outside(doc):
        return alive(doc.get("pid"))
    import subprocess

    try:
        r = subprocess.run([*_cli(doc), "inspect", "-f", "{{.State.Running}}", str(doc["container"])],
                           capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.stdout.strip() == "true"


def interrupt(campaign_id: str, db: str | None = None) -> bool:
    """SIGINT to the registered runner, when it is alive; the demos take that as
    `KeyboardInterrupt` and end the pass with the record holding what was judged."""
    st = status(campaign_id, db)
    if st["state"] != "running":
        return False
    if _outside(st):
        import subprocess

        # to the container's init, which hands it to flux
        return subprocess.run([*_cli(st), "kill", "--signal", "INT", str(st["container"])],
                              capture_output=True).returncode == 0
    os.kill(int(st["pid"]), signal.SIGINT)
    return True


def _read(p: str) -> dict[str, Any] | None:
    try:
        with open(p) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _write(p: str, doc: dict[str, Any]) -> None:
    # a per-process and per-thread temporary (D747: passes at once), so concurrent registrations
    # do not race; the replace is atomic
    import threading

    tmp = f"{p}.{os.getpid()}.{threading.get_ident()}.tmp"
    with open(tmp, "w") as f:
        json.dump(doc, f, indent=1)
    os.replace(tmp, p)
