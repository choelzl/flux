"""The TUI's main loop, driven headlessly (D532): a fake screen feeds a key script, a bus carries
a finished run with standings and a report, and every tab is visited, browsed and scrolled."""

from __future__ import annotations

import curses

import pytest

from flux_tui.app import _main
from flux_tui.events import EventBus


@pytest.fixture(autouse=True)
def _no_registered_run(monkeypatch):
    """Each test starts with no run registered, so a stop that q asked for does not leak."""
    from flux_loop import ops

    monkeypatch.setattr(ops, "_CURRENT", {})

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


def test_the_first_q_on_a_live_run_asks_for_a_stop_at_the_pass_boundary(tmp_path):
    """q once: the run stops at the end of its pass (as `flux stop`) and is not rerun; q again abandons it (D621)."""
    from flux_loop import ops

    ops._CURRENT["dir"] = str(tmp_path)
    bus = EventBus()                                   # a live run: not finished
    bus.log("running")
    scr = _FakeScreen([ord("q"), ord("q")])
    _main(scr, bus, _Feedback(), "t", "", False, lambda: None, {}, lambda: 1)
    assert "q in the TUI" in ops.stop_requested()
    assert any("stopping at the end of this pass" in ln for ln in bus.snapshot()["log"])


class _FrameScreen(_FakeScreen):
    """Keeps the last frame only: what is on the screen now."""

    def erase(self):
        super().erase()
        self.drawn = []


def _parts_bus(n: int) -> EventBus:
    bus = EventBus()
    bus.standing("standings", {"at": "after step 1", "step": 1, "steps": 3, "judged": n, "proven": n, "parts_total": n,
                               "objective": {"goal": "latency under 10 ns", "now": "measuring", "composed": "7 ns"},
                               "parts": [{"part": f"part_{i:03}", "state": "proven", "name": f"impl_{i:03}",
                                          "prototype": "def f(x):\n    return x\n", "report": f"report of part {i:03}"}
                                         for i in range(n)],
                               "front": [{"x": 1.0, "y": 2.0, "stage": "confirm"}, {"x": 2.0, "y": 1.0, "stage": "confirm"}],
                               "axes": ["latency", "cost"]})
    for i in range(40):
        bus.result(f"report line {i}")
    bus.done()
    return bus


def test_the_results_tab_shows_the_selected_part_and_the_objective_at_80x24():
    """D903, from an external review: with 30 parts at 80x24 the tab drew the tail of an oversized
    table, so selecting the first part changed the preview while its row stayed off-screen, and the
    objective area was cut. The list is a window that follows the highlight under a kept objective."""
    bus = _parts_bus(30)
    scr = _FrameScreen([ord("r"), ord("3"), *[curses.KEY_UP] * 31, ord("q")], size=(24, 80))   # r: no rerun; ↑ from the report to the first part
    _main(scr, bus, _Feedback(), "t", "", False, lambda: None, {}, lambda: 1)
    rows = {y: t for y, _x, t in scr.drawn}
    body = [rows.get(y, "") for y in range(3, 23)]
    assert any(t.startswith("▸") and "part_000" in t for t in body), body
    assert any("OBJECTIVE" in t and "latency under 10 ns" in t for t in body) and any("NOW" in t for t in body)
    assert any(t.startswith("▌── part_000") for t in body) and any("def f(x):" in t for t in body)
    assert any("more" in t and "↓" in t for t in body), "the list says what lies below it"
    assert "↑" not in rows.get(2, ""), "nothing of the panel is cut off above"


def test_the_results_tab_fits_its_height_and_reaches_every_entry():
    """D903: at every size and every highlight the tab keeps to its rows, the highlighted entry and
    its preview title are on them, and the objective comes first; the chart folds when the list needs
    the room. Design rows open their own design."""
    from flux_tui.panels import results_browse

    for n in (3, 30):
        snap = _parts_bus(n).snapshot()
        order = results_browse(snap, None)[2]
        for height in (20, 10, 40):
            for focus in order:
                for selected in (None, focus):
                    lines, ids, _o, roles = results_browse(snap, focus, selected=selected, width=78, height=height)
                    assert len(lines) <= height and len(ids) == len(lines) == len(roles), (n, height, focus)
                    assert [i for i, ln in zip(ids, lines) if ln.startswith("▸")] == [focus], (n, height, focus)
                    assert lines[0].startswith("standings ·") and (height < 20 or "OBJECTIVE" in lines[1])
                    assert sum(ln.startswith("▌──") for ln in lines) == 1
        tall = results_browse(snap, 0, width=78, height=40)[0]
        assert any("the front on the confirm stage" in ln for ln in tall) == (n == 3), "the chart where it fits, folded where not"
    snap = {"standings": {"standings": {"searching": True, "designs": [
        {"name": "w2", "stage": "screen", "numbers": "120 MHz", "decision": True, "deliverable": "a", "knobs": "width=2"},
        {"name": "w9", "stage": "screen", "numbers": "90 MHz", "deliverable": "b", "knobs": "width=9"},
        {"name": "w3", "stage": "screen", "numbers": "100 MHz", "deliverable": "a", "knobs": "width=3"}]}},
        "results": [], "measurements": []}
    lines, ids, order, _r = results_browse(snap, None, width=78, height=20)
    for i, ln in zip(ids, lines):
        if i is not None and "MHz" in ln:
            opened = results_browse(snap, i, selected=i, width=78, height=20)[0]
            name = ln.split()[1] if ln.split()[0] == "◆" else ln.split()[0]
            assert any(o.startswith(f"▌── {'◆ ' if '◆' in ln else ''}{name} on screen") for o in opened), (ln, opened)


class _WideScreen(_FakeScreen):
    """Answers `get_wch` as a terminal does: a str per character, an int per function key, and
    curses.error on the timeout."""

    def get_wch(self):
        if not self.keys:
            raise curses.error("no input")
        return self.keys.pop(0)


def test_feedback_typed_as_wide_keys_is_submitted_whole_at_the_minimum_size():
    """D902: the real key path -- get_wch, the keybinds, the editor -- submits Unicode whole, and at
    40x14 the line shows its end where the cursor is."""
    bus = _finished_bus()
    note = "Zürich café λ ≤ 10ns and a note longer than the line TAIL"
    fb = _Feedback()
    fb.notes = []
    scr = _WideScreen(["r", "f", *note, "\n", "q"], size=(14, 40))
    _main(scr, bus, fb, "t", "", True, lambda: None, {}, lambda: 1)
    assert fb.notes == [note]
    shown = [t for y, _x, t in scr.drawn if y == 12 and "feedback ❯" in t]
    assert shown and shown[-1].rstrip().endswith("TAIL") and all(len(t) <= 39 for t in shown)
