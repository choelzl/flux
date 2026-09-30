"""Runs started from the web (D683): `flux task run` detached in its own session, sandboxed, its
output to `<app>/runs/<n>.log`, its record in `<app>/out/`. What a run is doing is read from what
it writes -- the record's run pointer, `run.json`, `events.jsonl`, `turns.jsonl` -- so a run
outlives the server, and a restarted server finds it again."""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from .store import Store, User

__all__ = ["RunManager"]


class RunManager:
    def __init__(self, store: Store, *, sandbox: bool = True, max_running: int = 4) -> None:
        self.store, self.sandbox, self.max_running = store, sandbox, max_running
        self._procs: dict[int, subprocess.Popen] = {}

    # ---- start
    def start(self, user: User, app: str, app_dir: Path, document: str, doc_id: str,
              options: dict[str, Any]) -> int:
        if sum(1 for r in self.store.runs(user) if self.live(r)) >= self.max_running:
            raise ValueError(f"at most {self.max_running} runs at once per user")
        out = app_dir / "out"
        out.mkdir(exist_ok=True)
        logs = app_dir / "runs"
        logs.mkdir(exist_ok=True)
        db = out / f"{doc_id}.db"
        n = 1 + len(list(logs.glob("*.log")))
        log = logs / f"{n:04d}.log"
        flux = shutil.which("flux") or sys.argv[0]
        argv = [flux, "task", "run", str(app_dir / document), "--db", str(db), "--json", str(log.with_suffix(".json"))]
        passes = options.get("passes")
        if passes is not None:
            argv += ["--passes", str(int(passes))]
        if options.get("screen_only"):
            argv.append("--screen-only")
        env = {**os.environ, "FLUX_SANDBOX_APP": f"{user.name}-{app}", "PYTHONUNBUFFERED": "1"}
        env.pop("FLUX_SANDBOX_ALLOW", None)
        if options.get("allow"):
            env["FLUX_SANDBOX_ALLOW"] = ",".join(str(h).strip() for h in options["allow"] if str(h).strip())
        if self.sandbox:
            env["FLUX_SANDBOX"] = "1"                       # a shared server runs nothing on the host
        run_id = self.store.add_run(user, app, str(db), str(log), argv, options)
        fh = open(log, "ab")
        proc = subprocess.Popen(argv, cwd=str(app_dir), stdout=fh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                env=env, start_new_session=True)
        fh.close()
        self.store.set_run(run_id, pid=proc.pid)
        self._procs[run_id] = proc
        threading.Thread(target=self._wait, args=(run_id, proc), daemon=True).start()
        return run_id

    def _wait(self, run_id: int, proc: subprocess.Popen) -> None:
        rc = proc.wait()
        self.store.set_run(run_id, ended=time.time(), rc=rc)
        self._procs.pop(run_id, None)

    # ---- state
    def live(self, run: dict[str, Any]) -> bool:
        if run.get("ended"):
            return False
        pid = run.get("pid")
        if not pid:
            return False
        try:
            os.kill(int(pid), 0)
        except ProcessLookupError:
            self.store.set_run(run["id"], ended=time.time())      # gone while the server was away
            return False
        except PermissionError:
            return True
        return True

    def campaign(self, run: dict[str, Any]) -> tuple[str | None, str | None]:
        """(campaign id, its run directory) from the record's run pointer, once the run has one."""
        try:
            pointer = json.loads(Path(f"{run['db']}.runs.json").read_text())
        except (OSError, ValueError):
            return None, None
        if not pointer:
            return None, None
        cid, rdir = list(pointer.items())[-1]
        return cid, rdir

    def state(self, run: dict[str, Any]) -> dict[str, Any]:
        from flux_loop import ops

        cid, rdir = self.campaign(run)
        info: dict[str, Any] = {"id": run["id"], "app": run["app"], "user": run.get("user"), "started": run["started"],
                                "ended": run.get("ended"), "rc": run.get("rc"), "live": self.live(run),
                                "campaign": cid, "options": json.loads(run.get("options") or "{}")}
        if cid:
            st = ops.status(cid, run["db"])
            info.update(passes=st.get("passes"), at_rest=st.get("at_rest"), stop_requested=bool(st.get("stop")),
                        container=st.get("container"), last_pass_ended=st.get("last_pass_ended"))
        info["events"] = bool(rdir and os.path.exists(os.path.join(rdir, "events.jsonl")))
        return info

    def events_path(self, run: dict[str, Any]) -> str | None:
        _cid, rdir = self.campaign(run)
        return os.path.join(rdir, "events.jsonl") if rdir else None

    def turns_path(self, run: dict[str, Any]) -> str | None:
        _cid, rdir = self.campaign(run)
        return os.path.join(rdir, "turns.jsonl") if rdir else None

    # ---- stop
    def stop(self, run: dict[str, Any], now: bool = False, why: str = "stopped from the web") -> str:
        from flux_loop import ops

        cid, _rdir = self.campaign(run)
        if not self.live(run):
            return "not running"
        if cid:
            ops.request_stop(cid, why, db=run["db"])
            if now and ops.interrupt(cid, run["db"]):
                return "interrupted: the pass ends now"
            if not now:
                return "the run stops at the end of this pass"
        try:                                                  # before it registered, or no answer: the process group
            os.killpg(int(run["pid"]), signal.SIGINT)
            return "interrupted"
        except (ProcessLookupError, PermissionError, TypeError):
            return "not running"
