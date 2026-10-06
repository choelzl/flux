"""The prompt-line editor and the TUI-side feedback channel.

`LineEditor` is a pure state machine (key code in, buffer out) so tests need no terminal.
`TuiFeedback` implements `flux_feedback.FeedbackChannel`'s duck-typed contract (`.drain()`,
`.active`, `.start()`, `.close()`): inside curses the TUI owns the keyboard, so it is the channel.
"""

from __future__ import annotations

import curses
import time

from flux_feedback import Note


def key_code(k: int | str) -> int | str:
    """A key as the handlers take it (D902): `get_wch` hands back a str for a character and an
    int for a function key. A control character or printable ASCII becomes its code -- the
    keybinds and the getch callers' ints -- any other character stays the str it is, so
    `Zürich café λ ≤ 10ns` reaches a field whole."""
    if isinstance(k, str):
        return ord(k) if len(k) == 1 and (ord(k) < 128 or not k.isprintable()) else k
    return k


def read_key(scr) -> int | str:
    """One key from the terminal, wide (D902): `get_wch`, its timeout as -1 like getch; a
    screen without it (a test's fake) answers getch."""
    get = getattr(scr, "get_wch", None)
    if get is None:
        return scr.getch()
    try:
        return key_code(get())
    except curses.error:                           # the timeout: no key this frame
        return -1


def typed(key: int | str) -> str | None:
    """The printable text a key carries, None for a control or function key (D902)."""
    if isinstance(key, str):
        return key if key.isprintable() else None
    return chr(key) if 32 <= key < 127 else None


def window(text: str, pos: int, room: int) -> tuple[str, int]:
    """The `room` columns of a one-row field that show the insertion point (D902): (the text
    shown, the cursor's column in it); "…" says where the text runs on beyond either edge."""
    room = max(2, room)
    if len(text) < room:
        return text, pos
    lo = max(0, min(pos - room + 2, len(text) - room + 1))      # the cursor a column inside the right edge
    shown = text[lo:lo + room]
    if lo > 0:
        shown = "…" + shown[1:]
    if lo + room < len(text):
        shown = shown[:-1] + "…"
    return shown, pos - lo


class LineEditor:
    """Minimal single-line editor: printable keys insert at the cursor, Left/Right/Home/End move
    it, Backspace/Delete delete, Enter submits (returns the text and clears). Everything else is
    ignored here and handled by the app (panel switches, scrolling, quit)."""

    def __init__(self) -> None:
        self.buffer = ""
        self.pos = 0                               # the insertion point (D902)

    def set(self, text: str) -> None:
        self.buffer, self.pos = text, len(text)

    def handle(self, key: int | str) -> str | None:
        self.pos = max(0, min(self.pos, len(self.buffer)))     # a caller set the buffer directly
        if key in (10, 13, curses.KEY_ENTER):      # Enter
            text, self.buffer, self.pos = self.buffer.strip(), "", 0
            return text or None
        if key in (8, 127, curses.KEY_BACKSPACE):  # Backspace variants
            if self.pos:
                self.buffer = self.buffer[:self.pos - 1] + self.buffer[self.pos:]
                self.pos -= 1
            return None
        if key == curses.KEY_DC:
            self.buffer = self.buffer[:self.pos] + self.buffer[self.pos + 1:]
        elif key == curses.KEY_LEFT:
            self.pos = max(0, self.pos - 1)
        elif key == curses.KEY_RIGHT:
            self.pos = min(len(self.buffer), self.pos + 1)
        elif key in (curses.KEY_HOME, 1):          # Home, Ctrl-A
            self.pos = 0
        elif key in (curses.KEY_END, 5):           # End, Ctrl-E
            self.pos = len(self.buffer)
        elif (ch := typed(key)) is not None:       # D902: any printable character, not ASCII only
            self.buffer = self.buffer[:self.pos] + ch + self.buffer[self.pos:]
            self.pos += len(ch)
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
