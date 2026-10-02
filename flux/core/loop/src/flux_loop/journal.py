"""The run's journal (D683): the live task tree, so another process -- `flux serve` -- can follow a
run as the TUI does. Two files in the run's directory (D761):

- `events.jsonl`, appended: `{"t", "ev": hello|start|end|mark, "id", "parent", "name", ...}`. A
  phase's `id` is its order of start in this process; `parent` is the phase open around it on the
  same thread, or the phase a worker thread was handed its work under (D739); null at the top. An
  end's output is cut to END_MAX.
- `live.json`, rewritten at most once a second: each running phase's latest fields, and the
  standings -- what is only ever read at its latest, never appended.
- `marks.jsonl`, appended (D762): where in `events.jsonl` each start and each pass begins, so a
  page opens a journal of gigabytes on its last passes without reading it backwards.

Every text value is cut to its last TAIL characters. It never fails the run: a write that fails
is dropped."""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Any

__all__ = ["Journal", "TAIL", "attach", "compact", "read_events", "window_start"]

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


#: An end's output at most this long as JSON (D761): a turn's whole conversation is the turn log's.
END_MAX = 48_000


def _capped(output: dict[str, Any]) -> dict[str, Any]:
    """An end's output within END_MAX: its strings and lists cut harder until it fits."""
    out = _cut(output or {})
    for chars, items in ((1500, 20), (400, 8), (120, 3)):
        if len(json.dumps(out, default=str)) <= END_MAX:
            break
        out = _shrunk(out, chars, items)
    return out


def _shrunk(value: Any, chars: int, items: int) -> Any:
    if isinstance(value, str):
        return value if len(value) <= chars else "..." + value[-chars:]
    if isinstance(value, dict):
        return {k: _shrunk(v, chars, items) for k, v in value.items()}
    if isinstance(value, list):
        return [_shrunk(v, chars, items) for v in value[-items:]]
    return value


class Journal:
    """D761: two files. `events.jsonl` is the run's structure, appended: starts, ends, marks. What
    is only ever read at its latest -- a running phase's live fields, the standings -- is not
    appended a second at a time (an hour-long agent turn made tens of MB of it): it is kept in
    `live.json`, rewritten at most once a second."""

    def __init__(self, path: str) -> None:
        self.path = path
        self.live_path = os.path.join(os.path.dirname(path), "live.json")
        self.marks_path = os.path.join(os.path.dirname(path), "marks.jsonl")
        self._lock = threading.Lock()
        self._n = 0
        self._stacks: dict[int, list[int]] = {}
        self._adopted: dict[int, int] = {}     # a worker thread -> the phase it works under (D739)
        self._live: dict[str, Any] = {"updates": {}, "publish": {}}
        self._dirty = False
        self._written = 0.0
        self._flusher: threading.Thread | None = None

    def _touch_live(self) -> None:
        """The live state changed: written now if a second has passed, else by the flusher."""
        self._dirty = True
        if time.monotonic() - self._written >= 1.0:
            self._write_live()
        elif self._flusher is None:
            self._flusher = threading.Thread(target=self._flush_soon, daemon=True, name="flux-journal-live")
            self._flusher.start()

    def _flush_soon(self) -> None:
        time.sleep(1.0)
        self._flusher = None
        if self._dirty:
            self._write_live()

    def _write_live(self) -> None:
        with self._lock:
            doc = {"t": round(time.time(), 3), "updates": dict(self._live["updates"]), "publish": dict(self._live["publish"])}
            self._dirty, self._written = False, time.monotonic()
        tmp = f"{self.live_path}.{os.getpid()}.{threading.get_ident()}"
        try:
            with open(tmp, "w") as fh:
                json.dump(doc, fh, default=str)
            os.replace(tmp, self.live_path)
        except (OSError, TypeError, ValueError):
            try:
                os.unlink(tmp)
            except OSError:
                pass

    def _write(self, row: dict[str, Any]) -> None:
        row = {"t": round(time.time(), 3), **row}
        try:
            line = json.dumps(row, default=str)
            with self._lock, open(self.path, "a") as fh:
                at = fh.tell()
                fh.write(line + "\n")
                if row["ev"] == "hello" or (row["ev"] == "mark" and row.get("name") == "pass"):
                    with open(self.marks_path, "a") as mf:
                        mf.write(json.dumps({"at": at, "ev": "hello" if row["ev"] == "hello" else "pass", "n": _n_of(row)}) + "\n")
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
        with self._lock:
            self._live["updates"][str(token)] = _cut(output)
        self._touch_live()

    def phase_end(self, token: int, name: str, seconds: float, failed: bool, output: dict) -> None:
        with self._lock:
            stack = self._stacks.get(threading.get_ident(), [])
            if stack and stack[-1] == token:
                stack.pop()
        with self._lock:
            had = self._live["updates"].pop(str(token), None) is not None
        self._write({"ev": "end", "id": token, "name": name, "seconds": round(seconds, 3), "failed": bool(failed),
                     "output": _capped(output or {})})
        if had:
            self._touch_live()

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
        with self._lock:
            self._live["publish"][key] = _cut(payload)
        self._touch_live()


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
            j._write_live()                              # D761: a new start's live state begins empty
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



def compact(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """D762: a slice of the journal as the tree needs it -- a phase's updates (a run started
    before D761 wrote every streamed field into the journal: gigabytes over an hour's pass)
    merged into one, none for a phase that ended in it; a publish only at its latest."""
    ended = {e.get("id") for e in events if e.get("ev") == "end"}
    last_upd: dict[Any, int] = {}
    last_pub: dict[Any, int] = {}
    merged: dict[Any, dict] = {}
    for i, e in enumerate(events):
        if e.get("ev") == "update" and e.get("id") not in ended:
            merged.setdefault(e.get("id"), {}).update(e.get("fields") or {})
            last_upd[e.get("id")] = i
        elif e.get("ev") == "publish":
            last_pub[e.get("key")] = i
    out = []
    for i, e in enumerate(events):
        ev = e.get("ev")
        if ev == "update":
            if last_upd.get(e.get("id")) == i:
                out.append({**e, "fields": merged[e.get("id")]})
        elif ev == "publish":
            if last_pub.get(e.get("key")) == i:
                out.append(e)
        else:
            out.append(e)
    return out


def _n_of(row: dict[str, Any]) -> int:
    try:
        return int(json.loads(row.get("why") or "{}").get("n") or 0)
    except (ValueError, AttributeError, TypeError):
        return 0


def _choose(size: int, hello_at: int, marks: list[tuple[int, int]], passes: int, budget: int) -> tuple[int, int, int | None] | None:
    """From the start's pass marks (offset, n), oldest first: the last `passes` passes, the oldest
    of them whose whole is within `budget`; else the newest pass's last `budget` bytes, its mark."""
    if size - hello_at <= budget and len(marks) <= passes:
        return None
    whole = [m for m in marks[-passes:] if size - m[0] <= budget]
    if whole:
        return whole[0][0], max(0, whole[0][1] - 1), None
    return size - budget, max(0, marks[-1][1] - 1) if marks else 0, marks[-1][0] if marks else -1


def _marks_of(path: str, size: int) -> tuple[int, list[tuple[int, int]]] | None:
    """The latest start's offset and its passes from `marks.jsonl` (D762), None without one to trust."""
    try:
        with open(os.path.join(os.path.dirname(path), "marks.jsonl"), "rb") as fh:
            rows = [json.loads(x) for x in fh.read().splitlines() if x.strip()]
    except (OSError, ValueError):
        return None
    hello = max((i for i, r in enumerate(rows) if r.get("ev") == "hello"), default=-1)
    if hello < 0 or int(rows[-1].get("at", size + 1)) > size:
        return None                                           # another file's, or cut
    return int(rows[hello]["at"]), [(int(r["at"]), int(r.get("n") or 0)) for r in rows[hello + 1:] if r.get("ev") == "pass"]


def window_start(path: str, passes: int, budget: int = 24 << 20, reach: int = 64 << 20) -> tuple[int, int, int | None] | None:
    """Where the page's first look at the latest start begins (D759): its last `passes` passes, and
    never more than `budget` bytes (D762: a start of a few passes of hours each is gigabytes too).
    (byte offset of a whole line, how many passes of this start came before, and -- when the window
    begins inside a pass -- that pass's mark, said first, -1 when not found), or None when the whole start is within
    both. From `marks.jsonl`; a journal without one is read backwards, at most `reach` bytes past
    the budget (beyond, its pass is not named)."""
    PASS, HELLO = b'"ev": "mark", "name": "pass"', b'"ev": "hello"'
    try:
        size = os.path.getsize(path)
        fh = open(path, "rb")
    except OSError:
        return None
    with fh:
        known = _marks_of(path, size)
        if known is not None:
            got = _choose(size, known[0], known[1], passes, budget)
        else:
            found: list[tuple[int, int]] = []                  # (line start, n) of pass marks, newest first
            end, carry, got, hello_at = size, b"", None, None
            while end > 0 and size - end <= budget + reach:
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
                        at = base + body.rfind(b"\n", 0, i) + 1
                        try:
                            n = _n_of(json.loads(body[at - base:body.find(b"\n", i) % (len(body) + 1)]))
                        except ValueError:
                            n = 0                              # the line being written
                        marks.append((at, n))
                    i = body.find(PASS, i + len(PASS))
                found.extend(reversed(marks))
                if hello >= 0:
                    hello_at = base + body.rfind(b"\n", 0, hello) + 1
                    break
                if len(found) > passes or (found and size - base > budget):
                    break
                end = begin
            if hello_at is None and not found and size <= budget:
                hello_at = 0
            if hello_at is None and not (len(found) > passes or found and found[-1][0] < size - budget):
                got = (size - budget, 0, -1)                   # beyond reach: the tail, its pass unnamed
            else:
                got = _choose(size, hello_at or 0, list(reversed(found)), passes, budget)
        if got is None:
            return None
        at = got[0]
        if got[2] is not None:
            fh.seek(max(0, at - 1))                            # to the next whole line
            if at > 0:
                fh.readline()
            at = fh.tell()
        return at, got[1], got[2]
