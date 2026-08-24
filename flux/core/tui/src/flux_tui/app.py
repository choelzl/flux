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
import curses
import os
import threading
import time
from typing import Any, Callable

from .events import BusWriter, EventBus
from .input import LineEditor, TuiFeedback
from .panels import PANELS, build


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

    def phase_end(self, token: int, name: str, seconds: float, failed: bool) -> None:
        self._bus.task_end(token, ok=not failed)

    def mark(self, name: str, why: str) -> None:
        self._bus.task(name, why=why)


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
             "dim": curses.A_DIM}
    try:
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_GREEN, -1)
        curses.init_pair(2, curses.COLOR_RED, -1)
        curses.init_pair(3, curses.COLOR_CYAN, -1)
        attrs["ok"] = curses.color_pair(1) | curses.A_BOLD
        attrs["bad"] = curses.color_pair(2) | curses.A_BOLD
        attrs["run"] = curses.color_pair(3) | curses.A_BOLD
    except Exception:  # noqa: BLE001
        pass
    return attrs


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


def _main(scr, bus: EventBus, feedback: TuiFeedback, title: str, subtitle: str,
          feedback_enabled: bool, rerun, info: dict[str, Any], run_no_view) -> None:
    with contextlib.suppress(Exception):
        curses.curs_set(0)          # cursor appears only in feedback input mode
    color = _colors()
    scr.timeout(100)                                     # ~10 Hz frame budget
    scr = _Screen(scr)              # draw calls survive resizes and tiny terminals
    editor = LineEditor()
    panel = "task"
    scroll = 0
    quit_armed = False
    switched_on_done = False
    input_mode = False          # f enters it; Esc cancels; Enter submits (D392):
    think_state: bool | None = None   # modal input frees 0-9/r/q/t for keybinds
    # The r key is a LOOP TOGGLE (D409): on = when a run finishes, start the next
    # pass immediately and keep going; off = finish the current run and hold there.
    # Flipping it mid-run takes effect at the run's end, never by interrupting.
    loop_on = True

    while True:
        key = scr.getch()
        if key != -1 and input_mode:
            # MODAL feedback entry: every key belongs to the line until Esc or Enter,
            # so digits, r, q, t are typeable without fighting the keybinds.
            if key == 27:                                # Esc cancels
                editor.buffer = ""
                input_mode = False
            else:
                text = editor.handle(key)
                if text:
                    feedback.submit(text)
                    bus.log(f'feedback noted: "{text}" -- reaches the next drain point')
                    input_mode = False
        elif key != -1:
            ch = chr(key) if 0 <= key < 256 else ""
            if ch in PANELS:
                panel, scroll = PANELS[ch], 0
            elif key == curses.KEY_UP:
                scroll += 1
            elif key == curses.KEY_DOWN:
                scroll = max(0, scroll - 1)
            elif key == curses.KEY_PPAGE:
                scroll += 10
            elif key == curses.KEY_NPAGE:
                scroll = max(0, scroll - 10)
            elif ch == "q":
                if bus.snapshot()["finished"] or quit_armed:
                    return
                quit_armed = True                        # second q abandons a live run
                bus.log("[tui] run still going; press q again to abandon it")
            elif ch == "r":
                loop_on = not loop_on
                bus.log("[tui] loop -> " + (
                    "ON: each finished run starts the next pass" if loop_on
                    else "OFF: the current run finishes and holds"))
            elif ch == "f" and feedback_enabled:
                input_mode = True
            elif ch == "t":
                # Cycle the model's reasoning: default -> think ON -> think OFF -> …
                # Applies to every FUTURE model call (the current one keeps its mode).
                try:
                    from flux_llm import set_think_override

                    think_state = {None: True, True: False, False: None}[think_state]
                    set_think_override(think_state)
                    label = {None: "each proposer's default", True: "ON",
                             False: "OFF"}[think_state]
                    bus.log(f"[tui] model reasoning (think) -> {label} "
                            "for future calls")
                except Exception as exc:  # noqa: BLE001
                    bus.log(f"[tui] think toggle unavailable: {exc}")

        with contextlib.suppress(Exception):
            curses.curs_set(1 if input_mode else 0)
        snap = bus.snapshot()
        # On completion, land the reader on the results tab once (the whole point of
        # finishing); after that the keys navigate as usual and q exits.
        if snap["finished"] and not switched_on_done:
            switched_on_done = True
            if snap["results"] and key == -1:
                panel, scroll = "results", 0
        # The loop toggle's ON half: a finished run rolls straight into the next
        # pass (rerun() flips the bus back to running, so this fires once per run).
        if snap["finished"] and loop_on and not snap.get("error"):
            switched_on_done = False
            rerun()
            snap = bus.snapshot()
        h, w = scr.getmaxyx()
        scr.erase()

        # ── row 0: name left, live state right ─────────────────────────────
        up = _fmt_hdr_elapsed(snap["elapsed_s"])
        if snap.get("error"):
            state, sattr = f"failed · {up}", color["bad"]
        elif snap["finished"]:
            state, sattr = f"done · {up}", color["ok"]
        else:
            spin = _SPIN[int(time.time() * 8) % len(_SPIN)]
            state, sattr = f"{spin} running · {up}", color["run"]
        scr.addnstr(0, 0, f" {title}", w - 1, curses.A_BOLD)
        scr.addnstr(0, max(0, w - 1 - len(state) - 1), state, len(state) + 1, sattr)

        # ── row 1: tab bar, active segment highlighted ─────────────────────
        x = 1
        for k, name in PANELS.items():
            seg = f" {k} {name} "
            attr = curses.A_REVERSE if name == panel else color["dim"]
            if x + len(seg) < w - 1:
                scr.addnstr(1, x, seg, w - 1 - x, attr)
            x += len(seg) + 1
        scr.hline(2, 0, curses.ACS_HLINE, w - 1)

        # ── body ───────────────────────────────────────────────────────────
        top = 3
        # Status bar, plus the prompt row ONLY while typing: an idle "press f" line
        # duplicated the bottom bar's `f feedback` hint and cost a content row.
        bottom_rows = 1 + (1 if feedback_enabled and input_mode else 0)
        view_h = max(1, h - top - bottom_rows)
        info["_runs"] = run_no_view()
        try:
            lines = build(panel, snap, feedback.notes, feedback_enabled, info)
        except Exception as exc:  # noqa: BLE001 -- a panel bug must not exit the TUI
            lines = [f"panel error: {type(exc).__name__}: {exc}",
                     "(the run continues; other tabs may still render -- please report)"]
        scroll = min(scroll, max(0, len(lines) - view_h))   # never scroll past the top
        start = max(0, len(lines) - view_h - scroll)
        for i, line in enumerate(lines[start:start + view_h]):
            scr.addnstr(top + i, 1, line, w - 2)
        if start > 0:
            scr.addnstr(top, max(0, w - 16), f"↑ {start} more", 14, color["dim"])
        if scroll > 0:
            scr.addnstr(top + view_h - 1, max(0, w - 16), f"↓ {scroll} newer", 14,
                        color["dim"])

        # ── bottom: optional prompt, then status bar ───────────────────────
        if feedback_enabled and input_mode:
            scr.addnstr(h - 2, 0, f" feedback ❯ {editor.buffer}", w - 1, curses.A_BOLD)
        think_tag = {None: "", True: " · think:on", False: " · think:off"}[think_state]
        loop_tag = f" · r loop:{'ON' if loop_on else 'off'}"
        if input_mode:
            hints = " esc cancel · ⏎ send"
        elif snap["finished"]:
            hints = " q quit" + loop_tag + " · ↑/↓ scroll · t think" + think_tag
        else:
            hints = (" 1-6 tabs · ↑/↓ scroll · qq abandon" + loop_tag + " · t think"
                     + (" · f feedback" if feedback_enabled else "") + think_tag)
        bar = hints.ljust(w - 1)
        if subtitle:
            tail = f"{subtitle} "
            if len(hints) + len(tail) < w - 1:
                bar = bar[: w - 1 - len(tail)] + tail
        scr.addnstr(h - 1, 0, bar, w - 1, curses.A_REVERSE)
        if feedback_enabled and input_mode:
            scr.move(h - 2, min(len(f" feedback ❯ {editor.buffer}"), w - 2))
        scr.refresh()

        if snap["finished"] and key == -1:
            time.sleep(0.05)                             # idle politely once done
