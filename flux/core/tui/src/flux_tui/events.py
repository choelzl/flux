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


def _bounded(values: dict[str, Any] | None, cap: int) -> dict[str, Any]:
    """Long text values (an LLM prompt or reply, a command line) are kept -- the task
    panel exists to show them -- but bounded, so a pathological caller cannot grow the
    ring buffer's memory through one row."""
    return {k: (v if not isinstance(v, str) or len(v) <= cap
                else v[:cap] + f"…(+{len(v) - cap} chars)")
            for k, v in (values or {}).items()}


@dataclass
class EventBus:
    max_lines: int = 2000
    max_rows: int = 400

    def __post_init__(self) -> None:
        self._lock = threading.Lock()
        self.log_lines: deque[str] = deque(maxlen=self.max_lines)
        self.log_stamps: deque[float] = deque(maxlen=self.max_lines)
        self.standings: dict[str, dict[str, Any]] = {}
        # One dict per task (D391): a tool call, a stage headline, anything the loop
        # does that takes time or marks a boundary. Live rows have t1=None; the panel
        # computes their running duration each frame. Bounded like everything else.
        self.tasks: list[dict[str, Any]] = []      # pruned structurally, see _prune (D487)
        self.max_tasks = 800
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
            # stamped with the ACTIVE run time it arrived (D418l), so the log and the
            # timing tab read on one clock
            stamp = self.active_s + (0.0 if self.finished else time.time() - self.run_started_at)
            for part in str(line).rstrip("\n").split("\n"):
                self.log_lines.append(part)
                self.log_stamps.append(stamp)

    def standing(self, key: str, payload: dict[str, Any]) -> None:
        """The loop's live standings: the latest payload per key (results tab)."""
        with self._lock:
            self.standings[key] = dict(payload)

    def task_start(self, name: str, *, kind: str = "tool", why: str = "",
                   params: dict[str, Any] | None = None) -> int:
        """Open a task row (one tool call = one task). Returns the id for task_end.
        Long parameter values (an LLM prompt, a command line) are kept -- the task
        panel exists to show them -- but bounded, so a pathological caller cannot
        grow the ring buffer's memory through one row."""
        clean = _bounded(params, 4000)
        with self._lock:
            tid = self._next_task_id
            self._next_task_id += 1
            open_ = [t for t in self.tasks if t["t1"] is None and t["kind"] != "mark"]
            depth = len(open_)
            parent = open_[-1]["id"] if open_ else None      # the innermost running task
            self.tasks.append({"id": tid, "t0": time.time(), "t1": None,
                               "name": str(name), "kind": kind, "why": str(why),
                               "params": clean, "output": {}, "ok": None, "note": "",
                               "depth": depth, "parent": parent, "pruned": 0})
            self._prune()
            return tid

    def _prune(self) -> None:
        """Keep the buffer under `max_tasks` by removing the OLDEST FINISHED LEAVES first --
        never a task that still has children in the buffer, never a running one -- and
        leave a count on the parent so the tree shows "… N earlier" where they were (D487,
        Cedric: "keep the tree somehow ... prune the oldest children that exited already").
        Neighbouring pruned leaves under one parent are one gap, so one ellipsis."""
        while len(self.tasks) > self.max_tasks:
            parents = {t["parent"] for t in self.tasks if t.get("parent") is not None}
            victim = next((t for t in self.tasks
                           if t["t1"] is not None and t["id"] not in parents
                           and t["kind"] != "mark"), None)
            if victim is None:
                victim = next((t for t in self.tasks if t["t1"] is not None), None)
            if victim is None:
                return
            self.tasks.remove(victim)
            for t in self.tasks:
                if t["id"] == victim.get("parent"):
                    t["pruned"] = int(t.get("pruned", 0)) + 1 + int(victim.get("pruned", 0))
                    break

    def task_end(self, task_id: int, *, ok: bool = True, note: str = "",
                 output: dict[str, Any] | None = None) -> None:
        """Close a task row. `output` is what the call PRODUCED (a model's reply, a
        verdict, a build error) -- shown under the parameters in the details pane
        (D470); a reply is longer than a prompt's worth of window, so its bound is
        wider than a parameter's."""
        clean = _bounded(output, 12000)
        with self._lock:
            for row in reversed(self.tasks):
                if row["id"] == task_id:
                    row["t1"] = time.time()
                    row["ok"] = bool(ok)
                    row["note"] = str(note)
                    if clean:
                        row["output"] = clean
                    break

    def task_update(self, task_id: int, output: dict[str, Any] | None) -> None:
        """What a RUNNING task has produced so far (D493): a streaming model's thinking as
        it arrives. Merged under the row's `output`; the close replaces it whole."""
        clean = _bounded(output, 12000)
        if not clean:
            return
        with self._lock:
            for row in reversed(self.tasks):
                if row["id"] == task_id:
                    if row["t1"] is None:
                        row["output"] = {**(row.get("output") or {}), **clean}
                    break

    def task(self, name: str, *, why: str = "") -> None:
        """A stage headline: an instantaneous marker (kept for existing callers)."""
        with self._lock:
            tid = self._next_task_id
            self._next_task_id += 1
            now = time.time()
            open_ = [t for t in self.tasks if t["t1"] is None and t["kind"] != "mark"]
            self.tasks.append({"id": tid, "t0": now, "t1": now, "name": str(name),
                               "kind": "mark", "why": str(why), "params": {},
                               "parent": open_[-1]["id"] if open_ else None, "pruned": 0,
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
            self.log_stamps.append(self.active_s)

    # ---- TUI side (main thread) ----
    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "log": list(self.log_lines),
                "log_stamps": list(self.log_stamps),
                "standings": {k: dict(v) for k, v in self.standings.items()},
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
