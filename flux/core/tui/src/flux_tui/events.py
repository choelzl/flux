"""The event bus between a running loop and the TUI: thread-safe, bounded, lossy-late.

The loop side calls `log/task/measure/result` from its worker thread; the curses side
reads the ring buffers at frame time. Buffers are bounded deques so a chatty loop can
never grow memory or stall -- the TUI shows the tail, the full record stays wherever
the loop already writes it (stdout capture, provenance JSON, the campaign store). The
bus never blocks the loop: rendering is the TUI's problem (docs/decisions.md D390).
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any


@dataclass
class EventBus:
    max_lines: int = 2000
    max_rows: int = 400

    def __post_init__(self) -> None:
        self._lock = threading.Lock()
        self.log_lines: deque[str] = deque(maxlen=self.max_lines)
        # One dict per task (D391): a tool call, a stage headline, anything the loop
        # does that takes time or marks a boundary. Live rows have t1=None; the panel
        # computes their running duration each frame. Bounded like everything else.
        self.tasks: deque[dict[str, Any]] = deque(maxlen=300)
        self._next_task_id = 0
        self.measurements: deque[dict[str, Any]] = deque(maxlen=self.max_rows)
        self.results: deque[str] = deque(maxlen=self.max_rows)
        self.started_at = time.time()
        # The elapsed CLOCK counts active run time only: banked at done(), resumed by
        # restart() -- idle time spent reading results between runs is not run time
        # (measured complaint: r after a long look added the whole gap). Task rows'
        # "when" stays relative to started_at, the session timeline.
        self.run_started_at = self.started_at
        self.active_s = 0.0
        self.finished = False
        self.finished_at: float | None = None
        self.error: str | None = None

    # ---- loop side (any thread) ----
    def log(self, line: str) -> None:
        with self._lock:
            for part in str(line).rstrip("\n").split("\n"):
                self.log_lines.append(part)

    def task_start(self, name: str, *, kind: str = "tool", why: str = "",
                   params: dict[str, Any] | None = None) -> int:
        """Open a task row (one tool call = one task). Returns the id for task_end.
        Long parameter values (an LLM prompt, a command line) are kept -- the task
        panel exists to show them -- but bounded, so a pathological caller cannot
        grow the ring buffer's memory through one row."""
        clean = {k: (v if not isinstance(v, str) or len(v) <= 4000
                     else v[:4000] + f"…(+{len(v) - 4000} chars)")
                 for k, v in (params or {}).items()}
        with self._lock:
            tid = self._next_task_id
            self._next_task_id += 1
            self.tasks.append({"id": tid, "t0": time.time(), "t1": None,
                               "name": str(name), "kind": kind, "why": str(why),
                               "params": clean, "ok": None, "note": ""})
            return tid

    def task_end(self, task_id: int, *, ok: bool = True, note: str = "") -> None:
        with self._lock:
            for row in reversed(self.tasks):
                if row["id"] == task_id:
                    row["t1"] = time.time()
                    row["ok"] = bool(ok)
                    row["note"] = str(note)
                    break

    def task(self, name: str, *, why: str = "") -> None:
        """A stage headline: an instantaneous marker (kept for existing callers)."""
        with self._lock:
            tid = self._next_task_id
            self._next_task_id += 1
            now = time.time()
            self.tasks.append({"id": tid, "t0": now, "t1": now, "name": str(name),
                               "kind": "mark", "why": str(why), "params": {},
                               "ok": True, "note": ""})
            self.log_lines.append(f"[task] {name}")

    def measure(self, row: dict[str, Any]) -> None:
        with self._lock:
            self.measurements.append(dict(row))

    def result(self, line: str) -> None:
        with self._lock:
            self.results.append(str(line))

    def done(self, error: str | None = None) -> None:
        with self._lock:
            if self.finished:
                return
            self.finished = True
            self.finished_at = time.time()   # the clock STOPS here; done means done
            self.active_s += self.finished_at - self.run_started_at
            self.error = error

    def restart(self, run_no: int) -> None:
        """Arm the bus for another pass of the loop (the TUI's rerun key): history --
        log, tasks, results, measurements -- is kept, because the whole point of
        rerunning in place is comparing against what the last pass showed."""
        with self._lock:
            self.finished = False
            self.finished_at = None
            self.run_started_at = time.time()   # the clock resumes; idle not counted
            self.error = None
            now = time.time()
            self.tasks.append({"id": self._next_task_id, "t0": now, "t1": now,
                               "name": f"rerun #{run_no}", "kind": "mark",
                               "why": "operator pressed r", "params": {},
                               "ok": True, "note": ""})
            self._next_task_id += 1
            self.log_lines.append(f"[tui] rerun #{run_no} started")

    # ---- TUI side (main thread) ----
    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "log": list(self.log_lines),
                "tasks": [dict(t) for t in self.tasks],
                "measurements": list(self.measurements),
                "results": list(self.results),
                "started_at": self.started_at,
                "elapsed_s": self.active_s + (0.0 if self.finished
                                              else time.time() - self.run_started_at),
                "finished": self.finished,
                "finished_at": self.finished_at,
                "error": self.error,
            }


class BusWriter:
    """A file-like that feeds print() output into the bus, so a loop's existing
    reporting lands in the log panel without the loop changing a line.

    `fd` backs `fileno()`: Ray (and anything else doing fd-level redirection) asks
    the replacement stdout for its file descriptor, and a writer without one crashes
    the run (measured, D390). The TUI passes the write end of a drained pipe, so even
    raw fd writes land in the log panel instead of on the curses screen."""

    def __init__(self, bus: EventBus, fd: int | None = None) -> None:
        self._bus = bus
        self._buf = ""
        self._fd = fd

    def write(self, s: str) -> int:
        self._buf += s
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            self._bus.log(line)
        return len(s)

    def flush(self) -> None:
        if self._buf:
            self._bus.log(self._buf)
            self._buf = ""

    def isatty(self) -> bool:
        return False

    def fileno(self) -> int:
        if self._fd is None:
            import io

            raise io.UnsupportedOperation("BusWriter has no backing fd")
        return self._fd
