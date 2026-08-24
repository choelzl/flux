"""The prompt-line editor and the TUI-side feedback channel.

`LineEditor` is a pure state machine (key code in, buffer out) so tests never need a
terminal. `TuiFeedback` implements the same duck-typed contract the loops already
accept from `flux_feedback.FeedbackChannel` -- `.drain() -> list[Note]`, `.active`,
`.start()`, `.close()` -- because inside curses the raw-stdin channel cannot exist:
the TUI owns the keyboard, so the TUI is the channel.
"""

from __future__ import annotations

import time

from flux_feedback import Note


class LineEditor:
    """Minimal single-line editor: printable keys append, backspace deletes, Enter
    submits (returns the text and clears). Everything else is ignored here and
    handled by the app (panel switches, scrolling, quit)."""

    def __init__(self) -> None:
        self.buffer = ""

    def handle(self, key: int) -> str | None:
        if key in (10, 13):                      # Enter
            text, self.buffer = self.buffer.strip(), ""
            return text or None
        if key in (8, 127, 263):                 # Backspace variants
            self.buffer = self.buffer[:-1]
            return None
        if 32 <= key < 127:                      # printable ASCII
            self.buffer += chr(key)
            return None
        return None


class TuiFeedback:
    """The loops' feedback seam, fed by the TUI's prompt line instead of raw stdin."""

    def __init__(self) -> None:
        self.active = True
        self.notes: list[Note] = []
        self._pending: list[Note] = []

    def start(self) -> None:  # the channel contract; the TUI needs no reader thread
        return

    def submit(self, text: str) -> Note:
        note = Note(text=text, received_at=time.time())
        self._pending.append(note)
        self.notes.append(note)
        return note

    def drain(self) -> list[Note]:
        out, self._pending = self._pending, []
        return out

    def close(self) -> None:
        self.active = False
