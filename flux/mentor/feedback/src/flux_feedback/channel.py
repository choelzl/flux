"""The while-running feedback channel: lines the operator types, drained at round boundaries.

A daemon thread reads stdin so a person can steer a long run without killing it. Notes are
advisory guidance, their own provenance class (D388); `render_guidance` is the one place that
label is written, so no note reaches a prompt unlabelled.

Inert without a terminal (CI, a pipe, redirection): `active` is False, `start()` does nothing
and every `drain()` is empty, so a scripted run is byte-identical with or without it.
"""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Sequence, TextIO

__all__ = ["FeedbackChannel", "InboxChannel", "Joined", "Note", "drain_guidance", "reload_notes", "render_guidance",
           "scripted_channel"]

_LABEL = (
    "HUMAN GUIDANCE (typed by the operator during this run -- advisory directions, not "
    "measurements; every candidate still passes the same gates):")

_HINT = (
    "feedback: type a line + Enter at any time; it reaches the model's next proposal prompt "
    "(advisory -- every candidate still passes the same gates)")


@dataclass(frozen=True)
class Note:
    """One line the operator typed, with when and in which run."""

    text: str
    received_at: float
    origin: str = "this-run"        # or "earlier-run", for notes reloaded from the record


class FeedbackChannel:
    """Collects operator lines in the background; the loop drains them when it can act on them.

    `stream` is injectable so tests feed a fake terminal; the default is the process's stdin.
    Acknowledgements go through `say` immediately from the reader thread, so a note never looks
    dropped during a long evaluation.
    """

    def __init__(self, stream: TextIO | None = None, *,
                 say: Callable[[str], None] = print) -> None:
        if stream is None:
            import sys

            stream = sys.stdin
        self._stream = stream
        self._say = say
        self._queue: queue.SimpleQueue[Note] = queue.SimpleQueue()
        self._thread: threading.Thread | None = None
        self._stopped = False
        try:
            self.active = bool(stream.isatty())
        except Exception:                                                 # noqa: BLE001
            self.active = False

    def start(self) -> None:
        """Print the hint and begin reading; a no-op when there is no terminal."""
        if not self.active or self._thread is not None:
            return
        self._say(_HINT)
        self._thread = threading.Thread(target=self._read, name="flux-feedback", daemon=True)
        self._thread.start()

    def _read(self) -> None:
        while not self._stopped:
            line = self._stream.readline()
            if not line:                       # EOF: the terminal went away
                return
            text = line.strip()
            if not text or self._stopped:
                continue
            self._queue.put(Note(text=text, received_at=time.time()))
            self._say(f'feedback noted: "{text}" -- it reaches the next proposal prompt')

    def drain(self) -> list[Note]:
        """Every note received since the last drain, oldest first. Main-thread only."""
        notes: list[Note] = []
        while True:
            try:
                notes.append(self._queue.get_nowait())
            except queue.Empty:
                return notes

    def close(self) -> None:
        """Stop accepting notes. The daemon thread dies with the process; this only makes the
        cutoff explicit so a note typed during report printing is not half-acknowledged."""
        self._stopped = True


class InboxChannel:
    """Notes appended to a JSON-lines file by another process -- `flux serve`'s page (D684):
    `{"text": ..., "by": ...}` per line. `drain()` returns the lines added since the last drain;
    a line still being written waits for the next."""

    def __init__(self, path: str, say: Callable[[str], None] = print) -> None:
        self.path, self._say, self._offset = path, say, 0
        self.active = True
        try:
            import os

            self._offset = os.path.getsize(path)          # what was there before this run is not this run's
        except OSError:
            pass

    def start(self) -> None:
        self._say(f"feedback: notes and answers also arrive from {self.path}")

    def drain(self) -> list[Note]:
        import json

        try:
            with open(self.path, "rb") as fh:
                fh.seek(self._offset)
                data = fh.read()
        except OSError:
            return []
        end = data.rfind(b"\n")
        if end < 0:
            return []
        self._offset += end + 1
        docs = []
        for line in data[:end].splitlines():
            try:
                doc = json.loads(line)
            except ValueError:
                continue
            if isinstance(doc, dict):
                docs.append(doc)
        # D808: a note the page removed before this read is not taken (the file only grows: a
        # removal is a line of its own, so a reader's place in it never moves)
        gone = {str(d["forget"]) for d in docs if d.get("forget") is not None}
        notes = []
        for doc in docs:
            if str(doc.get("id") or doc.get("t")) in gone:
                continue
            text = str(doc.get("text") or "").strip()
            if text:
                by = str(doc.get("by") or "").strip()
                notes.append(Note(text=text, received_at=float(doc.get("t") or time.time())))
                self._say(f'feedback noted{" from " + by if by else ""}: "{text[:200]}" -- it reaches the next proposal prompt')
        return notes

    def close(self) -> None:
        pass


class Joined:
    """Several channels drained as one (the terminal and the inbox)."""

    def __init__(self, channels: list) -> None:
        self.channels = [c for c in channels if c is not None]
        self.active = any(getattr(c, "active", True) for c in self.channels)

    def start(self) -> None:
        for c in self.channels:
            c.start()

    def drain(self) -> list[Note]:
        return sorted((n for c in self.channels for n in c.drain()), key=lambda n: n.received_at)

    def close(self) -> None:
        for c in self.channels:
            c.close()


def scripted_channel(*texts: str):
    """A channel preloaded with notes, satisfying the `drain()` contract: the reference fake (D404)."""

    class _Scripted:
        def __init__(self) -> None:
            self._pending = [Note(text=t, received_at=time.time()) for t in texts]

        def drain(self) -> list[Note]:
            out, self._pending = self._pending, []
            return out

    return _Scripted()


def drain_guidance(channel, accumulated: list[Note], *,
                   on_note: Callable[[Note], None] | None = None) -> str | None:
    """Drain fresh notes, hand each to `on_note` (persist, echo), fold them into `notes`, and
    return the labelled prompt block over all notes so far, or None when there are none.
    `on_note` failures are swallowed: acknowledgement must not kill the run."""
    fresh = channel.drain() if channel is not None else []
    for n in fresh:
        if on_note is not None:
            try:
                on_note(n)
            except Exception:                                             # noqa: BLE001
                pass
    accumulated.extend(fresh)
    return render_guidance(accumulated) or None


def reload_notes(records, say: Callable[[str], None] = lambda _m: None) -> list[Note]:
    """A resumed campaign's earlier operator notes, as `earlier-run` Notes (D403).

    What the operator said last run still stands. Seed a run's notes list with this; a fresh or
    record-less run, or any failure, yields []."""
    try:
        if records is None or not getattr(records, "resumed", False):
            return []
        earlier = [Note(text=t, received_at=0.0, origin="earlier-run")
                   for t in records.notes()]
        if earlier:
            say(f"resumed: {len(earlier)} operator note(s) from an earlier run rejoin "
                "the proposer prompt")
        return earlier
    except Exception:                                                     # noqa: BLE001
        return []


def render_guidance(notes: Sequence[Note], *, max_chars: int = 1200) -> str:
    """The prompt block: the label, then the notes, newest kept when they will not all fit.

    Empty input renders to "". Truncation is announced: the model is told notes were omitted.
    """
    if not notes:
        return ""
    lines = []
    for n in notes:
        stamp = ("earlier run" if n.origin == "earlier-run"
                 else time.strftime("%H:%M:%S", time.localtime(n.received_at)))
        lines.append(f"  * [{stamp}] {n.text}")
    kept: list[str] = []
    used = len(_LABEL)
    omitted = 0
    for line in reversed(lines):
        if used + len(line) + 1 > max_chars and kept:
            omitted = len(lines) - len(kept)
            break
        kept.append(line)
        used += len(line) + 1
    body = "\n".join(reversed(kept))
    header = _LABEL if not omitted else (
        f"{_LABEL}\n  ({omitted} earlier note(s) omitted to fit)")
    return f"{header}\n{body}"


def guidance_lesson(text: str, *, reaches: str | None) -> str:
    """The lesson line for one operator note: what was said and where it went (`reaches` names
    the prompt it joins; None: no model role in this run)."""
    tail = (f" -- it goes into {reaches}" if reaches
            else " -- no model role in this run; recorded and reported, it reached no prompt")
    return f"[human] operator guidance: {text!r}{tail}"


def note_sink(records: Any, acknowledge: Callable[[str], None] | None = None,
              ) -> Callable[[Note], None]:
    """The `on_note` for `drain_guidance` (D429): persist the note into the campaign record when
    there is one (a resume re-shows it, D403), then acknowledge it the run's own way."""
    def on_note(n: Note) -> None:
        if records is not None:
            records.note(n.text)
        if acknowledge is not None:
            acknowledge(n.text)

    return on_note
