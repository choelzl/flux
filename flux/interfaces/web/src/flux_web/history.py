"""What the machine held up over time (D699): a sample a minute -- load, memory, each disk's use,
the loops running, the sandbox containers' CPU and memory -- kept a week in the server's
database, read back thinned to a few hundred points for the admin's charts. `flux serve` starts
the sampler; nothing samples in a test unless it asks."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable

__all__ = ["History"]

KEEP_S = 7 * 86400


class History:
    def __init__(self, db: str | Path) -> None:
        self.db = str(db)
        with self._con() as c:
            c.execute("CREATE TABLE IF NOT EXISTS samples (t REAL NOT NULL, data TEXT NOT NULL)")
            c.execute("CREATE INDEX IF NOT EXISTS samples_t ON samples (t)")
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _con(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db, timeout=10)

    def add(self, sample: dict[str, Any], t: float | None = None) -> None:
        t = time.time() if t is None else t
        with self._con() as c:
            c.execute("INSERT INTO samples VALUES (?, ?)", (t, json.dumps(sample)))
            c.execute("DELETE FROM samples WHERE t < ?", (time.time() - KEEP_S,))     # a week back from now

    def read(self, hours: float = 24, points: int = 360) -> list[dict[str, Any]]:
        """The samples of the last `hours`, at most `points`: each bucket's mean, and its maximum
        of the load and CPU, so a burst stays visible."""
        since = time.time() - hours * 3600
        with self._con() as c:
            rows = c.execute("SELECT t, data FROM samples WHERE t >= ? ORDER BY t", (since,)).fetchall()
        got = [{"t": t, **json.loads(d)} for t, d in rows]
        if len(got) <= points:
            return got
        size = len(got) / points
        out = []
        for i in range(points):
            chunk = got[int(i * size):int((i + 1) * size)] or [got[min(len(got) - 1, int(i * size))]]
            m: dict[str, Any] = {"t": chunk[-1]["t"]}
            for k, v in chunk[-1].items():
                if k == "t":
                    continue
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    vals = [c[k] for c in chunk if isinstance(c.get(k), (int, float))]
                    m[k] = max(vals) if k in ("load1", "cpu") else sum(vals) / len(vals)
                elif isinstance(v, dict):
                    m[k] = {kk: sum(c.get(k, {}).get(kk, 0) for c in chunk) / len(chunk) for kk in v}
                else:
                    m[k] = v
            out.append(m)
        return out

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
