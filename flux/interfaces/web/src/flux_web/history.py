"""Resource history: minute samples for a day, five-minute samples through day seven,
thirty-minute samples through day thirty. Preserve peaks, weighted means and outages
as samples age, and thin responses for the admin's charts. Only `flux serve` samples."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable

__all__ = ["History"]

DAY = 86400
KEEP_S = 30 * DAY
TIERS = ((7 * DAY, 1800), (DAY, 300))  # compact oldest first; the last day remains raw
COMPACT_S = 3600
GAP_S = 180


class History:
    def __init__(self, db: str | Path) -> None:
        self.db = str(db)
        with self._con() as c:
            c.execute("CREATE TABLE IF NOT EXISTS samples (t REAL NOT NULL, data TEXT NOT NULL)")
            c.execute("CREATE INDEX IF NOT EXISTS samples_t ON samples (t)")
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._next_compact = 0.0

    def _con(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db, timeout=10)

    def add(self, sample: dict[str, Any], t: float | None = None) -> None:
        now = time.time()
        t = now if t is None else t
        with self._lock:
            due = now >= self._next_compact
            with self._con() as c:
                c.execute("INSERT INTO samples VALUES (?, ?)", (t, json.dumps(sample)))
                c.execute("DELETE FROM samples WHERE t < ?", (now - KEEP_S,))
                if due:
                    self._compact(c, now)
            if due:
                self._next_compact = now + COMPACT_S

    def compact(self, now: float | None = None) -> None:
        """Roll up older data atomically. Existing minute-only databases need no migration.
        Deleted pages are reused; the existing database maintenance can reclaim free pages."""
        now = time.time() if now is None else now
        with self._lock:
            with self._con() as c:
                c.execute("DELETE FROM samples WHERE t < ?", (now - KEEP_S,))
                self._compact(c, now)
            self._next_compact = now + COMPACT_S

    def _compact(self, c: sqlite3.Connection, now: float) -> None:
        lower = now - KEEP_S
        for age, interval in TIERS:
            upper = now - age
            rows = c.execute("SELECT t, data FROM samples WHERE t >= ? AND t < ? ORDER BY t", (lower, upper)).fetchall()
            samples = self._samples(rows)
            groups: list[list[dict[str, Any]]] = []
            for sample in samples:
                bucket = int(sample["t"] // interval)
                if (not groups or sample.get("gap_before")
                        or int(groups[-1][-1]["t"] // interval) != bucket):
                    groups.append([])
                groups[-1].append(sample)
            offset = 0
            for group in groups:
                mean = dict(group[0]) if len(group) == 1 else self._mean(group)
                mean["start_t"] = group[0].get("start_t", group[0]["t"])
                mean["_count"] = sum(s.get("_count", 1) for s in group)
                compacted = (mean["t"], json.dumps({k: v for k, v in mean.items() if k != "t"}, sort_keys=True))
                original = rows[offset:offset + len(group)]
                offset += len(group)
                # Replace only changed buckets; newly aged samples need no rewrite of the tier.
                if original != [compacted]:
                    c.executemany("DELETE FROM samples WHERE t = ? AND data = ?", original)
                    c.execute("INSERT INTO samples VALUES (?, ?)", compacted)
            lower = upper

    @staticmethod
    def _samples(rows: list[tuple]) -> list[dict[str, Any]]:
        got = [{**json.loads(d), "t": t} for t, d in rows]
        for previous, current in zip(got, got[1:]):
            if current.get("start_t", current["t"]) - previous["t"] > GAP_S:
                current["gap_before"] = True
        return got

    def read(self, hours: float = 24, points: int = 360) -> list[dict[str, Any]]:
        """Thin the last `hours` to `points` buckets, splitting at sampling outages.
        Keep each bucket's mean and maximum load/CPU; outage splits may add points."""
        now = time.time()
        since = now - hours * 3600
        with self._con() as c:
            rows = c.execute("SELECT t, data FROM samples WHERE t >= ? AND t <= ? ORDER BY t", (since, now)).fetchall()
        got = self._samples(rows)
        if len(got) <= points:
            return [{k: v for k, v in s.items() if not k.startswith("_")} for s in got]
        size = len(got) / points
        out = []
        for i in range(points):
            chunk = got[int(i * size):int((i + 1) * size)] or [got[min(len(got) - 1, int(i * size))]]
            # Never average across an outage, even when it falls inside a thinning bucket.
            starts = [0] + [j for j in range(1, len(chunk)) if chunk[j].get("gap_before")]
            for start, end in zip(starts, starts[1:] + [len(chunk)]):
                out.append(self._mean(chunk[start:end]))
        return out

    @staticmethod
    def _mean(chunk: list[dict[str, Any]]) -> dict[str, Any]:
        m: dict[str, Any] = {"t": chunk[-1]["t"]}
        if chunk[0].get("gap_before"):
            m["gap_before"] = True
        if chunk[0].get("gap_before") or "start_t" in chunk[0]:
            m["start_t"] = chunk[0].get("start_t", chunk[0]["t"])
        for k, v in chunk[-1].items():
            if k in ("t", "gap_before", "start_t") or k.startswith("_"):
                continue
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                vals = [(c[k], c.get("_count", 1)) for c in chunk if isinstance(c.get(k), (int, float))]
                if k in ("load1", "cpu"):
                    m[k] = max(value for value, _ in vals)
                else:
                    m[k] = sum(value * weight for value, weight in vals) / sum(weight for _, weight in vals)
            elif isinstance(v, dict):
                m[k] = {}
                for kk in v:
                    vals = [(c[k][kk], c.get("_count", 1)) for c in chunk if isinstance(c.get(k), dict) and isinstance(c[k].get(kk), (int, float))]
                    if vals:
                        m[k][kk] = sum(value * weight for value, weight in vals) / sum(weight for _, weight in vals)
            else:
                m[k] = v
        return m

    def start(self, sample: Callable[[], dict[str, Any]], every_s: float = 60.0) -> None:
        """Sample now, then every `every_s` seconds, on a daemon thread; a failed sample is skipped."""
        if self._thread is not None:
            return

        def run() -> None:
            while not self._stop.is_set():
                try:
                    self.add(sample())
                except Exception:  # noqa: BLE001 -- a sample missed, the next one comes
                    pass
                self._stop.wait(every_s)

        self._thread = threading.Thread(target=run, name="flux-history", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
