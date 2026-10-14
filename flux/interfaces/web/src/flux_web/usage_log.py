"""The model and agent turns, summed as they happen (D1069): every loop's transcript (`turns.jsonl`) is
read once, a line at a time from where the last read stopped, into the server's database -- per minute,
loop and endpoint: turns, failures, seconds, tokens, cost, the last failure, and the seconds as a coarse
histogram (the median and the slow 95th); and the tokens per minute spread over the time each turn took
(the token-rate chart). Admin › Insights and Resources read these small tables; no page reads a
transcript. Minutes older than two days are summed into hours; hours are kept a year."""

from __future__ import annotations

import json
import math
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

__all__ = ["UsageLog"]

DAY = 86400
_REPORTED = __import__("re").compile(r"^\s*([\d,.]+|—) in, ([\d,.]+|—) out")   # an agent row's "tokens (reported)" (D1085)
MINUTE_DAYS = 2                        # minutes kept this long, then summed into hours
KEEP_DAYS = 400                        # hours kept this long
COMPACT_S = 3600


def _bin(secs: float) -> int:
    """A turn's seconds as a half-octave bin (0.01 s up): coarse enough to sum, fine enough for p50 / p95."""
    return max(0, int(math.floor(2 * math.log2(max(secs, 0.01) / 0.01))))


def _bin_secs(b: int) -> float:
    return 0.01 * 2 ** ((b + 0.5) / 2)


def _quantile(hist: dict[int, int], f: float) -> float:
    n = sum(hist.values())
    if not n:
        return 0.0
    want, seen = min(n - 1, int(f * n)), 0
    for b in sorted(hist):
        seen += hist[b]
        if seen > want:
            return round(_bin_secs(b), 3)
    return 0.0


class UsageLog:
    def __init__(self, db: str | Path, clock: Any = None) -> None:
        self.db = str(db)
        self._journals: dict[str, tuple[int, int]] = {}      # D1085: journal -> (inode, bytes read), this server's
        self._live_rows: dict[str, dict[Any, tuple[float, float]]] = {}   # journal -> row -> its tokens counted
        self._clock = clock or (lambda: time.time())    # what "now" is (a test's own time)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._caught_up = threading.Event()
        self._thread: threading.Thread | None = None
        self._next_compact = 0.0
        with self._con() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS usage_turns (t INTEGER NOT NULL, span INTEGER NOT NULL, path TEXT NOT NULL,
                user TEXT NOT NULL, app TEXT NOT NULL, kind TEXT NOT NULL, who TEXT NOT NULL, at TEXT NOT NULL,
                turns INTEGER NOT NULL, fails INTEGER NOT NULL, secs REAL NOT NULL, tin REAL NOT NULL, tout REAL NOT NULL,
                cost REAL NOT NULL, last REAL NOT NULL, fail_t REAL, fail_err TEXT, hist TEXT NOT NULL,
                PRIMARY KEY (t, span, path, user, app, kind, who, at))""")
            c.execute("""CREATE TABLE IF NOT EXISTS usage_rate (t INTEGER NOT NULL, span INTEGER NOT NULL, path TEXT NOT NULL,
                kind TEXT NOT NULL, tin REAL NOT NULL, tout REAL NOT NULL, PRIMARY KEY (t, span, path, kind))""")
            c.execute("""CREATE TABLE IF NOT EXISTS usage_files (path TEXT PRIMARY KEY, ino INTEGER NOT NULL,
                offset INTEGER NOT NULL, last REAL NOT NULL)""")
            # D1072: a loop's own sums (its Usage tab), per agent or model -- counted from the start: a log
            # counted before it is counted again
            if not c.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'usage_loops'").fetchone():
                c.execute("""CREATE TABLE usage_loops (path TEXT NOT NULL, kind TEXT NOT NULL, who TEXT NOT NULL,
                    sums TEXT NOT NULL, first REAL, last REAL, PRIMARY KEY (path, kind, who))""")
                for table in ("usage_files", "usage_turns", "usage_rate"):
                    c.execute(f"DELETE FROM {table}")

    def _con(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db, timeout=10)

    # ---- in
    def ingest(self, files: list[tuple[str, str, str]]) -> int:
        """Read what each (user, app, transcript path) added since the last read; the turns counted.
        A transcript replaced or cut (priced again, thinned by maintenance) is counted again from its
        start, what it counted before taken out: each row says the transcript it came from."""
        from .insights import turn_of
        from .usage import Lines

        n = 0
        with self._lock:
            with self._con() as c:
                seen = {p: (ino, off, last) for p, ino, off, last in c.execute("SELECT path, ino, offset, last FROM usage_files")}
            for user, app, path in sorted(set(files)):
                ino, off, last = seen.get(path, (None, 0, 0.0))
                lines = Lines(path, off)
                if lines.ino is None:
                    continue
                if lines.ino != ino or lines.size < off:          # replaced or cut: counted again, from its start
                    lines = Lines(path, 0)
                    with self._con() as c:
                        c.execute("DELETE FROM usage_turns WHERE path = ?", (path,))
                        c.execute("DELETE FROM usage_rate WHERE path = ?", (path,))
                        c.execute("DELETE FROM usage_loops WHERE path = ?", (path,))
                turns, rate, loops, newest = {}, {}, {}, last
                for raw in lines:
                    try:
                        doc = json.loads(raw)
                    except ValueError:
                        continue
                    if isinstance(doc, dict):
                        _loop_add(loops, doc)
                    t = turn_of(doc)
                    if t is None:
                        continue
                    # a turn stamped after now (a clock ahead) is not spread over the minutes before it; D1085: nor
                    # one whose tokens were counted as they came, from its journal (`live`)
                    self._add(turns, rate if t[0] <= self._clock() and not doc.get("streamed") else {}, path, user, app, t)
                    newest = max(newest, t[0])
                    n += 1
                    if len(turns) > 5000:                          # a long transcript in batches
                        self._write(turns, rate)
                        turns, rate = {}, {}
                self._write(turns, rate)
                self._write_loops(path, loops)
                with self._con() as c:
                    c.execute("INSERT OR REPLACE INTO usage_files VALUES (?, ?, ?, ?)", (path, lines.ino, lines.offset, newest))
        if self._clock() >= self._next_compact:
            self.compact()
        return n

    @staticmethod
    def _add(turns: dict, rate: dict, path: str, user: str, app: str, t: tuple) -> None:
        ts, kind, who, where, ok, secs, tin, tout, cost, err = t
        key = (int(ts // 60 * 60), 60, path, user, app, kind, who, where)
        r = turns.setdefault(key, {"turns": 0, "fails": 0, "secs": 0.0, "tin": 0.0, "tout": 0.0, "cost": 0.0,
                                   "last": 0.0, "fail_t": None, "fail_err": None, "hist": {}})
        r["turns"] += 1
        r["secs"] += secs
        r["tin"] += tin
        r["tout"] += tout
        r["cost"] += cost
        r["last"] = max(r["last"], ts)
        r["hist"][_bin(secs)] = r["hist"].get(_bin(secs), 0) + 1
        if ok is False:
            r["fails"] += 1
            if r["fail_t"] is None or ts >= r["fail_t"]:
                r["fail_t"], r["fail_err"] = ts, err
        # the tokens per minute, spread over the time the turn took (D838)
        if (tin or tout) and all(math.isfinite(v) for v in (ts, secs, tin, tout)) and tin >= 0 and tout >= 0:
            start = ts - (secs if secs > 0 else 1.0)
            k = "agent" if kind == "agent" else "model"
            m = int(start // 60 * 60)
            while m < ts:
                share = (min(ts, m + 60) - max(start, m)) / (ts - start)
                if share > 0:
                    x = rate.setdefault((m, 60, path, k), [0.0, 0.0])
                    x[0] += tin * share
                    x[1] += tout * share
                m += 60

    def _write(self, turns: dict, rate: dict) -> None:
        if not turns and not rate:
            return
        with self._con() as c:
            for key, r in turns.items():
                _put(c, key, r)
            for key, (tin, tout) in rate.items():
                c.execute("INSERT INTO usage_rate VALUES (?,?,?,?,?,?) ON CONFLICT (t, span, path, kind) DO UPDATE SET "
                          "tin = tin + excluded.tin, tout = tout + excluded.tout", (*key, tin, tout))

    def _write_loops(self, path: str, loops: dict) -> None:
        if not loops:
            return
        from .usage import _empty

        with self._con() as c:
            for (kind, who), (sums, first, last) in loops.items():
                got = c.execute("SELECT sums, first, last FROM usage_loops WHERE path = ? AND kind = ? AND who = ?",
                                (path, kind, who)).fetchone()
                if got:
                    was = {**_empty(), **json.loads(got[0])}
                    sums = {k: was[k] + sums[k] for k in sums}
                    first = min((x for x in (first, got[1]) if x), default=None)
                    last = max((x for x in (last, got[2]) if x), default=None)
                c.execute("INSERT OR REPLACE INTO usage_loops VALUES (?, ?, ?, ?, ?, ?)",
                          (path, kind, who, json.dumps(sums), first, last))

    def loop(self, path: str | None) -> dict[str, Any]:
        """A loop's usage as `usage.usage` says it, from the sums (D1072): {total, by, first, last}."""
        from .usage import _empty

        total, by, first, last = _empty(), [], None, None
        if path:
            with self._con() as c:
                rows = c.execute("SELECT kind, who, sums, first, last FROM usage_loops WHERE path = ?", (path,)).fetchall()
            for kind, who, sums, f, la in rows:
                b = {"kind": kind, "who": who, **_empty(), **json.loads(sums)}
                by.append(b)
                for k in total:
                    total[k] += b[k]
                first = min((x for x in (first, f) if x), default=None)
                last = max((x for x in (last, la) if x), default=None)
        return {"total": total, "by": sorted(by, key=lambda b: -b["seconds"]), "first": first, "last": last}

    def live(self, journals: list[str]) -> int:
        """D1085: the tokens agents report as they work -- the "tokens (reported)" on their journal rows, read
        as the journals grow -- into the minutes they came in (the rate, not the turns: a turn's transcript line
        still counts it, its `streamed` keeps it from being spread again). A journal is read from where it was
        when first seen (its past is the transcripts'); a row whose start was not read here counts from its
        next report on. The tokens of a turn: what its last report says, less what was counted before."""
        from .usage import Lines

        n = 0
        rate: dict = {}
        with self._lock:
            for path in sorted(set(journals)):
                ino, off = self._journals.get(path, (None, None))
                lines = Lines(path, off or 0)
                if lines.ino is None:
                    continue
                if off is None or lines.ino != ino or lines.size < off:    # first seen, replaced or cut: from its end
                    self._journals[path] = (lines.ino, lines.size)
                    self._live_rows[path] = {}
                    continue
                rows = self._live_rows.setdefault(path, {})
                for raw in lines:
                    if b'"ev": "start"' in raw[:80] or b'"ev":"start"' in raw[:80]:
                        try:
                            rows[json.loads(raw)["id"]] = (0.0, 0.0)          # its tokens count from nothing
                        except (ValueError, KeyError, TypeError):
                            pass
                        continue
                    if b"tokens (reported)" not in raw:
                        continue
                    try:
                        e = json.loads(raw)
                        said = ((e.get("fields") or {}) if e.get("ev") == "update" else (e.get("output") or {})).get("tokens (reported)")
                        m = _REPORTED.match(str(said or ""))
                        if not m:
                            continue
                        tin, tout = (float(x.replace(",", "")) if x != "—" else 0.0 for x in m.groups())
                        t = float(e["t"])
                    except (ValueError, KeyError, TypeError):
                        continue
                    was = rows.get(e.get("id"))
                    rows[e.get("id")] = (max(tin, was[0] if was else 0.0), max(tout, was[1] if was else 0.0))
                    if was is None:
                        continue                                      # first seen mid-turn: from its next report
                    din, dout = max(0.0, tin - was[0]), max(0.0, tout - was[1])
                    if din or dout:
                        x = rate.setdefault((int(t // 60 * 60), 60, path, "agent"), [0.0, 0.0])
                        x[0] += din
                        x[1] += dout
                        n += 1
                    if e.get("ev") == "end":
                        rows.pop(e.get("id"), None)
                self._journals[path] = (lines.ino, lines.offset)
            self._write({}, rate)
        return n

    def compact(self, now: float | None = None) -> None:
        """Minutes older than two days summed into hours; hours older than a year gone."""
        now = self._clock() if now is None else now
        cut = int((now - MINUTE_DAYS * DAY) // 3600 * 3600)
        with self._lock, self._con() as c:
            hours: dict[tuple, dict] = {}
            for row in c.execute("SELECT t, path, user, app, kind, who, at, turns, fails, secs, tin, tout, cost, last, fail_t, fail_err, hist "
                                 "FROM usage_turns WHERE span = 60 AND t < ?", (cut,)):
                key = (int(row[0] // 3600 * 3600), 3600, *row[1:7])
                hours[key] = _merge(hours[key], _row(row[7:])) if key in hours else _row(row[7:])
            c.execute("DELETE FROM usage_turns WHERE span = 60 AND t < ?", (cut,))
            for key, r in hours.items():
                _put(c, key, r)
            c.execute("INSERT INTO usage_rate SELECT t / 3600 * 3600, 3600, path, kind, SUM(tin), SUM(tout) FROM usage_rate "
                      "WHERE span = 60 AND t < ? GROUP BY t / 3600, path, kind ON CONFLICT (t, span, path, kind) DO UPDATE SET "
                      "tin = tin + excluded.tin, tout = tout + excluded.tout", (cut,))
            c.execute("DELETE FROM usage_rate WHERE span = 60 AND t < ?", (cut,))
            c.execute("DELETE FROM usage_turns WHERE t < ?", (now - KEEP_DAYS * DAY,))
            c.execute("DELETE FROM usage_rate WHERE t < ?", (now - KEEP_DAYS * DAY,))
        self._next_compact = now + COMPACT_S

    def fresh(self, files: Any, journals: Any = None) -> None:
        """Before a page reads: on a server the sampler keeps the log (seconds behind, D1085), and its
        first catch-up after an update may take minutes -- a page never waits on it; without a sampler
        (a test, a one-off), read now."""
        if self._thread is None:
            self.ingest(files())
            if journals is not None:
                self.live(journals())

    @property
    def catching_up(self) -> bool:
        """True until the sampler's first read of every transcript is done."""
        return self._thread is not None and not self._caught_up.is_set()

    def start(self, files: Any, every_s: float = 60.0, journals: Any = None, live_s: float = 10.0) -> None:
        """Read the transcripts now, then every `every_s` seconds, on a daemon thread; D1085: the running
        agents' reported tokens from their journals every `live_s` seconds between."""
        if self._thread is not None:
            return

        def run() -> None:
            due = 0.0
            while not self._stop.is_set():
                if time.monotonic() >= due:
                    try:
                        self.ingest(files())
                    except Exception:  # noqa: BLE001 -- a read missed, the next one comes
                        pass
                    self._caught_up.set()
                    due = time.monotonic() + every_s
                if journals is not None:
                    try:
                        self.live(journals())
                    except Exception:  # noqa: BLE001
                        pass
                self._stop.wait(live_s if journals is not None else every_s)

        self._thread = threading.Thread(target=run, name="flux-usage", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    # ---- out
    def _rows(self, since: float, until: float) -> list[tuple]:
        with self._con() as c:
            return c.execute("SELECT t, span, user, app, kind, who, at, turns, fails, secs, tin, tout, cost, last, fail_t, fail_err, hist "
                             "FROM usage_turns WHERE t + span > ? AND t <= ?", (since, until)).fetchall()

    def usage_by_day(self, days: int, now: float) -> dict[str, Any]:
        """As Insights › Usage reads it (D920): turns, tokens and cost over the last `days` × 24 h, per
        user and per agent or model, an hour a bucket over one day, else a day; the costliest loops."""
        start = now - days * DAY
        hourly = days <= 1
        size = 3600.0 if hourly else float(DAY)
        n = int(round((now - start) / size))
        labels = [time.strftime("%H:%M" if hourly else "%m-%d", time.gmtime(start + (i + 1) * size)) for i in range(n)]
        blank = lambda: {"turns": [0] * n, "tokens": [0.0] * n, "cost": [0.0] * n}   # noqa: E731
        by_user: dict[str, dict] = {}
        by_who: dict[str, dict] = {}
        loops: dict[tuple[str, str], dict[str, float]] = {}
        for t, span, user, app, _kind, who, _at, turns, _f, secs, tin, tout, cost, last, *_rest in self._rows(start, now):
            at = min(max(t + span / 2, start), now)           # a bucket's middle: where its turns are counted
            d = min(n - 1, max(0, int((at - start) // size)))
            for into in (by_user.setdefault(user, blank()), by_who.setdefault(who, blank())):
                into["turns"][d] += turns
                into["tokens"][d] += tin + tout
                into["cost"][d] += cost
            lp = loops.setdefault((user, app), {"turns": 0, "tokens": 0.0, "cost": 0.0, "seconds": 0.0})
            lp["turns"] += turns
            lp["tokens"] += tin + tout
            lp["cost"] += cost
            lp["seconds"] += secs
        top = sorted(({"user": u, "app": a, **v} for (u, a), v in loops.items()), key=lambda x: (-x["cost"], -x["tokens"]))[:10]
        return {"days": labels, "bucket": "hour" if hourly else "day", "users": by_user, "agents": by_who, "top": top}

    def endpoints(self, since: float, until: float, forgot: dict[str, float] | None = None) -> list[dict[str, Any]]:
        """Each endpoint and agent as its turns since `since` found it (D850: after it was forgotten):
        turns, failures and their rate, the median and the slow 95th seconds, the last failure, when last used."""
        forgot = forgot or {}
        by: dict[tuple[str, str], dict] = {}
        for t, span, _u, _a, kind, _who, at, *rest in self._rows(since, until):
            if t + span <= forgot.get(f"{kind}|{at}", 0.0):
                continue
            r = _row(rest)
            by[(kind, at)] = _merge(by[(kind, at)], r) if (kind, at) in by else r
        out = []
        for (kind, at), r in by.items():
            out.append({"key": f"{kind}|{at}", "kind": kind, "where": at, "turns": r["turns"], "failed": r["fails"],
                        "rate": r["fails"] / r["turns"] if r["turns"] else 0.0, "p50": _quantile(r["hist"], 0.5),
                        "p95": _quantile(r["hist"], 0.95), "last": r["last"], "last_error": r["fail_err"] or "",
                        "last_error_at": r["fail_t"]})
        return sorted(out, key=lambda e: (-e["rate"], -e["turns"]))

    def token_rate(self, hours: float, now: float, points: int = 180) -> list[dict[str, Any]]:
        """Tokens per second over the last `hours`, in and out, the agents' and Flux's model's apart (D838):
        each stored minute (or hour) spread evenly over the chart's buckets it covers."""
        size = hours * 3600 / points
        t0 = now - hours * 3600
        out = [{"start_t": t0 + i * size, "t": t0 + (i + 1) * size,
                "in_agent": 0.0, "in_model": 0.0, "out_agent": 0.0, "out_model": 0.0} for i in range(points)]
        with self._con() as c:
            rows = c.execute("SELECT t, span, kind, SUM(tin), SUM(tout) FROM usage_rate WHERE t + span > ? AND t < ? GROUP BY t, span, kind",
                             (t0, now)).fetchall()
        for t, span, kind, tin, tout in rows:
            end = min(t + span, now)                          # the minute still running: its tokens so far, up to now
            lo, hi = max(t, t0), end
            if hi <= lo or end <= t:
                continue
            for i in range(max(0, int((lo - t0) // size)), min(points, int((hi - t0) // size) + 1)):
                a, b = max(lo, t0 + i * size), min(hi, t0 + (i + 1) * size)
                if b > a:
                    share = (b - a) / (end - t)
                    out[i][f"in_{kind}"] += tin * share / size
                    out[i][f"out_{kind}"] += tout * share / size
        return out


def _loop_add(loops: dict, doc: dict) -> None:
    """A transcript line into a loop's sums, as `usage.usage` counts it."""
    from .usage import _add, _empty, _num

    kind = str(doc.get("kind") or "turn")
    who = str(doc.get("agent") or doc.get("model") or kind)
    sums, first, last = loops.get((kind, who)) or (_empty(), None, None)
    _add(sums, doc)
    ts = _num(doc.get("ts")) or None
    if ts:
        first, last = min(first or ts, ts), max(last or ts, ts)
    loops[(kind, who)] = (sums, first, last)


def _put(c: sqlite3.Connection, key: tuple, r: dict) -> None:
    """A row summed into the one with its key."""
    old = c.execute("SELECT turns, fails, secs, tin, tout, cost, last, fail_t, fail_err, hist FROM usage_turns "
                    "WHERE t=? AND span=? AND path=? AND user=? AND app=? AND kind=? AND who=? AND at=?", key).fetchone()
    if old:
        r = _merge(r, _row(old))
    c.execute("INSERT OR REPLACE INTO usage_turns VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
              (*key, r["turns"], r["fails"], r["secs"], r["tin"], r["tout"], r["cost"], r["last"], r["fail_t"], r["fail_err"], json.dumps(r["hist"])))


def _row(r: tuple) -> dict:
    turns, fails, secs, tin, tout, cost, last, fail_t, fail_err, hist = r
    return {"turns": turns, "fails": fails, "secs": secs, "tin": tin, "tout": tout, "cost": cost, "last": last,
            "fail_t": fail_t, "fail_err": fail_err, "hist": {int(k): v for k, v in json.loads(hist or "{}").items()}}


def _merge(a: dict, b: dict) -> dict:
    out = {k: a[k] + b[k] for k in ("turns", "fails", "secs", "tin", "tout", "cost")}
    out["last"] = max(a["last"], b["last"])
    fa, fb = a.get("fail_t"), b.get("fail_t")
    pick = a if fb is None or (fa is not None and fa >= fb) else b
    out["fail_t"], out["fail_err"] = pick.get("fail_t"), pick.get("fail_err")
    hist = dict(a["hist"])
    for k, v in b["hist"].items():
        hist[k] = hist.get(k, 0) + v
    out["hist"] = hist
    return out
