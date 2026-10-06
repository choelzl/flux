"""The setup screen before the loop screen (D587): what `flux ask --tui` opens -- the prompt,
the input files, who authors the problem, how many passes, and whether the document is shown
for review before the loop runs it. Filled, it hands its settings to the run; the run then
opens the loop TUI.

    ┌ flux ask ───────────────────────────────────────────────────────────┐
    │ Prompt       a signed 8x8 multiplier, the smallest that makes 1 GHz │
    │ Files        spec.pdf  ref.sv  + type a path, Enter to add          │
    │ Author       < model >                                              │
    │ Passes       until stopped   Screen only [ ]  Review first [x]      │
    │ Directory    out/ask_a_signed_8x8_multiplier                        │
    │                                        [ Start ]   [ Quit ]         │
    └─────────────────────────────────────────────────────────────────────┘

`SetupForm` is a pure state machine -- a key in, the form changed, maybe an action out -- so
tests drive it without a terminal; `run_setup` is the thin curses shell around it.
Keys: Tab / Shift-Tab (or Up / Down outside the prompt) move between fields; in the prompt,
Enter is a new line; in the files and skills fields, Enter adds the typed path (Tab completes it) and
Delete removes the last file; Left / Right change a choice or a number; in Passes, digits type
a cap (the first replaces, the next append, Backspace edits) and u (or Left from 1) is until
stopped (D902); Space toggles a box;
Enter on Start (or F5 anywhere) starts; Esc on an empty field, or Quit, leaves.
"""

from __future__ import annotations

import curses
import glob
import os
import re
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .input import read_key, typed

__all__ = ["AUTHORS", "FIELDS", "SetupForm", "run_setup", "slug"]

AUTHORS = ("model", "opencode", "claude", "codex")
FIELDS = ("prompt", "files", "skills", "author", "passes", "screen_only", "review", "workdir", "start", "quit")
_LABEL = {"prompt": "Prompt", "files": "Files", "skills": "Skills", "author": "Author", "passes": "Passes",
          "screen_only": "Screen only", "review": "Review first", "workdir": "Directory"}

TAB, BTAB, ENTER, ESC, BACKSPACES, DELETE = 9, curses.KEY_BTAB, (10, 13, curses.KEY_ENTER), 27, (8, 127, curses.KEY_BACKSPACE), curses.KEY_DC
F5 = curses.KEY_F5
MAX_PASSES = 9999


def slug(prompt: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", prompt.lower()).strip("_")[:40] or "ask"


@dataclass
class SetupForm:
    prompt: str = ""
    files: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)      # D588: skill folders
    author: str = "model"
    passes: int = 0                           # D593: 0 = until stopped; N caps the passes
    screen_only: bool = False
    review: bool = True
    workdir: str = ""                         # empty: out/ask_<slug of the prompt> (its name: the id, D786)
    focus: int = 0
    path_input: str = ""                      # the path being typed into the files field
    message: str = ""                         # the last thing the form said (a refused path, a missing prompt)
    passes_typing: bool = False               # D902: a digit appends once one was typed here; the first replaces

    @property
    def field(self) -> str:
        return FIELDS[self.focus]

    def directory(self) -> str:
        return self.workdir or str(Path("out") / f"ask_{slug(self.prompt)}")

    def settings(self) -> dict[str, Any]:
        return {"prompt": self.prompt.strip(), "files": list(self.files), "skills": list(self.skills), "author": self.author,
                "passes": self.passes, "screen_only": self.screen_only, "review": self.review,
                "workdir": self.directory()}

    def _move(self, d: int) -> None:
        self.focus = (self.focus + d) % len(FIELDS)
        self.message = ""
        self.passes_typing = False

    def _add_path(self) -> None:
        p = self.path_input.strip()
        if not p:
            return
        if not Path(p).expanduser().exists():
            self.message = f"{p}: no such file or folder"
            return
        if self.field == "skills":
            q = Path(p).expanduser()
            if not ((q / "SKILL.md").is_file() or any((d / "SKILL.md").is_file() for d in q.iterdir() if d.is_dir())
                    if q.is_dir() else False):
                self.message = f"{p}: not a skill (a folder with SKILL.md, or a folder of them)"
                return
        getattr(self, self.field).append(p)
        self.path_input = ""
        self.message = f"added {p}"

    def _complete(self) -> None:
        """Tab in a path field (files, skills): the typed path completed as far as it is unique."""
        hits = sorted(glob.glob(os.path.expanduser(self.path_input) + "*"))
        if len(hits) == 1:
            self.path_input = hits[0] + ("/" if os.path.isdir(hits[0]) else "")
        elif hits:
            self.path_input = os.path.commonprefix(hits)
            self.message = "  ".join(Path(h).name for h in hits[:8]) + (" ..." if len(hits) > 8 else "")

    def _start(self) -> str | None:
        if not self.prompt.strip():
            self.message = "the prompt is empty: say what you want"
            self.focus = 0
            return None
        return "start"

    def _passes(self, key: int | str) -> None:
        """The pass cap as an editable number (D902): the first digit typed here replaces the
        value, the next ones append (1 then 0 is 10, never 0); Backspace takes the last digit off;
        until stopped (0) is chosen, never typed -- u, or Left from 1 -- and a leading 0 is refused."""
        if key == curses.KEY_LEFT:
            self.passes = max(0, self.passes - 1)
        elif key == curses.KEY_RIGHT:
            self.passes = min(MAX_PASSES, self.passes + 1)
        elif key in (ord("u"), ord("U")):
            self.passes = 0
        elif key in BACKSPACES:
            self.passes = int(str(self.passes)[:-1] or 0) if self.passes else 0
        elif isinstance(key, int) and 48 <= key <= 57:
            now = str(self.passes) if self.passes and self.passes_typing else ""
            if not now and key == 48:
                self.message = "a cap starts at 1; u (or Left from 1) is until stopped"
                return
            self.passes = min(MAX_PASSES, int(now + chr(key)))
        else:
            return
        self.passes_typing = key not in (curses.KEY_LEFT, curses.KEY_RIGHT, ord("u"), ord("U"))
        self.message = "" if self.passes else "until stopped: type a number to cap the passes"

    def handle(self, key: int | str) -> str | None:
        """One key; returns "start" or "quit" when the form is done."""
        f = self.field
        if key == F5:
            return self._start()
        if key == BTAB:
            self._move(-1)
            return None
        if key == TAB and f not in ("files", "skills"):   # D903: both path fields complete on Tab
            self._move(1)
            return None
        if key == ESC:
            if f in ("files", "skills") and self.path_input:
                self.path_input = ""           # Esc clears a half-typed path first
                return None
            if f == "prompt" and self.prompt:
                return None                    # Esc never wipes a prompt, nor leaves one
            return "quit"
        if key in (curses.KEY_UP, curses.KEY_DOWN) and f != "prompt":
            self._move(-1 if key == curses.KEY_UP else 1)
            return None
        if f == "prompt":
            if key in ENTER:
                self.prompt += "\n"
            elif key in BACKSPACES:
                self.prompt = self.prompt[:-1]
            elif key in (curses.KEY_UP, curses.KEY_DOWN):
                self._move(-1 if key == curses.KEY_UP else 1)
            elif (ch := typed(key)) is not None:          # D902: any printable character
                self.prompt += ch
            return None
        if f in ("files", "skills"):
            if key == TAB:
                if self.path_input:
                    self._complete()
                else:
                    self._move(1)
            elif key in ENTER:
                self._add_path()
            elif key in BACKSPACES:
                self.path_input = self.path_input[:-1]
            elif key == DELETE and getattr(self, f):
                self.message = f"removed {getattr(self, f).pop()}"
            elif (ch := typed(key)) is not None:
                self.path_input += ch
            return None
        if f == "author" and key in (curses.KEY_LEFT, curses.KEY_RIGHT, 32):
            i = AUTHORS.index(self.author) if self.author in AUTHORS else 0
            self.author = AUTHORS[(i + (-1 if key == curses.KEY_LEFT else 1)) % len(AUTHORS)]
        elif f == "passes":
            self._passes(key)
        elif f in ("screen_only", "review") and key in (32, *ENTER):
            setattr(self, f, not getattr(self, f))
        elif f == "workdir":
            if key in BACKSPACES:
                self.workdir = (self.workdir or self.directory())[:-1]
            elif (ch := typed(key)) is not None:
                self.workdir = (self.workdir or "") + ch
        elif f == "start" and key in ENTER:
            return self._start()
        elif f == "quit" and key in ENTER:
            return "quit"
        return None

    def lines(self, width: int) -> list[tuple[str, str]]:
        """The form as (kind, text) rows -- kind "focus" marks the row with the cursor."""
        body, foot = self.parts(width)
        return body + foot

    def parts(self, width: int) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
        """(the fields, the footer) as rows (D857): the footer -- Start and Quit, the keys, a message
        -- stays on the screen; the fields scroll. The prompt wraps; a long value shows its end, where
        the cursor is, after an ellipsis."""
        label_w = 14
        room = max(8, width - label_w)
        rows: list[tuple[str, str]] = []

        def tail(text: str, keep: int = 1) -> str:         # an input: its end, where one types (and its cursor)
            n = room - keep
            return text if len(text) <= n else "…" + text[-(n - 1):]

        def head(text: str) -> str:                        # a list: its start, marked when cut
            return text if len(text) <= room else text[:room - 1] + "…"

        def row(name: str, text: str) -> None:
            rows.append(("focus" if self.field == name else "", f" {_LABEL.get(name, ''):<13}{text}"[:width]))

        wrapped: list[str] = []
        for ln in (self.prompt or "").split("\n"):
            # D902: room left for the "…" and the cursor, so neither is cut at the right edge
            wrapped += textwrap.wrap(ln, room - 2, break_long_words=True, replace_whitespace=False, drop_whitespace=False) or [""]
        cut = len(wrapped) > 6
        shown = wrapped[-6:] if cut else wrapped
        for i, ln in enumerate(shown):
            label = "Prompt" if i == 0 else ""
            more = "…" if cut and i == 0 else ""
            cursor = "▏" if self.field == "prompt" and i == len(shown) - 1 else ""
            rows.append(("focus" if self.field == "prompt" else "", f" {label:<13}{more}{ln}{cursor}"[:width]))
        if not self.prompt and self.field != "prompt":
            rows[-1] = ("", f" {'Prompt':<13}(what you want, in words)"[:width])
        for name in ("files", "skills"):
            row(name, head("  ".join(Path(f).name for f in getattr(self, name)) or "(none)"))
            if self.field == name:
                rows.append(("focus", f" {'':<13}+ {tail(self.path_input, 3)}▏"[:width]))
                rows.append(("dim", f" {'':<13}Enter adds, Tab completes, Del removes the last"[:width]))
        row("author", f"< {self.author} >")
        row("passes", f"< {self.passes or 'until stopped'} >" + ("▏" if self.field == "passes" and self.passes_typing else ""))
        if self.field == "passes":
            rows.append(("dim", f" {'':<13}digits cap it, Backspace edits, u until stopped"[:width]))
        row("screen_only", "[x]" if self.screen_only else "[ ]")
        row("review", ("[x]" if self.review else "[ ]") + "  show the problem before the loop runs it")
        row("workdir", tail(self.directory(), 1 if self.field == "workdir" else 0) + ("▏" if self.field == "workdir" else ""))
        start = "[ Start ]" if self.field != "start" else "[>Start<]"
        quit_ = "[ Quit ]" if self.field != "quit" else "[>Quit<]"
        keys = textwrap.wrap("Tab/Shift-Tab move · Left/Right choose · Space toggles · F5 starts · Esc leaves",
                             max(20, width - 1), break_on_hyphens=False)
        foot = [("", ""), ("focus" if self.field in ("start", "quit") else "", f" {'':<13}{start}   {quit_}"[:width]),
                *(("dim", f" {k}"[:width]) for k in keys)]
        if self.message:
            foot.append(("warn", f" {self.message}"[:width]))
        return rows, foot

    def screen(self, width: int, height: int) -> list[tuple[str, str]]:
        """Exactly what fits in `height` rows (D857): the footer always, the fields scrolled so the
        focused one shows, "↑"/"↓ more" where some are cut; a screen too small says so."""
        body, foot = self.parts(width)
        room = height - len(foot)
        if room < 3 or width < 30:
            return [("warn", " The terminal is too small for the form: 40×14 at least."[:width]),
                    ("dim", " F5 starts · Esc leaves"[:width])][:max(1, height)]
        if len(body) <= room:
            return body + foot
        focus = next((i for i, (k, _t) in enumerate(body) if k == "focus"), 0)
        last = max(i for i, (k, _t) in enumerate(body) if k == "focus") if any(k == "focus" for k, _t in body) else 0
        span = room - 2                                   # a row each for the "more" marks
        top = min(max(0, last - span + 1), max(0, focus))
        # D902: a focused field taller than the view shows the row one types on (its cursor)
        cursor = next((i for i, (k, t) in enumerate(body) if k == "focus" and "▏" in t), focus)
        top = max(top, cursor - span + 1)
        top = min(top, len(body) - span)
        shown = body[top:top + span]
        up = ("dim", f" ↑ {top} more"[:width]) if top else ("", "")
        down_n = len(body) - top - span
        down = ("dim", f" ↓ {down_n} more"[:width]) if down_n > 0 else ("", "")
        return [up, *shown, down, *foot]


def _draw(scr, form: SetupForm) -> None:
    scr.erase()
    h, w = scr.getmaxyx()
    title = " flux ask -- the loop from a prompt and files "
    with _quiet():
        scr.addstr(0, 0, title[: w - 1], curses.A_BOLD)
        sub = " An author writes the problem from your prompt and files; the loop runs it."
        scr.addstr(1, 0, (sub if len(sub) < w else sub[: w - 2] + "…")[: w - 1], curses.A_DIM)
    for i, (kind, text) in enumerate(form.screen(w - 1, h - 3)):      # D857: the footer kept, the fields scrolled
        if 3 + i >= h:
            break
        attr = curses.A_REVERSE if kind == "focus" else curses.A_DIM if kind == "dim" else curses.A_BOLD if kind == "warn" else 0
        with _quiet():
            scr.addstr(3 + i, 0, text.ljust(w - 1)[: w - 1], attr)
    scr.refresh()


class _quiet:
    """A draw call that does not fit is skipped, never raised (a tiny terminal)."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return exc[0] is curses.error


def run_setup(form: SetupForm | None = None) -> dict[str, Any] | None:
    """The setup screen; the settings when started, None when left."""
    form = form or SetupForm()

    def main(scr) -> dict[str, Any] | None:
        with _quiet():
            curses.curs_set(0)
        scr.keypad(True)
        while True:
            _draw(scr, form)
            key = read_key(scr)                        # D902: wide characters, whole
            got = form.handle(key)
            if got == "start":
                return form.settings()
            if got == "quit":
                return None

    os.environ.setdefault("TERM", "xterm-256color")
    os.environ.setdefault("ESCDELAY", "250")
    return curses.wrapper(main)
