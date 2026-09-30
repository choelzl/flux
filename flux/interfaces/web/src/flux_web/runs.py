"""A loop's starts from the web (D683, D689). A loop -- an application -- is running or not; starting
it again resumes it from its record. Each start is `flux task run` detached in its own session,
sandboxed, appending to the loop's one log (`<app>/runs/loop.log`, a line marking each start), with
one answer (`runs/answer.json`) and one notes inbox (`runs/inbox.jsonl`). The server keeps its
starts to know the process and the audit trail; nothing shows them as numbers. What a loop is doing
is read from what it writes -- the record's run pointer, `run.json`, `events.jsonl`, `turns.jsonl` --
so a loop outlives the server, and a restarted server finds it again."""

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

__all__ = ["RunManager", "loop_files", "run_env"]

#: The server's own model keys: never in the run of a user who brought their own endpoint.
_SERVER_KEYS = ("FLUX_REMOTE_API_KEY", "FLUX_REMOTE_API_KEY_FILE", "OPENROUTER_API_KEY")


def run_env(store: Store, user: User) -> dict[str, str]:
    """The environment of a user's run or check (D684): the server's, with the user's model
    settings over it. A run never reads the server's flux.env itself (FLUX_CONFIG): the server
    loaded it once, and a user with their own endpoint gets none of the server's keys."""
    env = {**os.environ, "FLUX_CONFIG": os.devnull}
    mine = store.settings(user, reveal=True)
    if mine.get("FLUX_REMOTE_BASE_URL"):
        for k in _SERVER_KEYS:
            env.pop(k, None)
        env["FLUX_LLM_REMOTE"] = "1"
    env.update(mine)
    return env


def loop_files(app_dir: Path) -> dict[str, Path]:
    """The loop's one log, answer and inbox, whatever the number of starts."""
    d = app_dir / "runs"
    return {"log": d / "loop.log", "answer": d / "answer.json", "inbox": d / "inbox.jsonl"}


class RunManager:
    def __init__(self, store: Store, *, sandbox: bool = True, max_running: int = 4) -> None:
        self.store, self.sandbox, self.max_running = store, sandbox, max_running

    # ---- start: the loop resumes from its record
    def start(self, user: User, app: str, app_dir: Path, document: str, doc_id: str, options: dict[str, Any]) -> None:
        paused = self.store.server_get("paused")
        if paused:
            raise ValueError(f"starts are paused by an admin: {paused}")
        mine = self.store.runs(user)
        if any(r["app"] == app and self.live(r) for r in mine):
            raise ValueError(f"{app} is running")
        limit = self.limit(user)
        if sum(1 for r in mine if self.live(r)) >= limit:
            raise ValueError(f"at most {limit} loop(s) running at once for {user.name}")
        (app_dir / "out").mkdir(exist_ok=True)
        files = loop_files(app_dir)
        files["log"].parent.mkdir(exist_ok=True)
        files["inbox"].touch()
        db = app_dir / "out" / f"{doc_id}.db"
        flux = shutil.which("flux") or sys.argv[0]
        argv = [flux, "task", "run", str(app_dir / document), "--db", str(db), "--json", str(files["answer"])]
        passes = options.get("passes")
        if passes is not None:
            argv += ["--passes", str(int(passes))]
        if options.get("screen_only"):
            argv.append("--screen-only")
        env = {**run_env(self.store, user), "FLUX_SANDBOX_APP": f"{user.name}.{app}", "PYTHONUNBUFFERED": "1",
               "FLUX_FEEDBACK_INBOX": str(files["inbox"])}                  # D684: notes and answers from the page
        env.pop("FLUX_SANDBOX_ALLOW", None)
        if options.get("allow"):
            env["FLUX_SANDBOX_ALLOW"] = ",".join(str(h).strip() for h in options["allow"] if str(h).strip())
        if self.sandbox:
            env["FLUX_SANDBOX"] = "1"                       # a shared server runs nothing on the host
        said = [f"{passes} pass(es)" if passes else "until stopped"] + (["screen only"] if options.get("screen_only") else []) \
            + ([f"network {env['FLUX_SANDBOX_ALLOW']}"] if options.get("allow") else [])
        with open(files["log"], "a") as fh:
            fh.write(f"\n── started {time.strftime('%Y-%m-%d %H:%M:%S')} by {user.name} · {', '.join(said)} ──\n")
        run_id = self.store.add_run(user, app, str(db), str(files["log"]), argv, options)
        fh = open(files["log"], "ab")
        proc = subprocess.Popen(argv, cwd=str(app_dir), stdout=fh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                env=env, start_new_session=True)
        fh.close()
        self.store.set_run(run_id, pid=proc.pid)
        threading.Thread(target=self._wait, args=(run_id, proc), daemon=True).start()

    def limit(self, user: User) -> int:
        """The user's loops running at once: an admin's setting for them (D695), else the server's."""
        got = self.store.server_get(f"max_running:{user.name}")
        return int(got) if got is not None else self.max_running

    def _wait(self, run_id: int, proc: subprocess.Popen) -> None:
        rc = proc.wait()
        self.store.set_run(run_id, ended=time.time(), rc=rc)

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

    def latest(self, user: User, app: str) -> dict[str, Any] | None:
        runs = self.store.runs(user, app)
        return runs[0] if runs else None

    def campaign(self, run: dict[str, Any] | None) -> tuple[str | None, str | None]:
        """(campaign id, its run directory) from the record's run pointer, once a start made one."""
        if not run:
            return None, None
        try:
            pointer = json.loads(Path(f"{run['db']}.runs.json").read_text())
        except (OSError, ValueError):
            return None, None
        if not pointer:
            return None, None
        cid, rdir = list(pointer.items())[-1]
        return cid, rdir

    def state(self, user: User, app: str) -> dict[str, Any]:
        """The loop's state: running or not, since when, its last activity, and while it runs its
        pass, whether a stop is asked, its sandbox and an open question."""
        from flux_loop import ops

        run = self.latest(user, app)
        info: dict[str, Any] = {"app": app, "user": user.name, "running": False, "since": None, "last_active": None,
                                "failed": False, "stopped": False, "question": None, "events": False, "options": {}}
        if run is None:
            return info
        running = self.live(run)
        info.update(running=running, since=run["started"] if running else None,
                    last_active=(time.time() if running else (run.get("ended") or run["started"])),
                    failed=(not running and run.get("rc") not in (0, None, 130)), stopped=(not running and run.get("rc") == 130),
                    options=json.loads(run.get("options") or "{}"))
        cid, rdir = self.campaign(run)
        info["campaign"] = cid
        if cid and running:
            st = ops.status(cid, run["db"])
            if st.get("started") and abs(float(st["started"]) - float(run["started"])) < 300:   # this start's registration
                info.update(passes=st.get("passes"), at_rest=st.get("at_rest"), stop_requested=bool(st.get("stop")),
                            container=st.get("container"))
        info["events"] = bool(rdir and os.path.exists(os.path.join(rdir, "events.jsonl")))
        info["question"] = self.open_question(run, rdir) if running else None
        return info

    def open_question(self, run: dict[str, Any], rdir: str | None) -> dict[str, Any] | None:
        """The agent's question still waiting for the operator (D688): the journal's last
        `question` mark of this start, when no note came after it and its time is not up."""
        if not rdir:
            return None
        path = os.path.join(rdir, "events.jsonl")
        try:
            with open(path, "rb") as fh:
                fh.seek(max(0, os.path.getsize(path) - 256 * 1024))
                tail = fh.read().decode("utf-8", "replace").splitlines()
        except OSError:
            return None
        asked = None
        for line in reversed(tail):
            if '"question"' not in line:
                continue
            try:
                e = json.loads(line)
                if e.get("ev") == "mark" and e.get("name") == "question" and e.get("t", 0) >= run["started"] - 1:
                    asked = json.loads(e["why"])
                    break
            except ValueError:
                continue
        if not asked:
            return None
        if time.time() > float(asked.get("asked", 0)) + float(asked.get("wait_s", 0)):
            return None
        if any(float(n.get("t", 0)) >= float(asked.get("asked", 0)) for n in self.notes(Path(run["log"]).parent)):
            return None
        return asked

    def events_path(self, run: dict[str, Any] | None) -> str | None:
        _cid, rdir = self.campaign(run)
        return os.path.join(rdir, "events.jsonl") if rdir else None

    def turns_path(self, run: dict[str, Any] | None) -> str | None:
        _cid, rdir = self.campaign(run)
        return os.path.join(rdir, "turns.jsonl") if rdir else None

    # ---- notes, into the loop's inbox (D684)
    def note(self, runs_dir: Path, user: User, text: str) -> None:
        with open(runs_dir / "inbox.jsonl", "a") as fh:
            fh.write(json.dumps({"text": text, "by": user.name, "t": time.time()}) + "\n")

    def notes(self, runs_dir: Path) -> list[dict[str, Any]]:
        try:
            lines = (runs_dir / "inbox.jsonl").read_text().splitlines()
        except OSError:
            return []
        out = []
        for ln in lines:
            try:
                out.append(json.loads(ln))
            except ValueError:
                pass
        return out

    # ---- stop
    def stop(self, run: dict[str, Any] | None, now: bool = False, why: str = "stopped from the web") -> str:
        from flux_loop import ops

        if not run or not self.live(run):
            return "not running"
        cid, _rdir = self.campaign(run)
        if cid:
            ops.request_stop(cid, why, db=run["db"])
            if now and ops.interrupt(cid, run["db"]):
                return "stopping now: the pass ends, the record keeps what was judged"
            if not now:
                return "it stops at the end of this pass"
        try:                                                  # before it registered, or no answer: the process group
            os.killpg(int(run["pid"]), signal.SIGINT)
            return "stopping now"
        except (ProcessLookupError, PermissionError, TypeError):
            return "not running"
