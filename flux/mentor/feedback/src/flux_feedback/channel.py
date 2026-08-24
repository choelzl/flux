"""The while-running feedback channel: lines the operator types, drained at round boundaries.

The channel is a daemon thread reading the demo's own stdin. It exists so a person watching a
long run can say "prefer smaller tables" without killing the process -- and it is honest about
what that is: advisory guidance from a human, a fourth provenance class beside curated text,
measured facts and inferred conclusions (docs/decisions.md D388). `render_guidance` is the one
place that heading is written, so no consumer can splice a note into a prompt without the label.

Inert without a terminal, on purpose: under CI, a pipe, or redirection, `active` is False,
`start()` prints nothing and reads nothing, and every `drain()` is empty -- a scripted run is
byte-identical with or without the channel.
"""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass
from typing import Callable, Sequence, TextIO

__all__ = ["FeedbackChannel", "Note", "drain_guidance", "reload_notes", "render_guidance", "scripted_channel"]

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
    Acknowledgements go through `say` immediately from the reader thread -- a note that vanishes
    silently for six minutes of ChampSim would feel dropped even when it is not.
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


def scripted_channel(*texts: str):
    """A channel preloaded with notes, satisfying the same `drain()` contract the
    loops consume -- the reference fake (D404). Tests had grown three hand-rolled
    copies of these four lines; this is the one spelling."""

    class _Scripted:
        def __init__(self) -> None:
            self._pending = [Note(text=t, received_at=time.time()) for t in texts]

        def drain(self) -> list[Note]:
            out, self._pending = self._pending, []
            return out

    return _Scripted()


def drain_guidance(channel, accumulated: list[Note], *,
                   on_note: Callable[[Note], None] | None = None) -> str | None:
    """The consumer's whole move, in one call (D397 phase 2): drain what was typed
    since the last check, hand each fresh note to `on_note` (persist it, echo it into
    lessons -- a typed line must never vanish silently, D388), fold it into the run's
    accumulated notes, and return the labelled prompt block over ALL notes so far --
    or None when there is nothing to say, so callers thread it as an optional block.
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

    What the operator said last run still stands until they say otherwise -- the same
    read-back rule as measurements (D367, D388). Seed a run's accumulated-notes list
    with this so `render_guidance` shows them under their honest stamp; a fresh or
    record-less run yields [], and failures yield [] rather than a failed run."""
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

    Empty input renders to "" so callers can thread the block as an optional kwarg. Truncation
    is announced rather than silent (the posture `flux_llm.fit_to_budget` set): the model is
    told notes were omitted, never handed a quietly shortened history.
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
