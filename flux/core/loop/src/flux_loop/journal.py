"""The run's journal (D683): every phase event of the live task tree, appended as JSON lines to
`<run dir>/events.jsonl`, so another process -- `flux serve` -- can follow a run as the TUI does.

One line per event: `{"t", "ev": start|update|end|mark|publish, "id", "parent", "name", ...}`.
A phase's `id` is its order of start in this process; `parent` is the phase open around it on
the same thread, or the phase a worker thread was handed its work under (D739); null at the top. Updates are at most one a second per
phase; every text value is cut to its last TAIL characters. It never fails the run: a write
that fails is dropped.
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Any

__all__ = ["Journal", "TAIL", "attach", "read_events", "window_start"]

TAIL = 4000
_ATTACHED: dict[str, "Journal"] = {}
_ATTACHING = threading.Lock()


def _cut(value: Any) -> Any:
    if isinstance(value, str):
        return value if len(value) <= TAIL else "..." + value[-TAIL:]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(k): _cut(v) for k, v in list(value.items())[:60]}
    if isinstance(value, (list, tuple)):
        return [_cut(v) for v in list(value)[:60]]
    return _cut(str(value))


class Journal:
    def __init__(self, path: str) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._n = 0
        self._stacks: dict[int, list[int]] = {}
        self._last_update: dict[int, float] = {}
        self._pending: dict[int, tuple[str, dict]] = {}
        self._adopted: dict[int, int] = {}     # a worker thread -> the phase it works under (D739)

    def _write(self, row: dict[str, Any]) -> None:
        row = {"t": round(time.time(), 3), **row}
        try:
            line = json.dumps(row, default=str)
            with self._lock, open(self.path, "a") as fh:
                fh.write(line + "\n")
        except (OSError, TypeError, ValueError):
            pass

    def phase_start(self, name: str, why: str, params: dict) -> int:
        with self._lock:
            self._n += 1
            pid = self._n
            stack = self._stacks.setdefault(threading.get_ident(), [])
            parent = stack[-1] if stack else self._adopted.get(threading.get_ident())
            stack.append(pid)
        self._write({"ev": "start", "id": pid, "parent": parent, "name": name, "why": _cut(why),
                     "params": _cut(params or {})})
        return pid

    def phase_update(self, token: int, name: str, output: dict) -> None:
        now = time.monotonic()
        if now - self._last_update.get(token, 0.0) < 1.0:
            self._pending[token] = (name, output)          # the newest waits for the next second or the end
            return
        self._last_update[token] = now
        self._pending.pop(token, None)
        self._write({"ev": "update", "id": token, "name": name, "fields": _cut(output)})

    def phase_end(self, token: int, name: str, seconds: float, failed: bool, output: dict) -> None:
        with self._lock:
            stack = self._stacks.get(threading.get_ident(), [])
            if stack and stack[-1] == token:
                stack.pop()
        self._pending.pop(token, None)
        self._last_update.pop(token, None)
        self._write({"ev": "end", "id": token, "name": name, "seconds": round(seconds, 3), "failed": bool(failed),
                     "output": _cut(output or {})})

    def adopt(self, token: int | None) -> None:
        """This thread's phases go under `token` (flux_profile.carried), or nowhere again."""
        with self._lock:
            if token is None:
                self._adopted.pop(threading.get_ident(), None)
            else:
                self._adopted[threading.get_ident()] = token

    def mark(self, name: str, why: str) -> None:
        self._write({"ev": "mark", "name": name, "why": _cut(why)})

    def publish(self, key: str, payload: dict) -> None:
        self._write({"ev": "publish", "key": key, "payload": _cut(payload)})


def attach(run_dir: str) -> Journal:
    """This process's journal into `run_dir` (once per directory), beside any TUI listener."""
    from flux_profile import add_listener, remove_listener

    with _ATTACHING:                                 # D747: passes starting at once attach one journal
        j = _ATTACHED.get(run_dir)
        if j is None:
            for old in list(_ATTACHED):              # one run per process: a new one replaces it
                remove_listener(_ATTACHED.pop(old))
            os.makedirs(run_dir, exist_ok=True)
            j = _ATTACHED[run_dir] = Journal(os.path.join(run_dir, "events.jsonl"))
            j._write({"ev": "hello", "pid": os.getpid()})
            add_listener(j)
    return j


def read_events(path: str, offset: int = 0, limit: int | None = None) -> tuple[list[dict[str, Any]], int]:
    """The events from byte `offset` on (at most `limit` bytes of them, D759: a day's journal is
    read in slices, not whole) and the offset after the last whole line (a follower calls again
    with it)."""
    try:
        with open(path, "rb") as fh:
            fh.seek(offset)
            data = fh.read(limit) if limit else fh.read()
    except OSError:
        return [], offset
    end = data.rfind(b"\n")
    if end < 0:
        return [], offset
    out = []
    for line in data[:end].splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out, offset + end + 1



def window_start(path: str, passes: int) -> tuple[int, int] | None:
    """Where the latest start's last `passes` passes begin, read from the end backwards (D759): a
    day-long journal is never read whole to open its latest passes. (byte offset, how many passes of
    this start came before), or None when the start holds no more than that."""
    PASS, HELLO = b'"ev": "mark", "name": "pass"', b'"ev": "hello"'
    try:
        size = os.path.getsize(path)
        fh = open(path, "rb")
    except OSError:
        return None
    with fh:
        found: list[int] = []                                  # line starts of pass marks, newest first
        end, carry = size, b""
        while end > 0:
            begin = max(0, end - (4 << 20))
            fh.seek(begin)
            block = fh.read(end - begin) + carry
            cut = block.find(b"\n") + 1 if begin > 0 else 0     # a partial first line waits for the next block
            carry, body = block[:cut], block[cut:]
            base = begin + cut
            hello = body.rfind(HELLO)
            marks = []
            i = body.find(PASS)
            while i >= 0:
                if hello < 0 or i > hello:
                    marks.append(base + body.rfind(b"\n", 0, i) + 1)
                i = body.find(PASS, i + len(PASS))
            found.extend(reversed(marks))
            if len(found) > passes:
                at = found[passes - 1]
                fh.seek(at)
                try:
                    n = int(json.loads(json.loads(fh.readline()).get("why") or "{}").get("n") or 0)
                except (ValueError, AttributeError):
                    n = 0
                return at, max(0, n - 1)
            if hello >= 0:
                return None                                    # the start is shorter than the window
            end = begin
        return None
