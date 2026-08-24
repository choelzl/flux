"""The curses shell: worker thread runs the loop, main thread paints at ~10 Hz.

Deliberately thin -- all content comes from `panels.build` (pure) and all input
editing from `LineEditor` (pure); this file only owns the terminal. Layout is the
btop convention: a header with the panel tabs, the panel body, and -- when feedback
is enabled -- a one-line prompt at the bottom. Without a tty, `run_tui` refuses and
the caller falls back to the plain run; a TUI that silently eats output under
redirection would be worse than none.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
import curses
import os
import threading
import time
from typing import Any, Callable

from .events import BusWriter, EventBus
from .input import LineEditor, TuiFeedback
from .panels import (PANELS, build, info_rows, log_rows, mentor_rows, results_browse, task_rows, timing_rows)


def _fmt_hdr_elapsed(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:d}:{s:02d}"


def demo_tui(run: Callable[..., Any], *, title: str, subtitle: str = "",
             print_report: Callable[[Any], None] | None = None,
             info: dict[str, Any] | None = None, feedback: bool = False) -> Any:
    """The one-liner every demo uses for `--tui`: run `run()` under the TUI, with
    `print_report(result)` captured into the results tab on completion. Falls back to
    a plain call when there is no tty (the TUI refuses redirection rather than eat
    output). KeyboardInterrupt from an abandoned run propagates to the caller, which
    owns the exit code and the "partial state is in the db" message.

    `feedback=True` arms the `f` prompt line (D388/D397): `run` is then called as
    `run(channel)` -- the TUI's TuiFeedback under the UI, `None` on the plain
    fallback -- so the loop can drain typed notes at its round boundaries."""

    def _on_result(result: Any) -> list[str]:
        if print_report is None:
            return []
        import io

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            print_report(result)
        return buf.getvalue().splitlines()

    try:
        return run_tui(lambda bus, fb: (run(fb) if feedback else run()),
                       title=title, subtitle=subtitle,
                       feedback_enabled=feedback, on_result=_on_result, info=info)
    except RuntimeError as exc:
        print(f"[tui unavailable: {exc}] running plain")
        return run(None) if feedback else run()


def _stop_requested() -> str | None:
    """`flux stop`'s request for this process's campaign (D513), consumed once seen."""
    try:
        from flux_loop import ops

        why = ops.stop_requested()
        if why:
            ops.clear_stop()
        return why
    except Exception:  # noqa: BLE001 -- the TUI runs fine without the loop package
        return None


def demo_run(run: Callable[..., Any], *, tui: bool, title: str, subtitle: str = "",
             print_report: Callable[[Any], None] | None = None,
             info: dict[str, Any] | None = None) -> Any:
    """A demo's whole run path (D404): the TUI with the f-key armed when asked, the
    plain terminal with the stdin channel otherwise -- one place, because five demos
    had grown the same eight lines. `run(channel)` receives whichever channel is
    live (the TUI's TuiFeedback, the stdin FeedbackChannel, or None on the no-tty
    fallback). KeyboardInterrupt propagates: the demo owns its exit code and its
    partial-state message."""
    if tui:
        return demo_tui(run, title=title, subtitle=subtitle,
                        print_report=print_report, info=info, feedback=True)
    from flux_feedback import FeedbackChannel

    channel = FeedbackChannel()
    channel.start()
    try:
        return run(channel)
    finally:
        channel.close()


class _PhaseTasks:
    """flux_profile listener -> bus task rows: phase() opens a task, its exit closes
    it with ok/FAIL; mark() threads stage headlines through the history."""

    def __init__(self, bus: EventBus) -> None:
        self._bus = bus

    def phase_start(self, name: str, why: str, params: dict) -> int:
        return self._bus.task_start(name, kind="tool", why=why, params=params)

    def phase_end(self, token: int, name: str, seconds: float, failed: bool,
                  output: dict | None = None) -> None:
        self._bus.task_end(token, ok=not failed, output=output)

    def phase_update(self, token: int, name: str, output: dict) -> None:
        self._bus.task_update(token, output)                  # D493: live thinking

    def mark(self, name: str, why: str) -> None:
        self._bus.task(name, why=why)

    def publish(self, key: str, payload: dict) -> None:
        self._bus.standing(key, payload)


def run_tui(target: Callable[[EventBus, TuiFeedback], Any], *,
            title: str = "flux", subtitle: str = "",
            feedback_enabled: bool = False,
            on_result: Callable[[Any], Any] | None = None,
            info: dict[str, Any] | None = None) -> Any:
    """Run `target(bus, feedback)` in a worker thread under a curses UI.

    Returns whatever `target` returned (or raises what it raised) once the user
    quits after completion. `target`'s prints are captured into the log panel.

    `on_result(result)` renders the finished run INTO the results tab (a string or
    list of lines): the TUI stays open on completion and switches to that tab, so
    the report is read inside the panels rather than lost to terminal scrollback
    after quitting -- the caller usually still prints it to the real stdout after
    `run_tui` returns, for the shell transcript.
    """
    import sys

    if not sys.stdout.isatty():
        raise RuntimeError("no tty: run without --tui (the TUI refuses redirection)")
    os.environ.setdefault("TERM", "xterm-256color")   # curses dies without one

    bus = EventBus()
    feedback = TuiFeedback()
    box: dict[str, Any] = {}
    # The terminal comes back EXACTLY as it was, no matter what curses, a worker
    # thread, or a grandchild process did to it -- a TUI that leaves echo off (typed
    # input invisible, measured) costs more trust than it earns. curses.wrapper
    # restores its own modes; this restores the tty attributes beneath them.
    try:
        import termios

        saved_tty = termios.tcgetattr(0)
    except Exception:  # noqa: BLE001 -- no tty attrs to save is fine
        termios = None
        saved_tty = None

    def worker() -> None:
        box.pop("error", None)
        if run_no[0] > 1:
            bus.result(f"── run #{run_no[0]} ──")   # separate rerun reports in tab 3
        writer = BusWriter(bus, fd=out_w)
        # Every `flux_profile.phase(...)` anywhere in the loop -- a ChampSim run, a
        # Yosys screen, a model call -- becomes a live task row with its real duration
        # (D391). No loop changes a line; richer sites pass why=/params on the phase.
        try:
            from flux_profile import clear_listener, set_listener

            set_listener(_PhaseTasks(bus))
        except Exception:  # noqa: BLE001 -- the TUI runs fine uninstrumented
            clear_listener = None
        try:
            # Python-level capture is process-global, so prints from ANY thread --
            # including Ray's driver-side log forwarding -- land in the log panel.
            with contextlib.redirect_stdout(writer), contextlib.redirect_stderr(writer):
                box["result"] = target(bus, feedback)
                bus.at_rest = bool(getattr(box["result"], "at_rest", False)
                                   or (isinstance(box["result"], dict) and box["result"].get("at_rest")))
                if on_result is not None:
                    try:
                        rendered = on_result(box["result"])
                        lines = (rendered.splitlines()
                                 if isinstance(rendered, str) else list(rendered or []))
                        for line in lines:
                            bus.result(str(line))
                        bus.log(f"[tui] final report rendered to the results tab "
                                f"({len(lines)} lines)")
                    except Exception as exc:  # noqa: BLE001 -- a report renderer must not kill the run
                        bus.log(f"[tui] on_result failed: {exc}")
            bus.done()
        except Exception as exc:  # noqa: BLE001 -- shown in the task panel, re-raised after quit
            box["error"] = exc
            bus.done(error=f"{type(exc).__name__}: {exc}")
        finally:
            if clear_listener is not None:
                clear_listener()
            writer.flush()

    # FD-level stderr capture (the lesson of the first live run, D390): Ray init
    # banners, C-level warnings and grandchild processes write to fd 2 directly and
    # scribble over the curses screen. curses never writes stderr, so fd 2 is safe to
    # steal for the whole session; fd 1 stays the terminal because curses draws
    # through it. A drain thread pumps the pipe into the log panel.
    saved_err = os.dup(2)
    pipe_r, pipe_w = os.pipe()
    os.dup2(pipe_w, 2)
    os.close(pipe_w)
    out_r, out_w = os.pipe()   # backs BusWriter.fileno() for fd-level stdout writers

    def _drain(fd: int, prefix: str) -> None:
        with os.fdopen(fd, "r", errors="replace") as f:
            for line in f:
                bus.log(prefix + line.rstrip("\n"))

    threading.Thread(target=_drain, args=(pipe_r, "[stderr] "),
                     name="flux-tui-stderr", daemon=True).start()
    threading.Thread(target=_drain, args=(out_r, ""),
                     name="flux-tui-fdout", daemon=True).start()

    run_no = [1]
    bus.task("starting", why="importing, resolving inputs -- before the loop's "
                             "own first stage mark")

    def rerun() -> None:
        """One more pass of the SAME loop on the same bus -- fired by the r loop
        toggle whenever a run finishes with looping ON (D409). For loops that
        resume from their own store (the campaign records, D367) each pass
        literally continues the study; for others it is an honest fresh pass whose
        history stays on screen next to the last one."""
        run_no[0] += 1
        bus.restart(run_no[0])
        threading.Thread(target=worker, name=f"flux-tui-loop-{run_no[0]}",
                         daemon=True).start()

    thread = threading.Thread(target=worker, name="flux-tui-loop", daemon=True)
    thread.start()
    try:
        os.environ.setdefault("ESCDELAY", "250")  # see set_escdelay in _main
        curses.wrapper(_main, bus, feedback, title, subtitle, feedback_enabled,
                       rerun, dict(info or {}), lambda: run_no[0])
    finally:
        os.dup2(saved_err, 2)
        os.close(saved_err)
        os.close(out_w)
        if saved_tty is not None:
            with contextlib.suppress(Exception):
                termios.tcsetattr(0, termios.TCSADRAIN, saved_tty)
    if "error" in box:
        raise box["error"]
    if not bus.snapshot()["finished"]:
        raise KeyboardInterrupt("run abandoned from the TUI")
    return box.get("result")


_SPIN = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


def _colors() -> dict[str, int]:
    """Guarded color pairs: state accents where the terminal has them, plain
    attributes where it does not -- the layout never depends on color."""
    attrs = {"ok": curses.A_BOLD, "bad": curses.A_BOLD, "run": curses.A_BOLD,
             "dim": curses.A_DIM, "warn": curses.A_BOLD,
             # every role key exists without colour too (a terminal without colours, a headless run)
             "io": 0, "mentor": 0, "orchestrator": 0, "generator": 0, "evaluator": 0, "model": curses.A_BOLD}
    try:
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_GREEN, -1)
        curses.init_pair(2, curses.COLOR_RED, -1)
        curses.init_pair(3, curses.COLOR_CYAN, -1)
        attrs["ok"] = curses.color_pair(1) | curses.A_BOLD
        attrs["bad"] = curses.color_pair(2) | curses.A_BOLD
        attrs["run"] = curses.color_pair(3) | curses.A_BOLD
        # The slides' roles (D418k): io green, mentor magenta, orchestrator plain,
        # generator yellow, evaluator blue; the model purple-and-bold, as the
        # legend's purple border marks the nodes that use it.
        curses.init_pair(4, curses.COLOR_MAGENTA, -1)
        curses.init_pair(5, curses.COLOR_YELLOW, -1)
        curses.init_pair(6, curses.COLOR_BLUE, -1)
        attrs["io"] = curses.color_pair(1)
        attrs["mentor"] = curses.color_pair(4)
        attrs["orchestrator"] = 0
        attrs["generator"] = curses.color_pair(5)
        attrs["evaluator"] = curses.color_pair(6)
        attrs["model"] = curses.color_pair(4) | curses.A_BOLD
        attrs["warn"] = curses.color_pair(5) | curses.A_BOLD     # the part being tried (D487)
    except Exception:  # noqa: BLE001
        pass
    return attrs




def _highlight_model(scr, y: int, x: int, text: str, width: int, attr: int) -> None:
    """Repaint the model marker -- "(model)" or "·model" -- in the marker's color,
    leaving the rest of the row in its role's color (D418k, Cedric: the model is a
    sub-color of the role, not a role)."""
    for token in ("(model)", "·model"):
        k = text.find(token)
        if k >= 0 and k < width:
            scr.addnstr(y, x + k, token[:width - k], width - k, attr)


def _task_key(key: int, st: dict, order: list, page: int = 10) -> dict:
    """The task tab's keys as a pure transition (D418h, Cedric's model): selection IS
    the mode. Nothing selected -> ↑/↓ move the highlight through the list, Enter (or a
    click) selects it. Something selected -> ↑/↓ scroll and ←/→ pan the details,
    PgUp/PgDn page, and Enter CLEARS the selection (a toggle). Esc is a no-op.
    `st` = {cursor, sel, dscroll, dhscroll}."""
    import curses as _c

    out = dict(st)
    if key == 27:
        # Esc does NOTHING here (Cedric: Enter drives select/deselect). Measured
        # reason as well as design: an arrow's escape sequence that arrives split
        # across reads is reported as a bare Esc, and a key that closed the
        # selection on that would yank it away under a scrolling user.
        return out
    if key in (10, 13, _c.KEY_ENTER) and st.get("sel") is not None:
        out["sel"], out["dscroll"], out["dhscroll"] = None, 0, 0   # Enter toggles: clear
        return out
    if st.get("sel") is None:
        if key in (10, 13, _c.KEY_ENTER) and order:
            cur = st.get("cursor")
            out["sel"] = cur if cur in order else order[0]
            out["dscroll"], out["dhscroll"] = 0, 0
        elif key in (_c.KEY_UP, _c.KEY_DOWN) and order:
            cur = order.index(st["cursor"]) if st.get("cursor") in order else 0
            step = -1 if key == _c.KEY_UP else +1
            out["cursor"] = order[max(0, min(len(order) - 1, cur + step))]
        return out
    # a scroll of -1 (TAIL) is the end of the text, sticking to it as lines are added (D506);
    # `dmax` is the last fixed offset the pane reported
    here = st.get("dscroll", 0)
    dmax = st.get("dmax")                    # None: the pane has not said where its end is
    if here < 0:
        here = dmax if dmax is not None else 0

    def down(to: int) -> int:
        return -1 if dmax is not None and to >= dmax else to

    if key == _c.KEY_UP:
        out["dscroll"] = max(0, here - 1)
    elif key == _c.KEY_DOWN:
        out["dscroll"] = down(here + 1)
    elif key == _c.KEY_PPAGE:
        out["dscroll"] = max(0, here - page)          # D574: a page is the screen's height, not ten lines
    elif key == _c.KEY_NPAGE:
        out["dscroll"] = down(here + page)
    elif key == _c.KEY_LEFT:
        out["dhscroll"] = max(0, st.get("dhscroll", 0) - 10)
    elif key == _c.KEY_RIGHT:
        out["dhscroll"] = st.get("dhscroll", 0) + 10
    return out


def _step_cursor(cursor: int, paths: list, delta: int) -> int:
    """Move the timing cursor to the next/previous TREE row (one with a path)."""
    i = cursor + delta
    while 0 <= i < len(paths):
        if paths[i] is not None:
            return i
        i += delta
    return cursor


def _keep_visible(idx: int, n: int, view_h: int, scroll: int) -> int:
    """The scroll (lines from the bottom) that keeps line `idx` on screen."""
    start = max(0, n - view_h - scroll)
    if idx < start:
        return max(0, n - view_h - idx)
    if idx >= start + view_h:
        return max(0, n - view_h - (idx - view_h + 1))
    return scroll


def _toggle_fold(folded: set, cursor: int, paths: list, kids: list) -> set:
    """Fold/unfold the row under the cursor when it has children; the root ()
    folds everything beneath it."""
    if not (0 <= cursor < len(paths)) or paths[cursor] is None or not kids[cursor]:
        return folded
    path = paths[cursor]
    out = set(folded)
    if path in out:
        out.discard(path)
    else:
        out.add(path)
    return out


def _bar_hit(bar: str, mx: int) -> str | None:
    """Which bottom-bar token a click at column `mx` lands on (D411 follow-up:
    mouse support). Pure, so the hit-testing is testable without a terminal."""
    for token, action in (("r loop", "loop"), ("t think", "think"),
                          ("f feedback", "feedback"), ("q quit", "quit")):
        i = bar.find(token)
        if i < 0:
            continue
        end = i + len(token)
        if end < len(bar) and bar[end] == ":":        # a ":ON"/":off" state suffix
            while end < len(bar) and bar[end] != " ":
                end += 1
        if i <= mx < end:
            return action
    return None


def _tab_hit(tabs: list[tuple[int, int, str]], mx: int) -> str | None:
    """Which panel a click on the tab row selects; `tabs` is (x0, x1, name)."""
    for x0, x1, name in tabs:
        if x0 <= mx < x1:
            return name
    return None


class _Screen:
    """The curses window, with `curses.error` swallowed on DRAW calls (D405).

    A resize mid-frame makes any write past the new edge raise, and a terminal
    squeezed below the layout's rows makes hline/addnstr raise every frame -- either
    way the exception unwound curses.wrapper and the TUI exited on its own while the
    run kept going, which read as the TUI abandoning the user (observed on the
    prefetcher demo). A frame is disposable: skip the failed write, draw the next
    frame against the new size. Input (`getch`) and geometry (`getmaxyx`) pass
    through untouched, so nothing that carries state is ever swallowed."""

    _DRAW = {"addnstr", "addstr", "hline", "move", "erase", "refresh"}

    def __init__(self, scr) -> None:
        self._scr = scr

    def __getattr__(self, name):
        attr = getattr(self._scr, name)
        if name not in self._DRAW:
            return attr

        def safe(*a, **k):
            try:
                return attr(*a, **k)
            except curses.error:
                return None

        return safe


@dataclass
class _Pane:
    """One browsable tab's state (D532, review step 13): the highlight, the pinned selection,
    the details' scroll and pan, the last render's row ids and order. The task, mentor and
    results tabs are three of these, each the same model (D498): browse with the arrows, pin
    with Enter or a click, scroll and pan the details, Enter closes."""

    cursor: int | None = None
    selected: int | None = None
    dscroll: int = 0
    dhscroll: int = 0
    dmax: int = 0
    ids: list = field(default_factory=list)
    order: list = field(default_factory=list)

    BROWSE = (10, 13, 27, curses.KEY_ENTER, curses.KEY_UP, curses.KEY_DOWN, curses.KEY_LEFT,
              curses.KEY_RIGHT, curses.KEY_PPAGE, curses.KEY_NPAGE)

    def key(self, key: int, start_at_end: bool = False, page: int = 10) -> None:
        if start_at_end and self.selected is None and self.cursor is None and key in (curses.KEY_UP, curses.KEY_DOWN):
            self.cursor = self.order[-1]                 # nothing highlighted: start at the last row
        cursor = self.cursor if (self.cursor is not None or not start_at_end) else self.order[-1]
        st = _task_key(key, {"cursor": cursor, "sel": self.selected, "dscroll": self.dscroll,
                             "dhscroll": self.dhscroll, "dmax": self.dmax}, self.order, page=page)
        self.cursor, self.selected = st["cursor"], st["sel"]
        self.dscroll, self.dhscroll = st["dscroll"], st["dhscroll"]

    def click(self, idx: int) -> bool:
        """A click on a row pins it; True when the row was one."""
        if 0 <= idx < len(self.ids) and self.ids[idx] is not None:
            self.cursor = self.selected = self.ids[idx]
            self.dscroll = self.dhscroll = 0
            return True
        return False

    def clamp(self, clamp: dict) -> None:
        self.dscroll = clamp.get("dscroll", self.dscroll)
        self.dmax = clamp.get("dmax", 0)
        self.dhscroll = clamp.get("dhscroll", self.dhscroll)

    def rendered(self, ids: list, order: list) -> None:
        self.ids, self.order = list(ids), list(order)


@dataclass
class _Timing:
    """The timing tab's tree: a cursor over its rows, what is folded (D418d)."""

    folded: set = field(default_factory=set)
    cursor: int = 1
    paths: list = field(default_factory=list)
    kids: list = field(default_factory=list)


def _page(view: Any) -> int:
    """How far PgUp/PgDn jump (D574, Cedric: "way faster"): the screen's height less the chrome,
    never under ten lines."""
    return max(10, int(getattr(view, "h_last", 0) or 0) - 8)


@dataclass
class _View:
    """What the whole screen shares: the tab, the panel's own scroll and pan, the modes, the
    toggles, the last frame's geometry."""

    color: dict
    editor: Any
    panel: str = "task"
    scroll: int = 0
    hscroll: int = 0
    quit_armed: bool = False
    switched_on_done: bool = False
    input_mode: bool = False
    filter_mode: bool = False
    think_state: bool | None = None
    loop_on: bool = True
    log_filter: str = ""
    tab_hits: list = field(default_factory=list)
    bar: str = ""
    h_last: int = 24
    view_h_last: int = 1
    tstart: int = 0
    kcursor: int = 0
    kids_task: list = field(default_factory=list)


def _main(scr, bus: EventBus, feedback: TuiFeedback, title: str, subtitle: str,
          feedback_enabled: bool, rerun, info: dict[str, Any], run_no_view) -> None:
    with contextlib.suppress(Exception):
        curses.curs_set(0)          # cursor appears only in feedback input mode
    with contextlib.suppress(Exception):
        # A bare Esc is held for ESCDELAY (1 s by default) in case it starts an
        # escape sequence. 25 ms made Esc instant but split real arrow sequences
        # over screen/SSH latency into Esc + junk (D418m, measured); 250 ms keeps
        # arrows whole, and Esc only cancels a typed prompt now, so its lag is moot.
        curses.set_escdelay(250)
    scr.timeout(100)                                     # ~10 Hz frame budget
    with contextlib.suppress(Exception):                 # mouse: clicks + wheel
        curses.mousemask(curses.ALL_MOUSE_EVENTS)
    scr = _Screen(scr)              # draw calls survive resizes and tiny terminals
    view = _View(color=_colors(), editor=LineEditor())
    try:                              # a demo may have set the override before the TUI (--think)
        from flux_llm import think_override

        view.think_state = think_override()
    except Exception:  # noqa: BLE001
        pass
    # The task, mentor and results tabs browse the same way (D498); the timing tab folds
    panes = {"task": _Pane(), "mentor": _Pane(), "results": _Pane()}
    timing = _Timing()
    while True:
        key = scr.getch()
        if _handle_key(key, view, panes, timing, bus, feedback, feedback_enabled) == "quit":
            return
        with contextlib.suppress(Exception):
            curses.curs_set(1 if (view.input_mode or view.filter_mode) else 0)
        snap = _advance(view, bus, rerun, key)
        _render(scr, view, panes, timing, snap, title, subtitle, feedback, feedback_enabled, info, run_no_view)
        if snap["finished"] and key == -1:
            time.sleep(0.05)                             # idle politely once done


def _handle_key(key: int, view: _View, panes: dict[str, _Pane], timing: _Timing, bus: EventBus,
                feedback: TuiFeedback, feedback_enabled: bool) -> str | None:
    """One key against the view: the modes first (typing a filter or a note), then the
    tabs, the panes, the toggles, the mouse. "quit" when the loop should end."""
    if key == -1:
        return None
    editor = view.editor
    if view.filter_mode:
        # typing the log filter: Esc cancels, Enter applies (D418l)
        if key == 27:
            editor.buffer = ""
            view.filter_mode = False
        else:
            text = editor.handle(key)
            if text is not None:
                view.log_filter = text.strip()
                view.filter_mode = False
                view.scroll = 0
        return None
    if view.input_mode:
        # MODAL feedback entry: every key belongs to the line until Esc or Enter,
        # so digits, r, q, t are typeable without fighting the keybinds.
        if key == 27:                                # Esc cancels
            editor.buffer = ""
            view.input_mode = False
        else:
            text = editor.handle(key)
            if text:
                feedback.submit(text)
                bus.log(f'feedback noted: "{text}" -- reaches the next drain point')
                view.input_mode = False
        return None
    ch = chr(key) if 0 <= key < 256 else ""
    panel = view.panel
    if ch in PANELS:
        view.panel, view.scroll, view.hscroll = PANELS[ch], 0, 0
    elif key == curses.KEY_LEFT and panel not in ("task", "mentor", "results"):
        view.hscroll = max(0, view.hscroll - 10)
    elif key == curses.KEY_RIGHT and panel not in ("task", "mentor", "results"):
        view.hscroll += 10
    elif panel == "results" and key in _Pane.BROWSE and panes["results"].order:
        # the task tab's model (D498): browsing, ↑↓ move the highlight through the
        # parts and the report with a preview below; ⏎ (or a click) pins, then ↑↓
        # scroll and ←→ pan the details, ⏎ closes
        panes["results"].key(key, start_at_end=True, page=_page(view))
    elif panel in ("mentor", "task") and key in _Pane.BROWSE:
        panes[panel].key(key, page=_page(view))
    elif key == curses.KEY_UP and panel == "timing" and timing.paths:
        timing.cursor = _step_cursor(timing.cursor, timing.paths, -1)
        view.scroll = _keep_visible(timing.cursor, len(timing.paths), view.view_h_last, view.scroll)
    elif key == curses.KEY_DOWN and panel == "timing" and timing.paths:
        timing.cursor = _step_cursor(timing.cursor, timing.paths, +1)
        view.scroll = _keep_visible(timing.cursor, len(timing.paths), view.view_h_last, view.scroll)
    elif key in (10, 13, curses.KEY_ENTER, ord(" ")) and panel == "timing":
        timing.folded = _toggle_fold(timing.folded, timing.cursor, timing.paths, timing.kids)
    elif ch == "/" and panel == "log":
        editor.buffer = view.log_filter
        view.filter_mode = True
    elif key == 27 and panel == "log" and view.log_filter:
        view.log_filter, view.scroll = "", 0
    elif ch == "-" and panel == "timing":
        timing.folded = {p for p, k in zip(timing.paths, timing.kids) if p is not None and k and p != ()}
    elif ch in ("+", "=") and panel == "timing":
        timing.folded = set()
    elif key == curses.KEY_UP:
        view.scroll += 1
    elif key == curses.KEY_DOWN:
        view.scroll = max(0, view.scroll - 1)
    elif key == curses.KEY_PPAGE:
        view.scroll += _page(view)                    # D574
    elif key == curses.KEY_NPAGE:
        view.scroll = max(0, view.scroll - _page(view))
    elif ch == "q":
        if bus.snapshot()["finished"] or view.quit_armed:
            return "quit"
        view.quit_armed = True                        # second q abandons a live run
        bus.log("[tui] run still going; press q again to abandon it")
    elif ch == "r":
        view.loop_on = not view.loop_on
        bus.log("[tui] loop -> " + (
            "ON: each finished run starts the next pass" if view.loop_on
            else "OFF: the current run finishes and holds"))
    elif ch == "f" and feedback_enabled:
        view.input_mode = True
    elif key == curses.KEY_MOUSE:
        return _mouse(view, panes, timing, bus, feedback_enabled)
    elif ch == "t":
        # Cycle the model's reasoning: default -> think ON -> think OFF -> …
        # Applies to every FUTURE model call (the current one keeps its mode).
        try:
            from flux_llm import set_think_override

            view.think_state = {None: True, True: False, False: None}[view.think_state]
            set_think_override(view.think_state)
            label = {None: "each proposer's default", True: "ON", False: "OFF"}[view.think_state]
            bus.log(f"[tui] model reasoning (think) -> {label} for future calls")
        except Exception as exc:  # noqa: BLE001
            bus.log(f"[tui] think toggle unavailable: {exc}")
    return None


def _mouse(view: _View, panes: dict[str, _Pane], timing: _Timing, bus: EventBus, feedback_enabled: bool) -> str | None:
    """A click or a wheel turn: the wheel IS the arrow key wherever ↑/↓ scroll or move
    (D487); a click on a row pins it, on a tab opens it, on the bar works its toggle."""
    with contextlib.suppress(Exception):
        _id, mx, my, _z, bstate = curses.getmouse()
        if bstate & curses.BUTTON4_PRESSED:            # wheel up
            for _ in range(3):
                curses.ungetch(curses.KEY_UP)
        elif bstate & getattr(curses, "BUTTON5_PRESSED", 0):
            for _ in range(3):
                curses.ungetch(curses.KEY_DOWN)             # wheel down
        elif bstate & (curses.BUTTON1_CLICKED | curses.BUTTON1_PRESSED):
            panel = view.panel
            in_body = 3 <= my < 3 + view.view_h_last
            idx = view.tstart + (my - 3)
            if panel in ("mentor", "results", "task") and in_body:
                panes[panel].click(idx)                 # a click OPENS the row (D487/D498)
            elif panel == "timing" and in_body and timing.paths:
                if 0 <= idx < len(timing.paths) and timing.paths[idx] is not None:
                    timing.cursor = idx
                    timing.folded = _toggle_fold(timing.folded, timing.cursor, timing.paths, timing.kids)
            elif my == 1:
                hit = _tab_hit(view.tab_hits, mx)
                if hit:
                    view.panel, view.scroll, view.hscroll = hit, 0, 0
            elif my == view.h_last - 1:
                act = _bar_hit(view.bar, mx)
                if act == "loop":
                    view.loop_on = not view.loop_on
                    bus.log("[tui] loop -> " + ("ON" if view.loop_on else "off"))
                elif act == "think":
                    curses.ungetch(ord("t"))
                elif act == "feedback" and feedback_enabled:
                    view.input_mode = True
                elif act == "quit" and bus.snapshot()["finished"]:
                    return "quit"
    return None


def _advance(view: _View, bus: EventBus, rerun, key: int) -> dict[str, Any]:
    """The bus's snapshot for this frame, and what a finished run does: land on the results
    tab once, then roll into the next pass when the loop toggle is on -- unless `flux stop`
    asked for the boundary (D513) or the campaign is at rest (D506)."""
    snap = bus.snapshot()
    # On completion, land the reader on the results tab once (the whole point of
    # finishing); after that the keys navigate as usual and q exits.
    if snap["finished"] and not view.switched_on_done:
        view.switched_on_done = True
        if snap["results"] and key == -1:
            view.panel, view.scroll = "results", 0
    # The loop toggle's ON half: a finished run rolls straight into the next
    # pass (rerun() flips the bus back to running, so this fires once per run).
    if snap["finished"] and view.loop_on and not snap.get("error"):
        asked = _stop_requested()
        if asked:
            # D513: `flux stop` asked for the pass boundary; the loop holds here and says so
            view.loop_on = False
            bus.log(f"[tui] loop -> off: {asked}; the pass ended and the run holds (q to leave, r to go on)")
        elif snap.get("at_rest"):
            # D506: the run says every ladder is spent -- another pass would decide the
            # same "stand" for every part (73 passes in four hours, live); the loop waits
            # for the operator instead of spinning
            view.loop_on = False
            bus.log("[tui] loop -> off: the campaign is AT REST (every ladder is spent); "
                    "press r to run another pass anyway, or change the ask")
        else:
            view.switched_on_done = False
            rerun()
            snap = bus.snapshot()
    return snap


def _render(scr, view: _View, panes: dict[str, _Pane], timing: _Timing, snap: dict[str, Any], title: str,
            subtitle: str, feedback: TuiFeedback, feedback_enabled: bool, info: dict[str, Any], run_no_view) -> None:
    """One frame: the header, the tab bar, the panel's lines, the bar."""
    color = view.color
    h, w = scr.getmaxyx()
    view.h_last = h
    scr.erase()
    # ── row 0: name left, live state right ─────────────────────────────
    up = _fmt_hdr_elapsed(snap["elapsed_s"])
    if snap.get("error"):
        state, sattr = f"failed · {up}", color["bad"]
    elif snap["finished"]:
        state, sattr = (f"at rest · {up}" if snap.get("at_rest") else f"done · {up}"), color["ok"]
    else:
        spin = _SPIN[int(time.time() * 8) % len(_SPIN)]
        state, sattr = f"{spin} running · {up}", color["run"]
    scr.addnstr(0, 0, f" {title}", w - 1, curses.A_BOLD)
    scr.addnstr(0, max(0, w - 1 - len(state) - 1), state, len(state) + 1, sattr)
    # ── row 1: tab bar, active segment highlighted ─────────────────────
    x = 1
    view.tab_hits = []
    for k, name in PANELS.items():
        seg = f" {k} {name} "
        attr = curses.A_REVERSE if name == view.panel else color["dim"]
        if x + len(seg) < w - 1:
            scr.addnstr(1, x, seg, w - 1 - x, attr)
            view.tab_hits.append((x, x + len(seg), name))
        x += len(seg) + 1
    scr.hline(2, 0, curses.ACS_HLINE, w - 1)
    # ── body ───────────────────────────────────────────────────────────
    top = 3
    # Status bar, plus the prompt row ONLY while typing: an idle "press f" line
    # duplicated the bottom bar's `f feedback` hint and cost a content row.
    bottom_rows = 1 + (1 if (feedback_enabled and view.input_mode) or view.filter_mode else 0)
    view_h = max(1, h - top - bottom_rows)
    info["_runs"] = run_no_view()
    lines, line_roles = _panel_lines(view, panes, timing, snap, feedback, feedback_enabled, info, w, view_h)
    view.view_h_last = view_h
    view.scroll = min(view.scroll, max(0, len(lines) - view_h))   # never scroll past the top
    longest = max((len(ln) for ln in lines), default=0)
    view.hscroll = max(0, min(view.hscroll, max(0, longest - 1)))   # nor past the right
    start = max(0, len(lines) - view_h - view.scroll)
    view.tstart = start
    visible = lines[start:start + view_h]
    panel = view.panel
    for i, line in enumerate(visible):
        idx = start + i
        role = line_roles[idx] if idx < len(line_roles) else None
        base, uses_model = (role or "").split("+")[0], "+model" in (role or "")
        attr = color.get(base, 0) if base else 0
        selected_row = _selected_row(panel, idx, line, view, panes, timing)
        if selected_row:
            attr = curses.A_REVERSE
        text = line[view.hscroll:] if view.hscroll < len(line) else ""
        scr.addnstr(top + i, 1, text, w - 2, attr)
        if uses_model and not selected_row:
            _highlight_model(scr, top + i, 1, text, w - 2, color["model"])
    if view.hscroll > 0:
        scr.addnstr(top, 1, f"⇠ col {view.hscroll}", 14, color["dim"])
    elif any(len(line) > w - 2 for line in visible):
        scr.addnstr(top, max(0, w - 20), "→ pans long lines", 18, color["dim"])
    if start > 0:
        scr.addnstr(top, max(0, w - 16), f"↑ {start} more", 14, color["dim"])
    if view.scroll > 0:
        scr.addnstr(top + view_h - 1, max(0, w - 16), f"↓ {view.scroll} newer", 14, color["dim"])
    _bottom(scr, view, snap, subtitle, feedback_enabled, h, w)
    scr.refresh()


def _panel_lines(view: _View, panes: dict[str, _Pane], timing: _Timing, snap: dict[str, Any], feedback: TuiFeedback,
                 feedback_enabled: bool, info: dict[str, Any], w: int, view_h: int) -> tuple[list[str], list]:
    """The current panel's lines and their roles; each browsable pane's ids, order and clamp
    land back on its `_Pane`. A panel bug must not exit the TUI."""
    panel = view.panel
    try:
        if panel == "timing":
            lines, timing.paths, timing.kids, line_roles = timing_rows(snap, timing.folded)
            timing.cursor = min(max(timing.cursor, 0), max(0, len(lines) - 1))
            view.kids_task = []
            return lines, line_roles
        timing.paths, timing.kids = [], []
        if panel == "task":
            pane = panes["task"]
            list_rows = 10
            detail_rows = max(3, view_h - 2 - list_rows - 1)   # the rest of the body
            clamp: dict = {}
            lines, kids_task, task_order, line_roles = task_rows(
                snap, pane.cursor, width=max(40, w - 4), list_rows=list_rows,
                detail_rows=detail_rows, detail_scroll=pane.dscroll,
                detail_hscroll=pane.dhscroll, selected=pane.selected, clamp=clamp)
            pane.clamp(clamp)                       # never past the end (TAIL = the end)
            pane.rendered(kids_task, task_order)
            view.kids_task = kids_task
            view.hscroll = 0                        # the details pane pans itself
            view.scroll = 0                         # fixed regions: no panel scroll
            if pane.selected not in task_order:
                pane.selected = None                 # it aged out: back to browsing
            if pane.cursor not in task_order:
                pane.cursor = None                   # follow the running task again
            shown_id = pane.selected if pane.selected is not None else pane.cursor
            sel_now = next((t for t in kids_task if t is not None and (shown_id is None or t == shown_id)), None)
            view.kcursor = kids_task.index(sel_now) if sel_now in kids_task else 0
            return lines, line_roles
        view.kids_task = []
        if panel == "info":
            lines, line_roles = info_rows(snap, info, feedback.notes)
        elif panel == "log":
            lines, line_roles = log_rows(snap, view.log_filter)
        elif panel == "mentor":
            pane = panes["mentor"]
            list_rows = 8
            detail_rows = max(3, view_h - 2 - list_rows - 1)
            clamp = {}
            lines, sec_ids, sec_order = mentor_rows(
                snap, pane.cursor, selected=pane.selected, width=max(40, w - 4),
                list_rows=list_rows, detail_rows=detail_rows,
                detail_scroll=pane.dscroll, detail_hscroll=pane.dhscroll, clamp=clamp)
            pane.clamp(clamp)
            pane.rendered(sec_ids, sec_order)
            line_roles = []
            view.hscroll = 0
            view.scroll = 0
        elif panel == "results":
            pane = panes["results"]
            n_table = len((snap.get("standings") or {}).get("standings", {}).get("parts") or []) + 3
            n_table += 4                             # OBJECTIVE / NOW / WHOLE / the report row (D497/D498)
            detail_rows = max(3, view_h - n_table - 1)
            clamp = {}
            lines, res_ids, res_order, line_roles = results_browse(
                snap, pane.cursor, selected=pane.selected, width=max(40, w - 4),
                detail_rows=detail_rows, detail_scroll=pane.dscroll, detail_hscroll=pane.dhscroll, clamp=clamp)
            pane.clamp(clamp)
            pane.rendered(res_ids, res_order)
            view.hscroll = 0                         # the tab pans and scrolls its details itself
            view.scroll = 0
        else:
            lines = build(panel, snap, feedback.notes, feedback_enabled, info)
            line_roles = []
        return lines, line_roles
    except Exception as exc:  # noqa: BLE001 -- a panel bug must not exit the TUI
        timing.paths, timing.kids = [], []
        return [f"panel error: {type(exc).__name__}: {exc}",
                "(the run continues; other tabs may still render -- please report)"], []


def _selected_row(panel: str, idx: int, line: str, view: _View, panes: dict[str, _Pane], timing: _Timing) -> bool:
    if panel == "timing":
        return bool(idx == timing.cursor and timing.paths and timing.paths[idx] is not None)
    if panel == "task":
        kids = view.kids_task
        return bool(kids and idx < len(kids) and kids[idx] is not None and kids[idx] == kids[view.kcursor])
    if panel in ("mentor", "results"):
        ids = panes[panel].ids
        return bool(ids and idx < len(ids) and ids[idx] is not None and line.startswith("▸"))
    return False


def _bottom(scr, view: _View, snap: dict[str, Any], subtitle: str, feedback_enabled: bool, h: int, w: int) -> None:
    """The optional prompt row, then the status bar: one token per toggle (D411 follow-up,
    Cedric's call) -- the key and its state fused, a word never twice (D418i)."""
    editor = view.editor
    if feedback_enabled and view.input_mode:
        scr.addnstr(h - 2, 0, f" feedback ❯ {editor.buffer}", w - 1, curses.A_BOLD)
    elif view.filter_mode:
        scr.addnstr(h - 2, 0, f" filter ❯ {editor.buffer}", w - 1, curses.A_BOLD)
    loop_tag = " · r loop:ON" if view.loop_on else " · r loop"
    think_tag = " · t think:ON" if view.think_state is True else " · t think"
    if view.input_mode or view.filter_mode:
        hints = " esc cancel · ⏎ " + ("send" if view.input_mode else "apply")
    elif snap["finished"]:
        hints = " q quit" + loop_tag + think_tag
    else:
        hints = (loop_tag.removeprefix(" ·") + think_tag + (" · f feedback" if feedback_enabled else ""))
    bar = hints.ljust(w - 1)
    if subtitle:
        tail = f"{subtitle} "
        if len(hints) + len(tail) < w - 1:
            bar = bar[: w - 1 - len(tail)] + tail
    view.bar = bar
    scr.addnstr(h - 1, 0, bar, w - 1, curses.A_REVERSE)
    if feedback_enabled and view.input_mode:
        scr.move(h - 2, min(len(f" feedback ❯ {editor.buffer}"), w - 2))
    elif view.filter_mode:
        scr.move(h - 2, min(len(f" filter ❯ {editor.buffer}"), w - 2))
