"""The TUI's main loop, driven headlessly (D532, review step 13): a fake screen feeds a key
script, a bus carries a finished run with standings and a report, and every tab is visited,
browsed and scrolled without a terminal. What the `Pane` refactor must keep working."""

from __future__ import annotations

import curses

from flux_tui.app import _main
from flux_tui.events import EventBus

if not hasattr(curses, "ACS_HLINE"):          # set by initscr, which a headless run never calls
    curses.ACS_HLINE = 0


class _FakeScreen:
    """Answers `getch` from a script (then -1 forever), records what was drawn."""

    def __init__(self, keys: list[int], size=(30, 100)) -> None:
        self.keys = list(keys)
        self.size = size
        self.drawn: list[tuple[int, int, str]] = []
        self.frames = 0

    def getch(self):
        return self.keys.pop(0) if self.keys else -1

    def getmaxyx(self):
        return self.size

    def erase(self):
        self.frames += 1

    def addnstr(self, y, x, text, n, attr=0):
        self.drawn.append((y, x, str(text)[:n]))

    def addstr(self, y, x, text, attr=0):
        self.drawn.append((y, x, str(text)))

    def hline(self, *a, **k):
        pass

    def refresh(self):
        pass

    def move(self, *a):
        pass

    def timeout(self, *a):
        pass

    def keypad(self, *a):
        pass


class _Feedback:
    notes: list = []

    def submit(self, text):
        self.notes.append(text)


def _finished_bus() -> EventBus:
    bus = EventBus()
    bus.log("hello from the run")
    bus.standing("standings", {"problem": "nlu", "at": "after step 1", "step": 1, "steps": 3, "judged": 2, "proven": 1,
                               "parts_total": 2, "measured": 1, "searching": False,
                               "objective": {"goal": "a goal", "now": "proving exp", "composed": "5,420 um2 · 788 MHz"},
                               "parts": [{"part": "recip", "state": "proven", "name": "transpiled_recip_p3", "numbers": "y = 1/x", "prototype": "def design(x):\n    return x\n", "report": "0 over", "artifact": "module nlu_recip; endmodule"},
                                         {"part": "exp", "state": "trying", "prototype_score": 12.0, "via": "table"}],
                               "front": [{"name": "composed[exp, recip]", "stage": "confirm", "x": 788.0, "y": 5420.0, "decision": True},
                                         {"name": "composed[exp, recip]", "stage": "confirm", "x": 812.0, "y": 6000.0, "decision": False}],
                               "axes": ["fmax_mhz", "area_um2"]})
    t = bus.task_start("llm: generating (model)", kind="model", why="turn 1")
    bus.task_end(t, ok=True, note="done")
    bus.result("THE ANSWER")
    bus.result("  composed: 5,420 um2 788 MHz")
    bus.done()
    return bus


def test_every_tab_is_visited_browsed_and_the_loop_quits_on_q():
    bus = _finished_bus()
    keys: list[int] = []
    for tab in "123456 9":
        if tab == " ":
            continue
        keys += [ord(tab), curses.KEY_DOWN, curses.KEY_DOWN, curses.KEY_UP, 10, curses.KEY_RIGHT, curses.KEY_LEFT, curses.KEY_NPAGE, curses.KEY_PPAGE, 27]
    keys += [ord("r"), ord("r"), ord("t"), ord("t"), ord("t"), ord("q"), ord("q")]
    scr = _FakeScreen(keys)
    reruns: list[int] = []

    def rerun():                                   # the real one flips the bus back to running
        reruns.append(1)
        bus.restart(len(reruns) + 1)

    _main(scr, bus, _Feedback(), "flux · test", "test.db", True, rerun, {"db": "test.db"}, lambda: 1)
    assert scr.frames >= len(keys) - 1, "one frame per key at least (the last q returns before a frame)"
    drawn = " ".join(t for _y, _x, t in scr.drawn)
    assert "flux · test" in drawn and "hello from the run" in drawn and "THE ANSWER" in drawn
    assert "transpiled_recip_p3" in drawn and "proving exp" in drawn and "the front on the confirm stage" in drawn
    assert reruns == [1], "loop:ON on a finished run starts the next pass once; the run is then live and q must be pressed twice"


def test_the_loop_toggle_reruns_a_finished_run_and_a_second_q_abandons_a_live_one():
    bus = _finished_bus()
    scr = _FakeScreen([-1, ord("q"), ord("q")])
    reruns: list[int] = []

    def rerun():
        reruns.append(1)
        bus.restart(2)

    _main(scr, bus, _Feedback(), "t", "", False, rerun, {}, lambda: 1)
    assert reruns == [1], "loop:ON on a finished run starts the next pass once, then the run is live and q arms"
    assert any("press q again" in ln for ln in bus.snapshot()["log"])
    # r off first: a finished run holds, and one q leaves
    bus2 = _finished_bus()
    scr2 = _FakeScreen([ord("r"), ord("q")])
    reruns2: list[int] = []
    _main(scr2, bus2, _Feedback(), "t", "", False, lambda: reruns2.append(1), {}, lambda: 1)
    assert reruns2 == [] and any("loop -> OFF" in ln for ln in bus2.snapshot()["log"])
