"""Maintenance (D885): the clean-up an admin schedules, as Gitea's cron tasks. Each task has a
schedule (every so many hours), an on/off switch, its settings and its last results; `tick` runs the
ones due, one at a time on a thread of its own, and Run now runs one at once. Four act on loops --
a loop's record, log, stale rows, caches -- over every loop, or one when asked (its owner may, from
the loop's Settings). A running loop is never touched: its files are being written. Every run is in
the audit trail."""

from __future__ import annotations

import fnmatch
import gzip
import os
import shutil
import sqlite3
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
         "loop's record.", 168, True, per_loop=True),
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
    Task("refused", "Prune refused-host log", "The sandbox proxies' log of refused connections keeps its last "
         "days (Insights > Network reads it).", 24, True, {"days": 30}),
    Task("stale", "Prune stale records", "Record rows measured under other inputs (D853): the loop no longer "
         "counts them. Reports them; deletes them when told to.", 168, False, {"delete": False}, per_loop=True),
)}


class Maintenance:
    """The tasks' settings, results and runs, for one server. `live(user, app)` says whether a
    loop runs now; `loops()` lists (user, app, folder)."""

    HISTORY = 20

    def __init__(self, store: Any, live: Callable[[str, str], bool],
                 loops: Callable[[], list[tuple[str, str, Path]]]) -> None:
        self.store, self.live, self.loops = store, live, loops
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

    def run(self, key: str, *, by: str = "schedule", loop: tuple[str, str] | None = None) -> dict[str, Any]:
        """One task now; its result (also kept, and in the audit trail). `loop`: (user, app) only."""
        t = TASKS[key]
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
        idle, busy = self._loops(loop)
        before = after = 0
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
        return f"{len(dbs)} database(s) compacted, {_mb(before)} to {_mb(after)}" + self._skipped(busy), bool(dbs)

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
        for u, a, _d in idle:
            for ev in sorted((cache_root() / _key(u, a)).glob("tmp/flux-traces/*/events.jsonl")):
                if ev.is_symlink() or ev.stat().st_size <= limit:
                    continue
                got = _condense_journal(ev, int(keep))
                if got:
                    n, freed = n + 1, freed + got
        return f"{n} journal(s) condensed, {_mb(freed)} freed" + self._skipped(busy), n > 0

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
        con = sqlite3.connect(db, timeout=60)
        try:
            rows = con.execute("SELECT id, stage, candidate_json FROM trials WHERE status = 'ok'").fetchall()
        except sqlite3.Error:
            rows = []
        finally:
            con.close()
        ids = []
        for rid, stage, cj in rows:
            if stage not in stages:
                continue
            try:
                c = json.loads(cj)
            except ValueError:
                continue
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


def _condense_journal(ev: Path, keep: int) -> int:
    """A task journal (D761) keeps its recent starts whole: cut at the earliest start (a hello mark)
    within the last `keep` bytes -- else at the latest start, so it alone is kept -- the part before
    appended to `events.jsonl.1.gz`, `marks.jsonl`'s offsets moved to match. The bytes freed."""
    import json

    marks = ev.with_name("marks.jsonl")
    try:
        rows = [json.loads(x) for x in marks.read_bytes().splitlines() if x.strip()]
    except (OSError, ValueError):
        return 0                                         # no index to trust: left whole
    data = ev.read_bytes()
    hellos = [int(r["at"]) for r in rows if r.get("ev") == "hello" and int(r.get("at", -1)) < len(data)]
    if not hellos:
        return 0
    cut = next((at for at in hellos if at >= len(data) - keep), hellos[-1])
    if cut <= 0:
        return 0
    with gzip.open(ev.with_name(ev.name + ".1.gz"), "ab") as gz:
        gz.write(data[:cut])
    for path, body in ((ev, data[cut:]),
                       (marks, b"".join(json.dumps({**r, "at": int(r["at"]) - cut}).encode() + b"\n"
                                        for r in rows if int(r.get("at", -1)) >= cut))):
        tmp = path.with_name(path.name + ".condensing")
        tmp.write_bytes(body)
        os.chmod(tmp, path.stat().st_mode & 0o777)
        os.replace(tmp, path)
    return cut


def _condense(log: Path, keep: int) -> int:
    """The log's last `keep` bytes stay, from a start's header (or a line) on; what came before is
    appended to `loop.log.1.gz` beside it. The bytes freed."""
    data = log.read_bytes()
    cut = max(0, len(data) - keep)
    start = data.find("\n── started ".encode(), cut)
    cut = start + 1 if start >= 0 else (data.find(b"\n", cut) + 1 or cut)
    if cut <= 0:
        return 0
    with gzip.open(log.with_name(log.name + ".1.gz"), "ab") as gz:
        gz.write(data[:cut])
    note = f"(the log before this line is in {log.name}.1.gz -- condensed {time.strftime('%Y-%m-%d %H:%M')})\n".encode()
    tmp = log.with_name(log.name + ".condensing")
    tmp.write_bytes(note + data[cut:])
    os.chmod(tmp, log.stat().st_mode & 0o777)
    os.replace(tmp, log)
    return cut - len(note)
