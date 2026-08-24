"""OPERATIONS (docs/decisions.md D513): a running campaign as something the command line can
see, stop at a pass boundary, and re-attach to -- instead of a screen session and a recipe
in a memory file.

Every `run_loop` REGISTERS itself under the campaign's trace directory
(`<trace root>/<campaign>/run.json`: pid, argv, started, the log it writes to, the passes
so far), and `flux status` reads that; `flux stop` writes a STOP request beside it that the
pass loop (the TUI's loop toggle, a headless `--passes 0` run) honours when the current pass
ends -- or sends SIGINT with `--now`; `flux run -- <command>` starts a command detached with
its output in a log the registration names, and `flux attach` tails that log.

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

__all__ = ["clear_stop", "register", "request_stop", "run_dir", "status", "stop_requested"]

_CURRENT: dict[str, Any] = {}          # this process's registration, for the pass loop to ask


def run_dir(campaign_id: str) -> str:
    return os.path.join(trace_root(), (campaign_id or "")[:12] or "no-record")


def register(campaign_id: str, workdir: str, *, argv: list[str] | None = None) -> str:
    """This process as the campaign's runner. `FLUX_RUN_LOG` (set by `flux run`) is the log
    the registration names for `flux attach`. Returns the run directory."""
    d = run_dir(campaign_id)
    os.makedirs(d, exist_ok=True)
    doc = {"pid": os.getpid(), "argv": list(argv if argv is not None else sys.argv), "cwd": os.getcwd(),
           "started": time.time(), "workdir": workdir, "log": os.environ.get("FLUX_RUN_LOG") or None,
           "passes": 0, "last_pass_ended": None, "campaign": campaign_id}
    _write(os.path.join(d, "run.json"), doc)
    _CURRENT.clear()
    _CURRENT.update({"campaign": campaign_id, "dir": d})
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


def stop_requested(campaign_id: str | None = None) -> str | None:
    """The reason a stop was asked for (the text `flux stop` wrote), or None. With no
    campaign: this process's own registration."""
    d = run_dir(campaign_id) if campaign_id else _CURRENT.get("dir")
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


def request_stop(campaign_id: str, why: str = "flux stop") -> str:
    d = run_dir(campaign_id)
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, "stop")
    with open(p, "w") as f:
        f.write(f"{why} ({time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())})\n")
    return p


def clear_stop(campaign_id: str | None = None) -> None:
    d = run_dir(campaign_id) if campaign_id else _CURRENT.get("dir")
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


def status(campaign_id: str) -> dict[str, Any]:
    """What the registration says, checked against the process table: `running`,
    `stale` (a registration whose pid is gone), or `none`."""
    d = run_dir(campaign_id)
    doc = _read(os.path.join(d, "run.json"))
    out: dict[str, Any] = {"campaign": campaign_id, "dir": d, "state": "none", "stop": stop_requested(campaign_id)}
    if not doc:
        return out
    out.update(doc)
    out["state"] = "running" if alive(doc.get("pid")) else "stale"
    return out


def interrupt(campaign_id: str) -> bool:
    """SIGINT to the registered runner, when it is alive; the demos take that as
    `KeyboardInterrupt` and end the pass with the record holding what was judged."""
    st = status(campaign_id)
    if st["state"] != "running":
        return False
    os.kill(int(st["pid"]), signal.SIGINT)
    return True


def _read(p: str) -> dict[str, Any] | None:
    try:
        with open(p) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _write(p: str, doc: dict[str, Any]) -> None:
    # the temporary is this process's own (D531: two test workers registering the same
    # campaign id at once raced on one `run.json.tmp`); the replace stays atomic
    tmp = f"{p}.{os.getpid()}.tmp"
    with open(tmp, "w") as f:
        json.dump(doc, f, indent=1)
    os.replace(tmp, p)
