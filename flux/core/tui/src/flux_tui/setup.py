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
Enter is a new line; in the files field, Enter adds the typed path (Tab completes it) and
Delete removes the last file; Left / Right change a choice or a number; Space toggles a box;
Enter on Start (or F5 anywhere) starts; Esc on an empty field, or Quit, leaves.
"""

from __future__ import annotations

import curses
import glob
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = ["AUTHORS", "FIELDS", "SetupForm", "run_setup", "slug"]

AUTHORS = ("model", "opencode", "claude", "codex")
FIELDS = ("prompt", "files", "skills", "author", "passes", "screen_only", "review", "workdir", "start", "quit")
_LABEL = {"prompt": "Prompt", "files": "Files", "skills": "Skills", "author": "Author", "passes": "Passes",
          "screen_only": "Screen only", "review": "Review first", "workdir": "Directory"}

TAB, BTAB, ENTER, ESC, BACKSPACES, DELETE = 9, curses.KEY_BTAB, (10, 13, curses.KEY_ENTER), 27, (8, 127, curses.KEY_BACKSPACE), curses.KEY_DC
F5 = curses.KEY_F5


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
        """Tab in the files field: the typed path completed as far as it is unique."""
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

    def handle(self, key: int) -> str | None:
        """One key; returns "start" or "quit" when the form is done."""
        f = self.field
        if key == F5:
            return self._start()
        if key == BTAB:
            self._move(-1)
            return None
        if key == TAB and f != "files":
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
            elif 32 <= key < 127:
                self.prompt += chr(key)
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
            elif 32 <= key < 127:
                self.path_input += chr(key)
            return None
        if f == "author" and key in (curses.KEY_LEFT, curses.KEY_RIGHT, 32):
            i = AUTHORS.index(self.author) if self.author in AUTHORS else 0
            self.author = AUTHORS[(i + (-1 if key == curses.KEY_LEFT else 1)) % len(AUTHORS)]
        elif f == "passes":
            if key == curses.KEY_LEFT:
                self.passes = max(0, self.passes - 1)
            elif key == curses.KEY_RIGHT:
                self.passes = min(20, self.passes + 1)
            elif 48 <= key <= 57:
                self.passes = int(chr(key))
        elif f in ("screen_only", "review") and key in (32, *ENTER):
            setattr(self, f, not getattr(self, f))
        elif f == "workdir":
            if key in BACKSPACES:
                self.workdir = (self.workdir or self.directory())[:-1]
            elif 32 <= key < 127:
                self.workdir = (self.workdir or "") + chr(key)
        elif f == "start" and key in ENTER:
            return self._start()
        elif f == "quit" and key in ENTER:
            return "quit"
        return None

    def lines(self, width: int) -> list[tuple[str, str]]:
        """The form as (kind, text) rows -- kind "focus" marks the row with the cursor."""
        rows: list[tuple[str, str]] = []

        def row(name: str, text: str) -> None:
            rows.append(("focus" if self.field == name else "", f" {_LABEL.get(name, ''):<13}{text}"[:width]))

        prompt_lines = (self.prompt or "").split("\n")
        shown = prompt_lines[-6:] if len(prompt_lines) > 6 else prompt_lines
        for i, ln in enumerate(shown):
            label = "Prompt" if i == 0 else ""
            cursor = "▏" if self.field == "prompt" and i == len(shown) - 1 else ""
            rows.append(("focus" if self.field == "prompt" else "", f" {label:<13}{ln}{cursor}"[:width]))
        if not self.prompt and self.field != "prompt":
            rows[-1] = ("", f" {'Prompt':<13}(what you want, in words)"[:width])
        for name in ("files", "skills"):
            row(name, "  ".join(Path(f).name for f in getattr(self, name)) or "(none)")
            if self.field == name:
                rows.append(("focus", f" {'':<13}+ {self.path_input}▏   Enter adds, Tab completes, Del removes the last"[:width]))
        row("author", f"< {self.author} >")
        row("passes", f"< {self.passes or 'until stopped'} >")
        row("screen_only", "[x]" if self.screen_only else "[ ]")
        row("review", ("[x]" if self.review else "[ ]") + "  show the problem before the loop runs it")
        row("workdir", self.directory() + ("▏" if self.field == "workdir" else ""))
        rows.append(("", ""))
        start = "[ Start ]" if self.field != "start" else "[>Start<]"
        quit_ = "[ Quit ]" if self.field != "quit" else "[>Quit<]"
        rows.append(("focus" if self.field in ("start", "quit") else "", f" {'':<13}{start}   {quit_}"))
        rows.append(("", ""))
        rows.append(("dim", " Tab/Shift-Tab move · Left/Right choose · Space toggles · F5 starts · Esc leaves"[:width]))
        if self.message:
            rows.append(("warn", f" {self.message}"[:width]))
        return rows


def _draw(scr, form: SetupForm) -> None:
    scr.erase()
    h, w = scr.getmaxyx()
    title = " flux ask -- the loop from a prompt and files "
    with _quiet():
        scr.addstr(0, 0, title[: w - 1], curses.A_BOLD)
        scr.addstr(1, 0, " An author writes the problem from your prompt and files; the loop runs it."[: w - 1], curses.A_DIM)
    for i, (kind, text) in enumerate(form.lines(w - 1)):
        if 3 + i >= h - 1:
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
            key = scr.getch()
            got = form.handle(key)
            if got == "start":
                return form.settings()
            if got == "quit":
                return None

    os.environ.setdefault("TERM", "xterm-256color")
    os.environ.setdefault("ESCDELAY", "250")
    return curses.wrapper(main)
