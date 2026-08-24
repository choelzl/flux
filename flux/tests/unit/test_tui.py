"""flux_tui (docs/decisions.md D390): the bus, the panels, and the editor are pure and
tested here without any terminal; the curses shell itself is exercised only by hand
(`--tui` on a demo), which is the honest boundary for a UI."""

from __future__ import annotations

import io

from flux_tui import BusWriter, EventBus, LineEditor, PANELS, TuiFeedback, build


def test_bus_collects_and_snapshots_thread_safely():
    bus = EventBus(max_lines=5)
    for i in range(8):
        bus.log(f"line {i}")
    bus.task("stage 1")
    bus.measure({"pair": "a+b", "latC": 1.5})
    bus.result("front: a+b")
    snap = bus.snapshot()
    assert len(snap["log"]) == 5 and snap["log"][-1] == "[task] stage 1"
    assert snap["tasks"][-1]["name"] == "stage 1" and snap["tasks"][-1]["kind"] == "mark"
    assert snap["measurements"] == [{"pair": "a+b", "latC": 1.5}]
    assert not snap["finished"]
    bus.done(error="boom")
    snap = bus.snapshot()
    assert snap["finished"] and snap["error"] == "boom"
    # the clock stops at done and restarts on rerun
    assert snap["finished_at"] is not None
    frozen = build("task", snap, [], False)[0]
    import time as _t
    _t.sleep(0.02)
    assert build("task", bus.snapshot(), [], False)[0] == frozen
    bus.restart(2)
    assert bus.snapshot()["finished_at"] is None
    # idle time between done and restart is NOT run time: the clock resumes from
    # where it stopped instead of swallowing the gap (the r-key complaint)
    bus2 = EventBus()
    bus2.started_at = bus2.run_started_at = _t.time() - 10.0   # ran 10s
    bus2.done()
    banked = bus2.snapshot()["elapsed_s"]
    assert 9.5 < banked < 10.5
    bus2.finished_at -= 300.0          # pretend 5 idle minutes passed reading results
    bus2.run_started_at = bus2.started_at   # (restart recomputes; simulate directly)
    bus2.restart(2)
    resumed = bus2.snapshot()["elapsed_s"]
    assert resumed < banked + 1.0, "restart must not add idle time to the clock"


def test_bus_writer_turns_prints_into_log_lines():
    bus = EventBus()
    w = BusWriter(bus)
    print("hello", file=w)
    print("a\nb", file=w)
    w.write("tail-no-newline")
    w.flush()
    assert bus.snapshot()["log"] == ["hello", "a", "b", "tail-no-newline"]


def test_task_rows_carry_why_params_duration_and_status():
    """D391: one tool call = one task row -- open with why/params, close with ok/FAIL,
    and the panel shows running rows with live durations and history with real ones."""
    import time as _t

    bus = EventBus()
    bus.task("stage1")   # headline
    tid = bus.task_start("tool:champsim", why="bingo+next_line",
                         params={"trace": "5G_1.xz", "sim": 150_000_000})
    running = bus.snapshot()["tasks"][-1]
    assert running["t1"] is None and running["why"] == "bingo+next_line"
    lines = build("task", bus.snapshot(), [], False)
    assert any("CURRENT: tool:champsim" in l for l in lines)
    assert any("why:" in l and "bingo+next_line" in l for l in lines)
    assert any("trace = 5G_1.xz" in l for l in lines)
    _t.sleep(0.01)
    bus.task_end(tid, ok=False, note="timeout")
    tid2 = bus.task_start("llm: generating", why="proposal round 2",
                          params={"model": "qwen3.8", "prompt": "line one\n" * 40})
    snap = bus.snapshot()
    # tab 1 shows the running LLM task in DEPTH: its actual prompt as a block
    lines = build("task", snap, [], False)
    assert any("CURRENT: llm: generating" in l for l in lines)
    assert any("prompt (" in l for l in lines)
    assert any("│ line one" in l for l in lines)
    bus.task_end(tid2)
    snap = bus.snapshot()
    done = [t for t in snap["tasks"] if t["t1"] is not None and t["kind"] != "mark"]
    assert done[0]["ok"] is False and done[0]["t1"] > done[0]["t0"]
    # between tasks, tab 1 shows the LAST FINISHED task in the same depth
    lines = build("task", snap, [], False)
    assert any("(between tasks)" in l for l in lines)
    assert any("LAST FINISHED: llm: generating" in l for l in lines)
    # the history table lives in the TIMING tab now, headlines threaded through
    lines = build("timing", snap, [], False)
    assert any("FAIL" in l and "tool:champsim" in l and "timeout" in l for l in lines)
    assert any("── stage1 ──" in l for l in lines)
    assert any("llm: generating" in l and "ok" in l for l in lines)
    # and huge param values are bounded at the bus edge
    tid3 = bus.task_start("t", params={"prompt": "x" * 9000})
    row = bus.snapshot()["tasks"][-1]
    assert len(row["params"]["prompt"]) < 4100 and "…(+" in row["params"]["prompt"]
    bus.task_end(tid3)


def test_profile_phases_become_task_rows_through_the_listener():
    """The auto-instrumentation seam: any `flux_profile.phase()` in any loop shows up
    as a task row when the TUI's listener is attached -- with the phase's why and
    params, and FAIL status when the block raised."""
    import flux_profile
    from flux_tui.app import _PhaseTasks

    bus = EventBus()
    flux_profile.set_listener(_PhaseTasks(bus))
    try:
        with flux_profile.phase("tool:yosys", why="hash block", top="iconflict_hash"):
            pass
        try:
            with flux_profile.phase("tool:openroad", why="placement"):
                raise RuntimeError("die too small")
        except RuntimeError:
            pass
        flux_profile.mark("confirm")
    finally:
        flux_profile.clear_listener()
    tasks = bus.snapshot()["tasks"]
    by_name = {t["name"]: t for t in tasks}
    assert by_name["tool:yosys"]["ok"] is True
    assert by_name["tool:yosys"]["params"] == {"top": "iconflict_hash"}
    assert by_name["tool:openroad"]["ok"] is False
    assert by_name["confirm"]["kind"] == "mark"
    # and a listener that raises never breaks the phase itself
    class Bad:
        def phase_start(self, *a):
            raise ValueError("broken listener")
    flux_profile.set_listener(Bad())
    try:
        with flux_profile.phase("still-times"):
            pass
    finally:
        flux_profile.clear_listener()
    assert flux_profile.seconds("still-times") >= 0


def test_on_result_lands_the_report_in_the_results_tab():
    """D391 follow-up: finishing must not lose the report -- run_tui's worker renders
    the returned result into the results tab via on_result. Tested at the seam the
    worker uses (curses itself stays hand-verified)."""
    bus = EventBus()
    result = {"decision": "S5+hier", "speedup": 1.0703}

    def on_result(r):
        return [f"decision: {r['decision']}", f"speedup: {r['speedup']:.4f}"]

    # the worker's rendering contract, inlined
    rendered = on_result(result)
    for line in rendered:
        bus.result(line)
    bus.done()
    snap = bus.snapshot()
    lines = build("results", snap, [], False)
    assert any("decision: S5+hier" in l for l in lines)
    assert any("speedup: 1.0703" in l for l in lines)
    assert snap["finished"]


def test_restart_arms_another_pass_and_keeps_history():
    """The rerun key's contract: restart() un-finishes the bus, marks the new pass,
    and loses nothing -- log, tasks, results all survive, because the point of
    rerunning in place is comparing against the last pass."""
    bus = EventBus()
    bus.log("pass one output")
    bus.result("decision: A")
    bus.done()
    assert bus.snapshot()["finished"]
    bus.restart(2)
    snap = bus.snapshot()
    assert not snap["finished"] and snap["error"] is None
    assert "pass one output" in snap["log"]
    assert "decision: A" in snap["results"]
    assert snap["tasks"][-1]["name"] == "rerun #2"
    assert snap["tasks"][-1]["kind"] == "mark"


def test_results_panel_is_tidied_not_bloated():
    """Blank-run collapse: a report captured from printf spacing renders compactly."""
    bus = EventBus()
    for line in ["", "== report ==", "", "", "", "decision: A", "", "speedup: 1.07",
                 "", "", ""]:
        bus.result(line)
    lines = build("results", bus.snapshot(), [], False)
    assert lines[0] == "== report =="
    assert lines[-1] == "speedup: 1.07"
    assert all(not (a == "" and b == "") for a, b in zip(lines, lines[1:]))


def test_timing_percentages_use_the_active_clock_not_idle_wall_time():
    """The r-key's second bite: flux_profile's own window keeps running while the
    operator reads results, so the timing panel passes the bus's active clock as the
    denominator, and a real gap shows as an explicit unattributed line."""
    import flux_profile

    flux_profile.reset()
    flux_profile.record("solver:z3", 8.0)
    lines = flux_profile.report_lines(total_s=10.0)
    assert any("ELAPSED (active run)" in l and "10.0" in l for l in lines)
    assert any("solver:z3" in l and "80.0%" in l for l in lines)
    assert any("unattributed" in l and "20.0%" in l for l in lines)
    lines = flux_profile.report_lines(total_s=100.0)
    assert any("unattributed" in l and "92.0%" in l for l in lines)
    flux_profile.reset()


def test_think_override_cycles_and_reaches_the_client():
    """The t key's seam: None defers to the proposer's own setting, True/False win
    for future calls, and the cycle returns to default."""
    from flux_llm import set_think_override, think_override
    from flux_llm.ollama_native import NativeOllamaProposer

    p = NativeOllamaProposer(model="x", think=False)
    try:
        assert think_override() is None
        set_think_override(True)
        assert think_override() is True     # would send think=true despite p.think
        set_think_override(False)
        assert think_override() is False
        set_think_override(None)
        assert think_override() is None
    finally:
        set_think_override(None)
    assert p.think is False                  # the proposer's own setting is untouched


def test_line_editor_edits_and_submits():
    ed = LineEditor()
    for ch in "hi x":
        assert ed.handle(ord(ch)) is None
    assert ed.handle(127) is None and ed.buffer == "hi "
    assert ed.handle(ord("!")) is None
    assert ed.handle(10) == "hi !" and ed.buffer == ""
    assert ed.handle(10) is None  # empty submit is not a note


def test_tui_feedback_is_channel_shaped():
    fb = TuiFeedback()
    fb.start()
    assert fb.active and fb.drain() == []
    fb.submit("prefer smaller tables")
    notes = fb.drain()
    assert [n.text for n in notes] == ["prefer smaller tables"]
    assert fb.drain() == [] and len(fb.notes) == 1  # notes kept for the panel
    fb.close()
    assert not fb.active


def test_panels_render_every_tab_from_a_snapshot():
    bus = EventBus()
    bus.task("measuring")
    bus.measure({"pair": "S5+hier", "latC": 14.9, "thruD": 12.8})
    bus.result("front: S5+hier")
    bus.log("some output")
    snap = bus.snapshot()
    fb = TuiFeedback()
    fb.submit("note one")
    for name in PANELS.values():
        lines = build(name, snap, fb.notes, feedback_enabled=True,
                      info={"db": "demo.db", "seed": 3})
        assert lines and all(isinstance(l, str) for l in lines), name
    # tab 6 is the run's identity now, help folded to a key line at its tail
    info_lines = build("info", snap, fb.notes, True, {"db": "demo.db", "seed": 3})
    assert any("db" in l and "demo.db" in l for l in info_lines)
    assert any("seed" in l and "3" in l for l in info_lines)
    assert any("keys:" in l for l in info_lines)
    assert "info" in PANELS.values() and "help" not in PANELS.values()
    assert any("measuring" in l for l in build("task", snap, [], False))
    assert any("S5+hier" in l for l in build("results", snap, [], False))
    assert any("note one" in l for l in build("feedback", snap, fb.notes, True))
    assert any("disabled" in l for l in build("feedback", snap, [], False))


def test_draw_calls_survive_curses_errors_but_input_passes_through():
    """D405: a resize mid-frame raises curses.error inside a draw call; the frame is
    disposable and must not unwind the TUI (observed live: the prefetcher TUI exited
    on its own and the abandonment was blamed on the user)."""
    import curses

    from flux_tui.app import _Screen

    class Raw:
        def addnstr(self, *a):
            raise curses.error("write past the edge")

        def hline(self, *a):
            raise curses.error("no such row")

        def getch(self):
            return 113

        def getmaxyx(self):
            return (5, 20)

    scr = _Screen(Raw())
    assert scr.addnstr(0, 0, "x", 5) is None     # swallowed: the frame is disposable
    assert scr.hline(2, 0, 0, 19) is None
    assert scr.getch() == 113                    # input is never swallowed
    assert scr.getmaxyx() == (5, 20)
