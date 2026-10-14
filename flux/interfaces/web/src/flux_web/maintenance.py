"""Maintenance (D885): the clean-up an admin schedules, as Gitea's cron tasks. Each task has a
schedule (every so many hours), an on/off switch, its settings and its last results; `tick` runs the
ones due, one at a time on a thread of its own, and Run now runs one at once. Loop tasks act
over every loop, or one when asked (its owner may, from
the loop's Settings). A running loop is never touched: its files are being written. Every run is in
the audit trail."""

from __future__ import annotations

import contextlib
import fnmatch
import gzip
import json
import os
import shutil
import sqlite3
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

__all__ = ["TASKS", "Maintenance"]

DAY = 86400.0


@dataclass(frozen=True)
class Task:
    key: str
    title: str
    what: str                                   # one line for the admin page
    every_h: float                              # the default schedule
    on: bool                                    # on by default
    params: dict[str, Any] = field(default_factory=dict)   # name -> default
    per_loop: bool = False                      # acts on loops: over all, or one


TASKS: dict[str, Task] = {t.key: t for t in (
    # off until an admin turns it on: the one task that acts beyond the server's own data -- the
    # machine's FLUX_TMPDIR, which other servers, test runs and tools share
    Task("scratch", "Clean scratch", "Delete what Flux's temporary folder (FLUX_TMPDIR) holds that nothing has "
         "touched for a while and no process uses. Shared by everything on this machine: off until turned on.",
         24, False, {"days": 3, "keep": "claude-*"}),
    Task("containers", "Reap containers", "Remove sandbox containers no process runs any more (D768).", 1 / 60, True),
    Task("caches", "Clean idle caches", "A loop's sandbox cache (tools' caches and past passes' agent folders) "
         "when the loop has not run for a while.", 168, True, {"days": 14}, per_loop=True),
    Task("disk", "Disk alert", "Tell the admins when a disk the server uses has little space left.", 1, True,
         {"min_free_pct": 10}),
    Task("compact", "Compact databases", "Checkpoint, optimise and vacuum the server's database and every "
         "loop's record; a record's designs kept once (a running loop's too, a few rows at a time).", 168, True,
         per_loop=True),
    Task("logs", "Condense run logs", "A loop's log over a size keeps its recent part; the older part is "
         "gzipped beside it.", 24, True, {"max_mb": 50, "keep_mb": 5}, per_loop=True),
    Task("tables", "Prune server tables", "Expired sessions and invitations, old login failures and "
         "notifications, and audit lines past their age.", 24, True,
         {"failures_days": 30, "notices_days": 30, "audit_days": 365}),
    # D940: what else grows with nothing tidying it
    Task("orphans", "Remove orphaned caches", "A sandbox cache whose loop was deleted (Admin > Resources lists it "
         "as gone).", 168, True),
    Task("runs", "Prune run history", "Each loop keeps its latest starts in the server's run list; older rows "
         "go (a running start never).", 24, True, {"keep": 200}),
    Task("journals", "Condense task journals", "A loop's task journal (the Live tree's history) over a size keeps "
         "its recent starts whole; older ones are gzipped beside it.", 24, True, {"max_mb": 20, "keep_mb": 4},
         per_loop=True),
    # D1069: every model and agent turn's prompt, reply and tool calls -- gigabytes after days of running
    Task("transcripts", "Thin old transcripts", "A loop's transcript (every model and agent turn: its prompt, reply "
         "and tool calls) keeps its last days whole; an older turn keeps only what usage counts -- when, who, "
         "tokens, cost, whether it failed and why. Insights reads the usage log, summed as turns happen. Running "
         "loops too: a turn is appended on its own.", 24, True, {"days": 14}, per_loop=True),
    # D1071: a pass's agent folders (its prompts, drafts, scratch) -- a running loop's too: ~100 MB a pass
    Task("passes", "Prune old agent folders", "A loop's working folders of passes started more than the days ago "
         "(each agent's prompt, drafts and scratch) go -- its newest folder always stays, and the journal, the "
         "transcript, the record and the workbench. Running loops too: an old pass's folder is not written to.",
         24, True, {"days": 3}, per_loop=True),
    Task("refused", "Prune refused-host log", "The sandbox proxies' log of refused connections keeps its last "
         "days (Insights > Network reads it).", 24, True, {"days": 30}),
    Task("stale", "Prune stale records", "Record rows measured under other inputs (D853): the loop no longer "
         "counts them. Reports them; deletes them when told to.", 168, False, {"delete": False}, per_loop=True),
    Task("empty", "Prune empty starts", "Starts that did nothing -- stopped before any search, design, check or "
         "measurement began (D987) -- go: their row, and for a loop's latest start its log and journal. "
         "Starts already checked are left.", 24, False, per_loop=True),
    Task("baseline", "Rerun baseline", "The loop's next start measures pass 0 again, once (D1005): otherwise a "
         "baseline on record stands while the document's baseline: section is unchanged, whatever else changed.",
         168, False, per_loop=True),
    Task("reevaluate", "Invalidate + re-evaluate all", "After the checks or measurement scripts change "
         "(D988): one run checks and measures every design on record again; the earlier results stay as "
         "history, no longer counted. Started on one loop only.", 168, False, per_loop=True),
    Task("integrity", "Check database integrity", "Read-only SQLite integrity checks on the server database "
         "and stopped loops' out/*.db records. Reports problems without repairing or deleting data.",
         168, False, per_loop=True),
    Task("documents", "Validate loop documents", "Load each stopped loop's selected problem document, including "
         "referenced parts. Reports errors without changing files or running tools or agents.",
         24, False, per_loop=True),
    Task("backups", "Back up server database", "Save a consistent copy of flux-web.db and secret.key in the "
         "server data folder's backups/. Keep the latest backups, deleting older ones. Contains accounts, "
         "settings and run metadata; loop files, outputs and sandbox caches are not included.",
         24, False, {"keep_backups": 7}),
)}


class Maintenance:
    """The tasks' settings, results and runs, for one server. `live(user, app)` says whether a
    loop runs now; `loops()` lists (user, app, folder)."""

    HISTORY = 20

    def __init__(self, store: Any, live: Callable[[str, str], bool],
                 loops: Callable[[], list[tuple[str, str, Path]]]) -> None:
        self.store, self.live, self.loops = store, live, loops
        self.runs: Any = None                  # D1000: the run manager, for the starts' history
        self._lock = threading.Lock()
        self.running: str | None = None

    # ---- settings and results
    def config(self, key: str) -> dict[str, Any]:
        t = TASKS[key]
        got = dict((self.store.server_get("maintenance") or {}).get(key) or {})
        return {"on": bool(got.get("on", t.on)), "every_h": float(got.get("every_h") or t.every_h),
                "params": {**t.params, **{k: v for k, v in (got.get("params") or {}).items() if k in t.params}}}

    def set_config(self, key: str, on: bool | None, every_h: float | None, params: dict[str, Any] | None) -> dict[str, Any]:
        t = TASKS[key]
        if every_h is not None and not 1 / 60 - 1e-9 <= every_h <= 24 * 90:
            raise ValueError("every: from a minute to 90 days")
        bad = sorted(set(params or {}) - set(t.params))
        if bad:
            raise ValueError(f"{t.title} has no setting {', '.join(bad)}")
        for k, v in (params or {}).items():
            want = t.params[k]
            if isinstance(want, bool):
                if not isinstance(v, bool):
                    raise ValueError(f"{k}: yes or no")
            elif isinstance(want, (int, float)):
                if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0:
                    raise ValueError(f"{k}: a number, 0 or more")
                if k == "keep_backups" and (v < 1 or v != int(v)):
                    raise ValueError("keep_backups: a whole number, 1 or more")
            elif not isinstance(v, str):
                raise ValueError(f"{k}: text")
        def change(all_: Any) -> Any:
            all_ = dict(all_ or {})
            cur = dict(all_.get(key) or {})
            if on is not None:
                cur["on"] = on
            if every_h is not None:
                cur["every_h"] = every_h
            if params:
                cur["params"] = {**(cur.get("params") or {}), **params}
            all_[key] = cur
            return all_

        self.store.server_update("maintenance", change)             # D905: two tasks set at once both kept
        return self.config(key)

    def history(self, key: str) -> list[dict[str, Any]]:
        return list(self.store.server_get(f"maintenance:runs:{key}") or [])

    def view(self) -> list[dict[str, Any]]:
        out = []
        for key, t in TASKS.items():
            cfg, runs = self.config(key), self.history(key)
            last = runs[0] if runs else None
            checked = self.checked(key)
            nxt = (checked + cfg["every_h"] * 3600 if checked else time.time()) if cfg["on"] else None
            out.append({"key": key, "title": t.title, "what": t.what, "per_loop": t.per_loop, **cfg,
                        "last": last, "runs": runs, "running": self.running == key, "next": nxt, "checked": checked})
        return out

    # ---- running
    def due(self, now: float | None = None) -> list[str]:
        now = time.time() if now is None else now
        out = []
        for key in TASKS:
            cfg, checked = self.config(key), self.checked(key)
            if cfg["on"] and (not checked or now - checked >= cfg["every_h"] * 3600 - 1):
                out.append(key)
        return out

    def checked(self, key: str) -> float | None:
        """When the task last ran over everything (the schedule's clock), whether or not it did anything."""
        got = self.store.server_get(f"maintenance:checked:{key}")
        return float(got) if got else None

    def tick(self) -> None:
        """From the server's minute (D699): the tasks due, one after another, on a thread."""
        if self.running is not None:
            return
        keys = self.due()
        if keys:
            threading.Thread(target=lambda: [self.run(k, by="schedule") for k in keys], daemon=True,
                             name="flux-maintenance").start()

    def run(self, key: str, *, by: str = "schedule", loop: tuple[str, str] | None = None,
            start: Callable[[], Any] | None = None) -> dict[str, Any]:
        """One task now; its result (also kept, and in the audit trail). `loop`: (user, app) only;
        `start`: how a task that is a run (re-evaluate) starts it."""
        t = TASKS[key]
        self._start = start
        if loop is not None and not t.per_loop:
            raise ValueError(f"{t.title} is the server's, not a loop's")
        if not self._lock.acquire(blocking=False):
            raise RuntimeError(f"{TASKS[self.running].title if self.running else 'a task'} is running")
        self.running = key
        t0 = time.time()
        try:
            try:
                said, did = getattr(self, f"_{key}")(self.config(key)["params"], loop)
                ok = True
            except Exception as exc:  # noqa: BLE001 -- a task that fails says so, the next still runs
                said, did, ok = f"failed: {exc!s:.300}", True, False
            rec = {"t": t0, "s": round(time.time() - t0, 1), "ok": ok, "said": said, "by": by,
                   **({"loop": f"{loop[0]}/{loop[1]}"} if loop else {})}
            if loop is None:
                self.store.server_set(f"maintenance:checked:{key}", t0)
            # a scheduled run that found nothing to do is not news: the clock moves, the history and
            # the audit trail are kept for what happened (a minute's reaper would fill them)
            if did or by != "schedule":
                self.store.server_set(f"maintenance:runs:{key}", [rec, *self.history(key)][: self.HISTORY])
                self.store.audit(None if by == "schedule" else by, "maintenance",
                                 f"{t.title}{' on ' + rec['loop'] if loop else ''}: {said}"[:500])
            return rec
        finally:
            self.running = None
            self._lock.release()

    def _loops(self, loop: tuple[str, str] | None) -> tuple[list[tuple[str, str, Path]], list[str]]:
        """The loops a task acts on, and those it leaves because they run."""
        got = [x for x in self.loops() if loop is None or (x[0], x[1]) == loop]
        if loop is not None and not got:
            raise ValueError(f"no loop {loop[0]}/{loop[1]}")
        idle = [x for x in got if not self.live(x[0], x[1])]
        return idle, [f"{u}/{a}" for u, a, _d in got if (u, a, _d) not in idle]

    @staticmethod
    def _skipped(busy: list[str]) -> str:
        return f"; {len(busy)} running, left alone ({', '.join(busy[:4])})" if busy else ""

    # ---- the tasks: each returns (what it did in a line, whether it changed anything)
    def _integrity(self, _p: dict[str, Any], loop: tuple[str, str] | None) -> tuple[str, bool]:
        idle, busy = self._loops(loop)
        dbs = [] if loop else [("server", Path(self.store.path))]
        for u, a, d in idle:
            dbs.extend((f"{u}/{a}/{db.relative_to(d)}", db) for db in sorted((d / "out").glob("*.db"))
                       if not db.is_symlink() and db.resolve().is_relative_to(d.resolve()))
        errors = []
        for label, db in dbs:
            try:
                with contextlib.closing(sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True, timeout=30)) as con:
                    rows = [r[0] for r in con.execute("PRAGMA integrity_check")]
                if rows != ["ok"]:
                    errors.append(f"{label}: {'; '.join(rows)[:200]}")
            except sqlite3.Error as exc:
                errors.append(f"{label}: {exc}")
        if errors:
            raise ValueError(f"{len(errors)} of {len(dbs)} database(s) failed: " + "; ".join(errors[:6]) + self._skipped(busy))
        return f"{len(dbs)} database(s) checked, all OK" + self._skipped(busy), False

    def _documents(self, _p: dict[str, Any], loop: tuple[str, str] | None) -> tuple[str, bool]:
        from flux_loop.document import confined, load_task
        from flux_loop.document.library import inside

        idle, busy = self._loops(loop)
        errors = []
        for u, a, d in idle:
            try:
                with confined(d):
                    meta_path = inside(d / ".flux-app.json", "loop metadata")
                    meta = json.loads(meta_path.read_text()) if meta_path.is_file() else {}
                    doc = inside(d / str(meta.get("document") or "problem.yaml"), "problem document")
                    load_task(doc)
            except Exception as exc:  # noqa: BLE001 -- report one document and still check the others
                errors.append(f"{u}/{a}: {' '.join(str(exc).split())[:200]}")
        if errors:
            raise ValueError(f"{len(errors)} of {len(idle)} loop document(s) failed: " + "; ".join(errors[:6]) + self._skipped(busy))
        return f"{len(idle)} loop document(s) checked, all OK" + self._skipped(busy), False

    def _backups(self, p: dict[str, Any], _loop: Any) -> tuple[str, bool]:
        root = self.store.data / "backups"
        if root.is_symlink():
            raise ValueError("backups/ must be a folder in the server data directory, not a symlink")
        root.mkdir(mode=0o700, exist_ok=True)
        os.chmod(root, 0o700)
        dest = root / f"server-{time.time_ns()}"
        tmp = Path(tempfile.mkdtemp(prefix=".server-", dir=root))
        try:
            db = tmp / "flux-web.db"
            db.touch(mode=0o600)
            with contextlib.closing(sqlite3.connect(self.store.path, timeout=30)) as src, \
                    contextlib.closing(sqlite3.connect(db)) as dst:
                src.backup(dst)
            key = self.store.data / "secret.key"
            if key.is_file():
                target = tmp / "secret.key"
                target.touch(mode=0o600)
                target.write_bytes(key.read_bytes())
            os.replace(tmp, dest)
        finally:
            if tmp.exists():
                shutil.rmtree(tmp)
        backups = sorted((d for d in root.iterdir() if d.name.startswith("server-")
                          and d.name[7:].isdigit() and d.is_dir() and not d.is_symlink()),
                         key=lambda d: int(d.name[7:]), reverse=True)
        old = backups[max(1, int(p["keep_backups"])):]
        for d in old:
            shutil.rmtree(d)
        return f"saved backups/{dest.name}/ (database and encryption key if present); {len(old)} older backup(s) removed", True

    def _scratch(self, p: dict[str, Any], _loop: Any) -> str:
        root, why = scratch_root(self.store.data)
        if root is None:
            return why, False
        keep = [g.strip() for g in str(p.get("keep") or "").split(",") if g.strip()]
        cutoff = time.time() - float(p["days"]) * DAY
        used = _paths_in_use()
        n, freed, kept = 0, 0, 0
        for e in sorted(root.iterdir()):
            if any(fnmatch.fnmatch(e.name, g) for g in keep) or e.is_symlink():
                continue
            ep = str(e.resolve())
            if any(u == ep or u.startswith(ep + os.sep) for u in used) or _touched_after(e, cutoff):
                kept += 1
                continue
            size = _size(e)
            shutil.rmtree(e, ignore_errors=True) if e.is_dir() else e.unlink(missing_ok=True)
            if not e.exists():
                n, freed = n + 1, freed + size
        return f"{n} entr{'y' if n == 1 else 'ies'} removed, {_mb(freed)} freed; {kept} recent or in use kept", n > 0

    def _containers(self, _p: dict[str, Any], _loop: Any) -> str:
        from .admin import reap

        gone = reap()
        return f"{len(gone)} container(s) removed" + (f": {', '.join(c['name'] for c in gone[:5])}" if gone else ""), bool(gone)

    def _caches(self, p: dict[str, Any], loop: tuple[str, str] | None) -> str:
        from .admin import _key, cache_root, clean

        idle, busy = self._loops(loop)
        cutoff = time.time() - float(p["days"]) * DAY
        n, freed = 0, 0
        for u, a, d in idle:
            cache = cache_root() / _key(u, a)
            if not cache.is_dir() or (loop is None and _last_run(d) > cutoff):
                continue
            for what in ("tools", "scratch"):
                freed += clean(cache.name, what)
            n += 1
        return f"{n} loop cache(s) cleaned, {_mb(freed)} freed" + self._skipped(busy), freed > 0

    def _disk(self, p: dict[str, Any], _loop: Any) -> str:
        from .admin import cache_root, machine

        paths = {"server data": str(self.store.data), "sandbox caches": str(cache_root().parent)}
        if os.environ.get("FLUX_TMPDIR"):
            paths["scratch"] = os.environ["FLUX_TMPDIR"]
        low = []
        for d in machine({k: v for k, v in paths.items() if os.path.exists(v)})["disks"]:
            if d.get("same_as"):
                continue
            pct = 100.0 * d["free"] / d["total"] if d["total"] else 100.0
            if pct < float(p["min_free_pct"]):
                low.append(f"{d['label']} ({d['path']}): {pct:.1f}% free, {_mb(d['free'])}")
        was = bool(self.store.server_get("maintenance:disk-low"))
        if low and not was:                          # said once, when it gets low; again after it recovered
            for u in self.store.users():
                if u.role == "admin" and not u.disabled:
                    self.store.notify(u.name, "Disk space is low: " + "; ".join(low), "#/admin", "bad")
        self.store.server_set("maintenance:disk-low", bool(low) or None)
        return (("low: " + "; ".join(low) + ("" if was else " -- admins told")) if low else "every disk has room"), bool(low) != was

    def _compact(self, _p: dict[str, Any], loop: tuple[str, str] | None) -> str:
        from flux_store import CampaignStore

        idle, busy = self._loops(loop)
        before = after = moved = 0
        # D1072: every record's designs kept once -- a running loop's too, a short transaction at a time
        for _u, _a, d in idle + [x for x in self.loops() if f"{x[0]}/{x[1]}" in busy]:
            for db in sorted((d / "out").glob("*.db")) if (d / "out").is_dir() else []:
                if db.is_symlink() or not db.resolve().is_relative_to(d.resolve()):
                    continue
                try:
                    store = CampaignStore(str(db))
                except sqlite3.Error:
                    continue
                try:
                    moved += store.share_artifacts()[0]
                    store._conn.execute("DELETE FROM artifacts WHERE key NOT IN "
                                        "(SELECT artifact_key FROM trials WHERE artifact_key IS NOT NULL)")
                    store._conn.commit()
                finally:
                    store.close()
        dbs = [] if loop else [Path(self.store.path)]
        for _u, _a, d in idle:
            dbs += sorted((d / "out").glob("*.db")) if (d / "out").is_dir() else []
        for db in dbs:
            before += _size_db(db)
            con = sqlite3.connect(db, timeout=60)
            try:
                con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                con.execute("PRAGMA optimize")
                con.execute("VACUUM")
                con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            finally:
                con.close()
            after += _size_db(db)
        kept = f"; {moved} trial(s)' designs now kept once" if moved else ""
        return (f"{len(dbs)} database(s) compacted, {_mb(before)} to {_mb(after)}" + kept + self._skipped(busy),
                bool(dbs) or bool(moved))

    def _logs(self, p: dict[str, Any], loop: tuple[str, str] | None) -> str:
        idle, busy = self._loops(loop)
        limit, keep = float(p["max_mb"]) * 1e6, float(p["keep_mb"]) * 1e6
        n, freed = 0, 0
        for _u, _a, d in idle:
            log = d / "runs" / "loop.log"
            if not log.is_file() or log.stat().st_size <= limit:
                continue
            freed += _condense(log, int(keep))
            n += 1
        return f"{n} log(s) condensed, {_mb(freed)} freed" + self._skipped(busy), n > 0

    def _tables(self, p: dict[str, Any], _loop: Any) -> str:
        now = time.time()
        with self.store._db() as db:
            s = db.execute("DELETE FROM sessions WHERE expires < ?", (now,)).rowcount
            i = db.execute("DELETE FROM invites WHERE expires < ?", (now,)).rowcount
            f = db.execute("DELETE FROM failures WHERE t < ?", (now - float(p["failures_days"]) * DAY,)).rowcount
            a = db.execute("DELETE FROM audit WHERE t < ?", (now - float(p["audit_days"]) * DAY,)).rowcount \
                if float(p["audit_days"]) > 0 else 0
        notes = 0
        for u in self.store.users():
            gone = [0]

            def prune(got: Any, gone: list[int] = gone) -> Any:
                kept = [x for x in got or [] if now - float(x.get("t") or now) < float(p["notices_days"]) * DAY]
                gone[0] = len(got or []) - len(kept)
                return kept or None

            self.store.server_update(f"notices:{u.name}", prune)     # D905: a notice added meanwhile is kept
            notes += gone[0]
        return (f"{s} session(s), {i} invitation(s), {f} login failure(s), {notes} notification(s), {a} audit line(s) removed",
                bool(s or i or f or notes or a))

    def _orphans(self, _p: dict[str, Any], _loop: Any) -> tuple[str, bool]:
        from .admin import caches, clean

        live = {(u, a) for u, a, _d in self.loops()}
        users = {u.name for u in self.store.users()}
        gone = [c for c in caches(live, users) if c["kind"] == "gone"]   # a user's, its loop deleted
        freed = sum(clean(c["key"], "all") for c in gone)
        return f"{len(gone)} orphaned cache(s) removed, {_mb(freed)} freed", bool(gone)

    def _runs(self, p: dict[str, Any], _loop: Any) -> tuple[str, bool]:
        keep = max(1, int(p["keep"]))
        with self.store._db() as db:
            n = db.execute(
                "DELETE FROM runs WHERE ended IS NOT NULL AND id NOT IN ("
                " SELECT id FROM (SELECT id, ROW_NUMBER() OVER (PARTITION BY user_id, app ORDER BY id DESC) AS k FROM runs)"
                " WHERE k <= ?)", (keep,)).rowcount
        return f"{n} old start(s) removed, the latest {keep} of each loop kept", n > 0

    def _journals(self, p: dict[str, Any], loop: tuple[str, str] | None) -> tuple[str, bool]:
        from .admin import _key, cache_root

        idle, busy = self._loops(loop)
        limit, keep = float(p["max_mb"]) * 1e6, float(p["keep_mb"]) * 1e6
        n, freed = 0, 0
        # D1071: running loops too -- a journal line is appended on its own, as a transcript's turn
        for u, a, _d in idle + [x for x in self.loops() if f"{x[0]}/{x[1]}" in busy]:
            for ev in sorted((cache_root() / _key(u, a)).glob("tmp/flux-traces/*/events.jsonl")):
                if ev.is_symlink() or ev.stat().st_size <= limit:
                    continue
                got = _condense_journal(ev, int(keep))
                if got:
                    n, freed = n + 1, freed + got
        return f"{n} journal(s) condensed, {_mb(freed)} freed", n > 0

    def _transcripts(self, p: dict[str, Any], loop: tuple[str, str] | None) -> tuple[str, bool]:
        from .admin import _key, cache_root

        idle, busy = self._loops(loop)
        cutoff = time.time() - float(p["days"]) * DAY
        n, freed = 0, 0
        for u, a, _d in idle + [x for x in self.loops() if f"{x[0]}/{x[1]}" in busy]:
            for tr in sorted((cache_root() / _key(u, a)).glob("tmp/flux-traces/*/turns.jsonl")):
                if tr.is_symlink():
                    continue
                got = _thin_transcript(tr, cutoff)
                if got:
                    n, freed = n + 1, freed + got
        return f"{n} transcript(s) thinned, {_mb(freed)} freed", n > 0

    def _passes(self, p: dict[str, Any], loop: tuple[str, str] | None) -> tuple[str, bool]:
        import re as _re

        from .admin import _key, cache_root

        idle, busy = self._loops(loop)
        cutoff = time.time() - float(p["days"]) * DAY
        n, freed = 0, 0
        for u, a, _d in idle + [x for x in self.loops() if f"{x[0]}/{x[1]}" in busy]:
            for run in (cache_root() / _key(u, a)).glob("tmp/flux-traces/*"):
                if not run.is_dir() or run.is_symlink():
                    continue
                dated = sorted(d for d in run.iterdir() if d.is_dir() and not d.is_symlink() and _re.fullmatch(r"\d{8}T\d{6}.*", d.name))
                for d in dated[:-1]:                                  # the newest stays: the pass at work
                    try:
                        started = time.mktime(time.strptime(d.name[:15], "%Y%m%dT%H%M%S"))
                    except ValueError:
                        continue
                    if started >= cutoff or _in_use(d):
                        continue
                    size = _tree_bytes(d)
                    shutil.rmtree(d, ignore_errors=True)
                    if not d.exists():
                        n, freed = n + 1, freed + size
        return f"{n} pass folder(s) removed, {_mb(freed)} freed", n > 0

    def _refused(self, p: dict[str, Any], _loop: Any) -> tuple[str, bool]:
        import json

        from .store import _REFUSALS

        f = Path(self.store.refusals_file)
        if not f.is_file():
            return "no refused-host log yet", False
        cutoff = time.time() - float(p["days"]) * DAY
        with _REFUSALS:                                   # the sampler reads it under the same lock
            self.store._take_refusals()                   # what is new goes to the audit first
            data = f.read_bytes()
            kept = []
            for raw in data.splitlines():
                try:
                    t = float(json.loads(raw).get("t") or 0)
                except (ValueError, AttributeError):
                    t = 0
                if t >= cutoff:
                    kept.append(raw)
            dropped = len(data.splitlines()) - len(kept)
            if not dropped:
                return "nothing older than the kept days", False
            late = f.read_bytes()[len(data):]             # a proxy's line written meanwhile (it opens per line)
            tmp = f.with_name(f.name + ".pruning")
            tmp.write_bytes(b"\n".join(kept) + (b"\n" if kept else b"") + late)
            os.chmod(tmp, f.stat().st_mode & 0o777)
            os.replace(tmp, f)
            st = f.stat()
            self.store.server_set("refusals_read", {"ino": st.st_ino, "offset": st.st_size - len(late)})
        return f"{dropped} refused connection(s) older than {p['days']:g} days removed", True

    def _baseline(self, _p: dict[str, Any], loop: tuple[str, str] | None) -> tuple[str, bool]:
        """D1005: marks the loop -- its next start measures the baseline again, then the mark goes."""
        if loop is None:
            raise ValueError("a baseline is measured again on one loop, from its settings")
        from .workspace import Workspace

        w = Workspace(self.store.data, loop[0])
        if not w.meta(loop[1]).get("document") and not (w.root / loop[1]).is_dir():
            raise ValueError(f"no loop {loop[0]}/{loop[1]}")
        w.set_meta(loop[1], rerun_baseline=True)
        return "the next start measures the baseline again (once)", True

    def _reevaluate(self, _p: dict[str, Any], loop: tuple[str, str] | None) -> tuple[str, bool]:
        """D988: not a clean-up of its own but a run of the loop; never on a schedule, never on all."""
        if loop is None or getattr(self, "_start", None) is None:
            raise ValueError("re-evaluation is started on one loop, from its settings")
        if self.live(*loop):
            raise ValueError("the loop is running: stop it first")
        self._start()
        return "started: every design on record is checked and measured again (Live shows it)", True

    def _empty(self, _p: dict[str, Any], loop: tuple[str, str] | None) -> tuple[str, bool]:
        """D1000: the starts that did nothing, forgotten as the history's own read does (D987),
        on loops at rest; a running loop's starts are left alone."""
        if self.runs is None:
            return "no run history here", False
        idle, busy = self._loops(loop)
        gone, per = 0, []
        for u, a, _d in idle:
            user = self.store.user(name=u)
            if user is None:
                continue
            n = sum(1 for r in self.store.runs(user, a) if r.get("ended") and self.runs.forget_if_empty(r["id"]))
            if n:
                gone += n
                per.append(f"{u}/{a}: {n}")
        return (f"{gone} empty start(s) removed" + (f" ({', '.join(per[:6])})" if per else "")
                + self._skipped(busy)), gone > 0

    def _stale(self, p: dict[str, Any], loop: tuple[str, str] | None) -> str:
        idle, busy = self._loops(loop)
        found = gone = 0
        per = []
        for u, a, d in idle:
            try:
                from flux_loop.agent import added_agents

                from .agents import added_kinds

                with added_agents(lambda: added_kinds(self.store)):     # D934: its added agents known
                    rows = stale_rows(d)
            except Exception as exc:  # noqa: BLE001 -- a loop whose document does not load: said, the rest go on
                per.append(f"{u}/{a}: not read ({exc!s:.80})")
                continue
            if not rows:
                continue
            found += sum(len(ids) for ids in rows.values())
            per.append(f"{u}/{a}: {sum(len(ids) for ids in rows.values())}")
            if p.get("delete"):
                for db, ids in rows.items():
                    con = sqlite3.connect(db, timeout=60)
                    try:
                        with con:
                            con.executemany("DELETE FROM trials WHERE id = ?", [(i,) for i in ids])
                        gone += len(ids)
                    finally:
                        con.close()
        head = (f"{found} stale row(s), {gone} deleted" if p.get("delete") else f"{found} stale row(s) found, none deleted (delete: off)")
        return head + (f" -- {'; '.join(per[:6])}" if per else "") + self._skipped(busy), bool(found)


def stale_rows(app_dir: Path) -> dict[Path, list[int]]:
    """A loop's record rows measured under other inputs than today's (D853), by record: the rows the
    loop no longer counts. A loop with no document or no record has none."""
    import json

    from flux_loop import LoopRequest, PromptProblem, load_task
    from flux_loop.records import fresh
    from flux_loop.types import Candidate, LoopState

    meta = {}
    try:
        meta = json.loads((app_dir / ".flux-app.json").read_text())
    except (OSError, ValueError):
        pass
    doc = app_dir / str(meta.get("document") or "problem.yaml")
    if not doc.is_file():
        return {}
    from flux_loop.document import confined

    with confined(app_dir):                            # D905: the server reads nothing outside the loop
        problem = PromptProblem(load_task(str(doc)))
    stages = set(problem.stages() or [])
    state = LoopState(request=LoopRequest(db=""), say=lambda _m: None, proposer=None, feedback=None)
    out: dict[Path, list[int]] = {}
    for db in sorted((app_dir / "out").glob("*.db")) if (app_dir / "out").is_dir() else []:
        from flux_store import CampaignStore

        try:
            CampaignStore(str(db)).close()           # the record's tables as today's store keeps them
        except sqlite3.Error:
            continue
        con = sqlite3.connect(db, timeout=60)
        try:                                         # D1072: a design's text kept once, beside its rows
            rows = con.execute("SELECT t.id, t.stage, t.candidate_json, a.text FROM trials t "
                               "LEFT JOIN artifacts a ON a.key = t.artifact_key WHERE t.status = 'ok'").fetchall()
        except sqlite3.Error:
            rows = []
        finally:
            con.close()
        ids = []
        for rid, stage, cj, art in rows:
            if stage not in stages:
                continue
            try:
                c = json.loads(cj)
            except ValueError:
                continue
            if art is not None:
                c["artifact"] = art
            cand = Candidate.from_record(c)        # D886: read as the loop reads it, or a good row looks stale
            try:
                problem.cache_key(cand, stage, state)
            except Exception:  # noqa: BLE001 -- what cannot be keyed today is not called stale
                continue
            if not fresh(problem, cand, stage, state):
                ids.append(rid)
        if ids:
            out[db] = ids
    return out


def scratch_root(data: Path | None = None) -> tuple[Path | None, str]:
    """The folder Clean scratch may empty, or (None, why not). Only FLUX_TMPDIR, said and absolute --
    never a default: unset, `Path("")` is the working folder, and a test run from the repository once
    lost files there (D885) -- and never a folder that holds a repository, a project, a home or the
    server's data."""
    raw = (os.environ.get("FLUX_TMPDIR") or "").strip()
    if not raw:
        return None, "no FLUX_TMPDIR on this server: nothing to clean"
    root = Path(raw)
    if not root.is_absolute():
        return None, f"FLUX_TMPDIR {raw!r} is not an absolute path: nothing cleaned"
    try:
        root = root.resolve(strict=True)
    except OSError:
        return None, f"FLUX_TMPDIR {raw} does not exist: nothing to clean"
    if not root.is_dir():
        return None, f"FLUX_TMPDIR {raw} is not a folder: nothing cleaned"
    homes = {Path("/").resolve(), Path.home().resolve()}
    marks = [m for m in (".git", "pyproject.toml", "flake.nix", "flux-web.db", ".flux-app.json") if (root / m).exists()]
    if root in homes or marks or (data is not None and Path(data).resolve().is_relative_to(root)):
        return None, f"FLUX_TMPDIR {root} is not a scratch folder ({', '.join(marks) or 'a home, the root or the server data'}): nothing cleaned"
    return root, ""


# ---- helpers
def _mb(n: float) -> str:
    return f"{n / 1e6:,.1f} MB"


def _size(p: Path) -> int:
    from .admin import dir_size

    try:
        return dir_size(p, fresh=True) if p.is_dir() else p.stat().st_size
    except OSError:
        return 0


def _size_db(db: Path) -> int:
    return sum(Path(f"{db}{s}").stat().st_size for s in ("", "-wal", "-shm") if Path(f"{db}{s}").exists())


def _touched_after(p: Path, cutoff: float) -> bool:
    """Whether anything in `p` changed after `cutoff` (stops at the first that did)."""
    try:
        if p.lstat().st_mtime > cutoff:
            return True
        if not p.is_dir():
            return False
        for top, dirs, files in os.walk(p):
            for name in (*dirs, *files):
                try:
                    if os.lstat(os.path.join(top, name)).st_mtime > cutoff:
                        return True
                except OSError:
                    continue
    except OSError:
        return True
    return False


def _paths_in_use() -> set[str]:
    """Every process's working folder and open files on this machine (that this user can read)."""
    out: set[str] = set()
    for pid in os.listdir("/proc") if os.path.isdir("/proc") else []:
        if not pid.isdigit():
            continue
        try:
            out.add(os.readlink(f"/proc/{pid}/cwd"))
            for fd in os.listdir(f"/proc/{pid}/fd"):
                try:
                    out.add(os.readlink(f"/proc/{pid}/fd/{fd}"))
                except OSError:
                    continue
        except OSError:
            continue
    return out


def _last_run(app_dir: Path) -> float:
    """When the loop last wrote its log or record: its last activity."""
    got = 0.0
    for p in (app_dir / "runs" / "loop.log", *((app_dir / "out").glob("*.db") if (app_dir / "out").is_dir() else ())):
        try:
            got = max(got, p.stat().st_mtime)
        except OSError:
            continue
    return got


#: D1069: what a thinned turn keeps -- what usage and Insights count
_TURN_KEEPS = ("ts", "kind", "agent", "model", "about", "server", "ok", "rc", "seconds", "tokens_in", "tokens_out",
               "tokens_cached", "tokens_complete", "cost_usd", "priced")


def _thin_transcript(tr: Path, cutoff: float) -> int:
    """A transcript's turns before `cutoff` keep only `_TURN_KEEPS` (and an error's first words); the
    newer ones stay whole. Read and written a line at a time; a turn appended meanwhile is kept (the
    loop opens the file for each one). Returns the bytes freed, 0 when nothing was older."""
    size = tr.stat().st_size
    tmp = tr.with_name(tr.name + ".thinning")
    thinned = 0
    with open(tr, "rb") as src, open(tmp, "wb") as dst:
        read = 0
        for raw in src:
            if read + len(raw) > size or not raw.endswith(b"\n"):
                break
            read += len(raw)
            try:
                t = json.loads(raw)
            except ValueError:
                dst.write(raw)
                continue
            if not isinstance(t, dict) or float(t.get("ts") or 0) >= cutoff or t.get("thinned"):
                dst.write(raw)
                continue
            kept = {k: t[k] for k in _TURN_KEEPS if k in t}
            notes = t.get("notes") if isinstance(t.get("notes"), dict) else {}
            if any(k in notes for k in ("input_tokens", "output_tokens")):   # an agent's own count, where usage reads it
                kept["notes"] = {k: notes[k] for k in ("input_tokens", "output_tokens") if k in notes}
            for k in ("error", "stderr"):
                if t.get(k):
                    kept[k] = " ".join(str(t[k]).split())[-300:]
            kept["thinned"] = True
            line = json.dumps(kept, ensure_ascii=False).encode() + b"\n"
            if len(line) < len(raw):
                thinned += len(raw) - len(line)
            dst.write(line if len(line) < len(raw) else raw)
        if not thinned:
            dst.close()
            tmp.unlink(missing_ok=True)
            return 0
        src.seek(read)
        shutil.copyfileobj(src, dst)                  # turns written while it was read
    os.chmod(tmp, tr.stat().st_mode & 0o777)
    os.replace(tmp, tr)
    return thinned


def _condense_journal(ev: Path, keep: int) -> int:
    """A task journal (D761) keeps its recent starts whole: cut at the earliest start (a hello mark)
    within the last `keep` bytes -- else at the latest start, so it alone is kept -- the part before
    appended to `events.jsonl.1.gz`, `marks.jsonl`'s offsets moved to match. The bytes freed.
    D1071: a start that alone is far over `keep` (a loop running for days in one start) is cut at a
    pass within it, the kept part headed by its start's own hello line; read and written in pieces,
    never the journal at once; a line appended meanwhile is kept (the loop opens it for each line)."""
    import json

    marks = ev.with_name("marks.jsonl")
    try:
        rows = [json.loads(x) for x in marks.read_bytes().splitlines() if x.strip()]
    except (OSError, ValueError):
        return 0                                         # no index to trust: left whole
    size = ev.stat().st_size
    at = lambda r: int(r.get("at", -1))                  # noqa: E731
    hellos = [at(r) for r in rows if r.get("ev") == "hello" and 0 <= at(r) < size]
    if not hellos:
        return 0
    cut = next((x for x in hellos if x >= size - keep), hellos[-1])
    head = b""
    if cut == hellos[-1] and size - cut > 2 * keep:      # one start, far over what is kept: cut at a pass in it
        passes = [at(r) for r in rows if r.get("ev") == "pass" and cut < at(r) < size]
        within = next((x for x in passes if x >= size - keep), passes[-1] if passes else None)
        if within is not None:
            with open(ev, "rb") as fh:
                fh.seek(cut)
                head = fh.readline()                     # its hello: the kept part is still that start
            cut = within
    if cut <= 0:
        return 0
    chunk = 8 << 20
    tmp = ev.with_name(ev.name + ".condensing")
    with open(ev, "rb") as src:
        _gz_head(src, cut, ev.with_name(ev.name + ".1.gz"), chunk)
        with open(tmp, "wb") as dst:
            dst.write(head)
            src.seek(cut)
            shutil.copyfileobj(src, dst, chunk)          # the rest, and what was appended while it ran
    shift = cut - len(head)
    kept = ([json.dumps({"at": 0, "ev": "hello", "n": None}).encode() + b"\n"] if head else []) + [
        json.dumps({**r, "at": at(r) - shift}).encode() + b"\n" for r in rows if at(r) >= cut]
    mtmp = marks.with_name(marks.name + ".condensing")
    mtmp.write_bytes(b"".join(kept))
    for t, path in ((tmp, ev), (mtmp, marks)):
        os.chmod(t, path.stat().st_mode & 0o777)
        os.replace(t, path)
    return shift


def _tree_bytes(d: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(d):
        for f in files:
            with contextlib.suppress(OSError):
                total += os.lstat(os.path.join(root, f)).st_size
    return total


def _in_use(d: Path) -> bool:
    """D1071: a process works in it (its current folder is under it) -- an agent still continuing an old pass."""
    real = str(d.resolve())
    for proc in Path("/proc").glob("[0-9]*"):
        with contextlib.suppress(OSError):
            cwd = os.readlink(proc / "cwd")
            if cwd == real or cwd.startswith(real + os.sep):
                return True
    return False


def _gz_head(src, cut: int, gz_path: Path, chunk: int) -> None:
    """D1071: the first `cut` bytes of `src` appended to `gz_path`, a piece at a time."""
    src.seek(0)
    with gzip.open(gz_path, "ab") as gz:
        left = cut
        while left > 0:
            piece = src.read(min(chunk, left))
            if not piece:
                break
            gz.write(piece)
            left -= len(piece)


def _condense(log: Path, keep: int) -> int:
    """The log's last `keep` bytes stay, from a start's header (or a line) on; what came before is
    appended to `loop.log.1.gz` beside it. The bytes freed. D1071: read and written in pieces."""
    size = log.stat().st_size
    cut = max(0, size - keep)
    with open(log, "rb") as fh:
        fh.seek(cut)
        tail = fh.read(keep)                             # only the kept part is looked through
    start = tail.find("\n── started ".encode())
    nl = tail.find(b"\n")
    cut = cut + (start + 1 if start >= 0 else nl + 1 if nl >= 0 else 0)
    if cut <= 0:
        return 0
    chunk = 8 << 20
    note = f"(the log before this line is in {log.name}.1.gz -- condensed {time.strftime('%Y-%m-%d %H:%M')})\n".encode()
    tmp = log.with_name(log.name + ".condensing")
    with open(log, "rb") as src:
        _gz_head(src, cut, log.with_name(log.name + ".1.gz"), chunk)
        with open(tmp, "wb") as dst:
            dst.write(note)
            src.seek(cut)
            shutil.copyfileobj(src, dst, chunk)
    os.chmod(tmp, log.stat().st_mode & 0o777)
    os.replace(tmp, log)
    return cut - len(note)
