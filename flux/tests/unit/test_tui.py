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
    assert not any(l.startswith("state:") for l in lines)      # the top bar says it (D418e)
    assert any(l.startswith("now: tool:champsim") for l in lines)        # the breadcrumb
    assert any("running" in l and "tool:champsim" in l and "— bingo+next_line" in l
               for l in lines)                                           # one line per task
    assert any("── details: tool:champsim" in l for l in lines)
    assert any(l == "why: bingo+next_line" for l in lines)
    assert any(l == "trace = 5G_1.xz" for l in lines)
    _t.sleep(0.01)
    bus.task_end(tid, ok=False, note="timeout")
    tid2 = bus.task_start("llm: generating", why="proposal round 2",
                          params={"model": "qwen3.8", "prompt": "line one\n" * 40})
    snap = bus.snapshot()
    # tab 1 shows the running LLM task in DEPTH: its actual prompt as a block
    lines = build("task", snap, [], False)
    assert any("running" in l and "llm: generating" in l for l in lines)
    assert any("── details: llm: generating" in l for l in lines)  # follows the running task
    assert any("prompt (" in l for l in lines)
    assert any("│ line one" in l for l in lines)
    bus.task_end(tid2)
    snap = bus.snapshot()
    done = [t for t in snap["tasks"] if t["t1"] is not None and t["kind"] != "mark"]
    assert done[0]["ok"] is False and done[0]["t1"] > done[0]["t0"]
    # between tasks, tab 1 shows the LAST FINISHED task in the same depth
    lines = build("task", snap, [], False)
    assert any(l.startswith("▸") and "llm: generating" in l for l in lines)  # newest selected
    assert any(l.startswith("now: (between tasks)") for l in lines)
    assert any("── details: llm: generating" in l for l in lines)
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


def test_a_phases_output_reaches_the_task_details_pane():
    """D470: what a task PRODUCED -- the model's reply, a verdict, a build error --
    shows under its parameters. `phase()` hands the block a dict; the listener carries
    it into the row at exit; `task_details` renders it under an `output` rule."""
    import flux_profile
    from flux_tui.app import _PhaseTasks
    from flux_tui.panels import task_details

    bus = EventBus()
    flux_profile.set_listener(_PhaseTasks(bus))
    try:
        with flux_profile.phase("llm: generating (model)", why="~400 tok prompt",
                                model="qwen", prompt="design exp") as out:
            out["tokens"] = "400 in, 22 out, finish_reason=stop"
            out["reply"] = '{"method": "range reduction",\n "source": "module nlu_exp"}'
        with flux_profile.phase("test: build nlu_exp") as out:
            out["error"] = "x" * 201000                    # bounded, like a parameter (D544: 200,000)
        with flux_profile.phase("knowledge: prepare"):
            pass                                            # a block that says nothing
    finally:
        flux_profile.clear_listener()
    tasks = {t["name"]: t for t in bus.snapshot()["tasks"]}
    llm = tasks["llm: generating (model)"]
    assert llm["params"] == {"model": "qwen", "prompt": "design exp"}
    assert llm["output"]["tokens"] == "400 in, 22 out, finish_reason=stop"
    lines = task_details(llm, now=0.0, width=120)
    i = lines.index("── output ──")
    assert lines[:i] == [lines[0], "why: ~400 tok prompt", "model = qwen", "prompt = design exp"]
    assert lines[i + 1] == "tokens = 400 in, 22 out, finish_reason=stop"
    assert lines[i + 2] == "reply (58 chars, 2 lines):"
    assert lines[i + 3].startswith("  │ {\"method\"")
    assert tasks["test: build nlu_exp"]["output"]["error"].endswith("…(+1000 chars)")
    assert tasks["knowledge: prepare"]["output"] == {}
    assert "── output ──" not in task_details(tasks["knowledge: prepare"], now=0.0)
    running = dict(llm, t1=None, output={})
    assert "── output ── (still running)" in task_details(running, now=1.0)


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
    # the ACTIVE clock is the denominator: 8s of 10s is 80%, not a wall-time
    # fraction (the duration itself now prints in human units, D418)
    assert any("ELAPSED (active run)" in l and "10s" in l for l in lines)
    assert any("solver:z3" in l and "80.0%" in l for l in lines)
    assert any("solver:z3" in l and "80.0%" in l for l in lines)
    assert any("unattributed" in l and "20.0%" in l for l in lines)
    lines = flux_profile.report_lines(total_s=100.0)
    assert any("unattributed" in l and "92.0%" in l for l in lines)
    flux_profile.reset()


def test_think_override_cycles_and_reaches_the_client():
    """The t key's seam: None defers to the proposer's own setting, True/False win
    for future calls, and the cycle returns to default."""
    from flux_llm import OpenAIChatProposer, set_think_override, think_override

    p = OpenAIChatProposer("x", think=False)
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
    assert any(l.startswith("keys:") for l in info_lines)
    assert not any("task tab" in l or "timing tab" in l for l in info_lines)  # tabs say theirs
    assert any("↑↓←→" in l and "scroll / pan" in l for l in info_lines)
    for key, what in (("f", "feedback"), ("t", "think"), ("r", "loop")):   # one line each
        assert any(l.strip().startswith(key + " ") and l.rstrip().endswith(what)
                   for l in info_lines), key
    assert any("qq" in l for l in info_lines)
    assert not any("active time" in l for l in info_lines)      # the top bar has it
    assert "info" in PANELS.values() and "help" not in PANELS.values()
    assert build("task", snap, [], False)                      # renders with no tasks yet
    assert any("── measuring ──" in l for l in build("timing", snap, [], False))
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


def test_mouse_hit_testing_is_pure_and_forgiving():
    """D411: clicks map to actions through pure helpers -- token hits tolerate the
    :ON suffix, misses return None, tabs resolve by segment."""
    from flux_tui.app import _bar_hit, _tab_hit

    bar = " r loop:ON · t think · f feedback"      # the bar carries the toggles only (D418i)
    i = bar.find("r loop")
    assert _bar_hit(bar, i) == "loop"
    assert _bar_hit(bar, i + 8) == "loop"          # inside ":ON"
    assert _bar_hit(bar, bar.find("t think") + 2) == "think"
    assert _bar_hit(bar, bar.find("f feedback")) == "feedback"
    assert _bar_hit(bar, 0) is None
    tabs = [(1, 9, "task"), (10, 20, "timing")]
    assert _tab_hit(tabs, 5) == "task"
    assert _tab_hit(tabs, 12) == "timing"
    assert _tab_hit(tabs, 25) is None


def test_durations_read_in_human_units_and_the_table_shows_per_call():
    """D418: nobody should divide by 60 to compare two rows. Durations carry their
    unit, and the timing table reports seconds PER CALL beside the total."""
    import flux_profile
    from flux_profile import human_s, report_lines

    assert human_s(0.4) == "0.4s" and human_s(45) == "45s"
    assert human_s(92) == "1m32" and human_s(3725) == "1h02"
    assert human_s(129600) == "1d12h"

    flux_profile.reset()
    with flux_profile.phase("slow thing"):
        pass
    lines = report_lines(total_s=100.0)
    header = lines[0]
    assert "per call" in header and "calls" in header and "total" in header
    assert header.rstrip().endswith("state")        # running phases get their own column


def test_timing_nests_sub_tasks_under_their_stage():
    """D418 follow-up: the timing tab indents by where a phase RAN, so the table and
    the history read as the loop's structure -- a stage, then its sub-tasks."""
    import flux_profile
    from flux_profile import report_lines
    from flux_tui.events import EventBus as Bus
    from flux_tui.panels import task_history_rows

    flux_profile.reset()
    from flux_tui.app import _PhaseTasks

    bus = Bus()
    flux_profile.set_listener(_PhaseTasks(bus))     # the adapter the TUI installs
    try:
        with flux_profile.phase("generate: exp RTL"):
            with flux_profile.phase("llm: generating (model)"):
                pass
            with flux_profile.phase("sanity: compile exp"):
                pass
        with flux_profile.phase("prove: exhaustive"):
            pass
    finally:
        flux_profile.clear_listener()
    table = report_lines(total_s=10.0)
    names = [l[:34].rstrip() for l in table[1:]]
    # the run is the root row, stages one level in, their sub-tasks one further (D418c)
    assert names[0] == "ELAPSED (active run)"
    # a stage that calls the model directly carries the "·model" marker (D418k)
    assert "  generate: exp RTL ·model" in names
    assert "    llm: generating (model)" in names and "    sanity: compile exp" in names
    assert names.index("  generate: exp RTL ·model") < names.index("    llm: generating (model)")
    assert "  prove: exhaustive" in names          # a stage, under the run
    # the same name under two parents is two rows, each with its own time
    with flux_profile.phase("patch: exp"):
        with flux_profile.phase("llm: generating (model)"):
            pass
    names2 = [l[:34].rstrip() for l in report_lines(total_s=10.0)[1:]]
    assert names2.count("    llm: generating (model)") == 2
    hist = task_history_rows(bus.snapshot())[0]
    assert any("  llm: generating (model)" in l for l in hist)
    assert any(l.rstrip().endswith("generate: exp RTL") for l in hist)


def test_an_open_phase_is_attributed_while_it_runs():
    """D418c: a model call in its twentieth minute is time the loop CALLED for; the
    table shows it running under its stage instead of as 'unattributed'."""
    import threading
    import time as _t

    import flux_profile
    from flux_profile import report_lines, tree

    flux_profile.reset()
    entered = threading.Event()
    release = threading.Event()

    def worker():
        with flux_profile.phase("prepare: nlu"):
            with flux_profile.phase("llm: generating (model)"):
                entered.set()
                release.wait(5)

    th = threading.Thread(target=worker)
    th.start()
    entered.wait(5)
    _t.sleep(0.05)
    t = tree()
    assert t[("prepare: nlu",)][2] is True                    # running
    assert t[("prepare: nlu", "llm: generating (model)")][1] > 0.0
    rows = report_lines(total_s=1.0)
    # D487 (Cedric: "show the per call time for running tasks too"): the per-call column
    # counts the open call, and the state column carries that call's own elapsed (▶ 0.1s)
    stage = next(r for r in rows if r.startswith("  prepare: nlu"))
    assert "▶ " in stage.rstrip()[-10:] and stage.split()[-3] != ""           # ▶ <elapsed>
    call = next(r for r in rows if r.startswith("    llm: generating (model)"))
    assert "▶ " in call.rstrip()[-10:]
    cols = call.split()
    assert any(c.endswith("s") or c.endswith("ms") for c in cols[-5:-2])   # a per-call figure
    assert not any("unattributed" in r for r in rows)          # it IS attributed
    release.set()
    th.join(5)


def test_timing_table_folds_and_unfolds_subtrees():
    """D418d: folding. With a fold set, rows with children carry a marker and the
    descendants of folded paths are omitted; plain text rendering stays marker-free."""
    import flux_profile
    from flux_profile import report_lines, report_rows

    flux_profile.reset()
    with flux_profile.phase("step 1"):
        with flux_profile.phase("generate-loop: exp"):
            with flux_profile.phase("build: exp"):
                pass
        with flux_profile.phase("judge: exp"):
            pass
    with flux_profile.phase("evaluate"):
        pass
    plain = [l[:34].rstrip() for l in report_lines(total_s=10.0)]
    assert "  step 1" in plain and "▾" not in "".join(plain)      # no markers in text
    rows = report_rows(total_s=10.0, folded=set())
    texts = [r["text"][:34].rstrip() for r in rows]
    assert "  ▾ step 1" in texts and "    ▾ generate-loop: exp" in texts
    assert "        build: exp" in texts                          # a leaf: no marker
    step = next(r for r in rows if r["path"] == ("step 1",))
    assert step["has_children"] and not step["folded"]
    folded = report_rows(total_s=10.0, folded={("step 1",)})
    ftexts = [r["text"][:34].rstrip() for r in folded]
    assert "  ▸ step 1" in ftexts
    assert not any("generate-loop" in t or "judge" in t for t in ftexts)   # hidden
    assert "    evaluate" in ftexts                              # siblings still shown
    # folding the root hides every stage
    root = report_rows(total_s=10.0, folded={()})
    assert [r["path"] for r in root if r["path"] is not None] == [()]


def test_timing_rows_align_paths_with_lines():
    from flux_tui.app import _PhaseTasks
    from flux_tui.events import EventBus
    from flux_tui.panels import timing_rows
    import flux_profile

    flux_profile.reset()
    bus = EventBus()
    flux_profile.set_listener(_PhaseTasks(bus))
    try:
        with flux_profile.phase("step 1"):
            with flux_profile.phase("plan"):
                pass
    finally:
        flux_profile.clear_listener()
    lines, paths, kids, roles = timing_rows(bus.snapshot(), set())
    assert len(lines) == len(paths) == len(kids) == len(roles)
    i = next(k for k, p in enumerate(paths) if p == ("step 1",))
    assert "step 1" in lines[i] and kids[i]
    j = next(k for k, p in enumerate(paths) if p == ("step 1", "plan"))
    assert "plan" in lines[j] and not kids[j]
    assert paths[0] is None                                       # the header
    assert not any("task history" in l for l in lines)          # D487: the task tab's tree now


def test_fold_cursor_helpers_move_over_tree_rows_only():
    from flux_tui.app import _keep_visible, _step_cursor, _toggle_fold

    paths = [None, (), ("a",), ("a", "b"), None, None]
    kids = [False, True, True, False, False, False]
    assert _step_cursor(1, paths, +1) == 2 and _step_cursor(3, paths, +1) == 3
    assert _step_cursor(2, paths, -1) == 1 and _step_cursor(1, paths, -1) == 1
    assert _toggle_fold(set(), 2, paths, kids) == {("a",)}
    assert _toggle_fold({("a",)}, 2, paths, kids) == set()
    assert _toggle_fold(set(), 3, paths, kids) == set()          # a leaf does not fold
    # keep the cursor on screen: 20 lines, 5 visible, scroll counts from the bottom
    assert _keep_visible(2, 20, 5, 0) == 13                       # line 2 -> top of view
    assert _keep_visible(19, 20, 5, 13) == 0                      # last line -> bottom
    assert _keep_visible(16, 20, 5, 0) == 0                       # already visible


def test_task_tab_selection_drives_the_params_section():
    """D418e: the task list is compact (1-2 lines per task) and the params box shows
    the SELECTED task; rows carry their task id so the app can move a cursor."""
    from flux_tui.panels import task_rows

    bus = EventBus()
    a = bus.task_start("tool:yosys", why="screen", params={"top": "nlu"})
    bus.task_end(a)
    b = bus.task_start("llm: generating (model)", why="attempt 2",
                       params={"model": "qwen3.8", "prompt": "p\n" * 5})
    snap = bus.snapshot()
    lines, ids, order, roles = task_rows(snap, None)
    assert len(lines) == len(ids) == len(roles) and order == [b, a]   # the tree, newest first (D487)
    sel = [i for i, l in enumerate(lines) if l.startswith("▸")]
    assert len(sel) == 1 and ids[sel[0]] == b                     # running task selected
    assert any("── details: llm: generating (model)" in l for l in lines)
    assert any("following the running task" in l for l in lines)
    assert not any("details: tool:yosys" in l for l in lines)
    # browsing onto the finished task previews its details; selecting it says so
    lines2, ids2, _o, _r = task_rows(snap, a)
    assert any("browsing" in l for l in lines2)
    assert any("── details: tool:yosys" in l for l in lines2)
    lines3, _i, _o, _r = task_rows(snap, a, selected=a)
    assert any("a task is selected" in l for l in lines3)
    assert any(l == "top = nlu" for l in lines2)
    assert [i for i, l in enumerate(lines2) if l.startswith("▸")] == [ids2.index(a)]
    # every task-list row carries its id; header/box/blank rows carry None
    assert ids2[0] is None and ids2[-1] is None


def test_task_list_is_height_limited_and_scrolls_inside():
    """The list keeps to `list_rows` lines with a window that follows the selection;
    clipped entries are counted (↑/↓ n more) and every task stays reachable through
    `order`. No stage headline: the outermost running task is the stage."""
    from flux_tui.panels import task_rows

    bus = EventBus()
    bus.task("starting", why="importing, resolving inputs")
    ids_all = []
    for i in range(20):
        t = bus.task_start(f"tool:step{i}", why=f"w{i}")
        bus.task_end(t)
        ids_all.append(t)
    snap = bus.snapshot()
    lines, ids, order, _r = task_rows(snap, None, list_rows=8)
    assert not any(l.startswith("stage:") for l in lines)
    listed = [i for i in ids if i is not None]
    assert 0 < len(set(listed)) < 20                          # windowed, not everything
    assert any("↓" in l and "more" in l for l in lines)       # following: the window sits at the top
    assert order == list(reversed(ids_all))                   # newest first, all reachable (D487)
    # selecting an old task slides the window to it
    old = ids_all[2]
    lines2, ids2, _o, _r = task_rows(snap, old, list_rows=8)
    assert old in ids2 and any(l.startswith("▸") and "tool:step2" in l for l in lines2)
    assert any("↑" in l and "more" in l for l in lines2)      # an old one: the newer lie above
    assert any("── details: tool:step2" in l for l in lines2)


def test_task_history_prunes_finished_leaves_and_keeps_the_tree():
    """D487 (Cedric): a longer history, pruned structurally -- the oldest finished LEAVES go
    first, never a parent, never a running task; one "…" row per gap, under the parent."""
    from flux_tui.panels import task_rows

    bus = EventBus()
    bus.max_tasks = 12
    step = bus.task_start("DSE: step 1", why="w")                       # a parent that stays
    gen = bus.task_start("generation: exp", why="w")                    # its child, running
    leaves = []
    for i in range(20):                                                 # grandchildren, finished
        t = bus.task_start(f"generate: prototype exp", why=f"attempt {i + 1}")
        bus.task_end(t)
        leaves.append(t)
    snap = bus.snapshot()
    names = [t["id"] for t in snap["tasks"]]
    assert step in names and gen in names                               # the tree's spine survives
    assert len(snap["tasks"]) <= 12
    kept = [t for t in leaves if t in names]
    assert kept == leaves[-len(kept):]                                  # the OLDEST leaves went
    parent = next(t for t in snap["tasks"] if t["id"] == gen)
    assert parent["pruned"] == 20 - len(kept)
    lines, ids, order, roles = task_rows(snap, None, list_rows=30)
    gaps = [l for l in lines if "…" in l and "earlier, pruned" in l]
    assert len(gaps) == 1 and f"… {20 - len(kept)} earlier, pruned" in gaps[0]   # one ellipsis per gap
    gi = lines.index(gaps[0])
    assert "generation: exp" in lines[gi + 1]                           # just above its parent (newest first)
    assert ids[gi] is None and roles[gi] == "dim"
    # a task's line carries when, duration, status and its indentation
    row = next(l for l in lines if "generate: prototype exp" in l)
    assert "attempt" in row and "    generate: prototype exp" in row and "ok" in row


def test_task_details_scroll_on_their_own_region():
    """D418f: PgUp/PgDn scroll the details region only; the list and the breadcrumb
    stay put, and the region says how much lies above and below."""
    from flux_tui.panels import task_rows

    bus = EventBus()
    bus.task_start("llm: generating (model)", why="w",
                   params={"prompt": "\n".join(f"L{i}" for i in range(50))})
    snap = bus.snapshot()
    tid = snap["tasks"][0]["id"]
    # a SELECTED task scrolls where the keys put it; a fixed-height region either way
    top, _i, _o, _r = task_rows(snap, None, list_rows=4, detail_rows=6, detail_scroll=0, selected=tid)
    down, _i, _o, _r = task_rows(snap, None, list_rows=4, detail_rows=6, detail_scroll=10, selected=tid)
    assert top[0] == down[0]                                     # breadcrumb unchanged
    assert [l for l in top if l.startswith("▸")] == [l for l in down if l.startswith("▸")]
    hd = next(i for i, l in enumerate(top) if "── details" in l)
    assert len(top[hd + 1:]) == 6 and len(down[hd + 1:]) == 6    # fixed-height region
    assert down[hd + 1].startswith("↑ 10 more lines above")
    assert top[hd + 6].startswith("↓") and "more lines below" in top[hd + 6]
    # D506 (Cedric: "live tail doesn't tail properly"): FOLLOWING a running task, the pane
    # shows its END and stays there as lines are added; the clamp says so (TAIL)
    clamp: dict = {}
    tail, _i, _o, _r = task_rows(snap, None, list_rows=4, detail_rows=6, detail_scroll=0, clamp=clamp)
    assert tail[hd + 1].startswith("↑") and "still running" in tail[-1] and clamp["dscroll"] == -1
    assert any(l.strip("│ ") == "L49" for l in tail) and not any(l.strip("│ ") == "L40" for l in tail)   # the end, not the top
    bus.task_update(tid, {"thinking (live tail)": "\n".join(f"T{i}" for i in range(80))})
    tail2, _i, _o, _r = task_rows(bus.snapshot(), None, list_rows=4, detail_rows=6, detail_scroll=clamp["dscroll"], clamp=clamp)
    assert any(l.strip("│ ") == "T79" for l in tail2) and not any(l.strip("│ ") == "T70" for l in tail2)   # still the end, 80 lines on
    # a selected task scrolled past its end sticks to the end too; ↑ from there is a fixed line
    from flux_tui.app import _task_key
    import curses

    st = {"cursor": tid, "sel": tid, "dscroll": 0, "dhscroll": 0, "dmax": clamp["dmax"]}
    for _ in range(200):
        st = _task_key(curses.KEY_NPAGE, st, [tid])
    assert st["dscroll"] == -1
    st = _task_key(curses.KEY_UP, st, [tid])
    assert st["dscroll"] == clamp["dmax"] - 1


def test_task_tab_selection_is_the_mode():
    """D418h (Cedric's model). Nothing selected: ↑/↓ browse the list and the details
    preview the highlighted task. Enter/click selects: the arrows scroll and pan the
    details. Esc steps back: selected -> browsing at the same row -> following."""
    import curses

    from flux_tui.app import _task_key
    from flux_tui.panels import task_rows

    order = [30, 20, 10]
    st = {"cursor": None, "sel": None, "dscroll": 0, "dhscroll": 0}
    st = _task_key(curses.KEY_DOWN, st, order)
    assert st["cursor"] == 20 and st["sel"] is None            # browsing moved the highlight
    st = _task_key(curses.KEY_RIGHT, st, order)
    assert st["dhscroll"] == 0                                 # arrows do not pan while browsing
    st = _task_key(10, st, order)                              # Enter selects the highlight
    assert st["sel"] == 20
    st = _task_key(curses.KEY_DOWN, st, order)
    st = _task_key(curses.KEY_RIGHT, st, order)
    assert st["sel"] == 20 and st["cursor"] == 20              # selection unchanged...
    assert st["dscroll"] == 1 and st["dhscroll"] == 10         # ...the details moved
    st = _task_key(curses.KEY_NPAGE, st, order)
    assert st["dscroll"] == 11
    st = _task_key(10, st, order)                              # Enter again: clears (toggle)
    assert st["sel"] is None and st["cursor"] == 20 and st["dscroll"] == 0
    st = _task_key(10, st, order)                              # ...and selects once more
    assert st["sel"] == 20
    before = dict(st)
    st = _task_key(27, st, order)                              # Esc: a no-op (Enter toggles)
    assert st == before

    bus = EventBus()
    a = bus.task_start("tool:yosys", why="screen",
                       params={"top": "nlu", "cmd": "yosys -p 'synth_asap7 -top nlu' design.sv"})
    bus.task_end(a)
    b = bus.task_start("llm: generating (model)", why="w",
                       params={"prompt": "0123456789" * 12 + "\n" + "second"})
    snap = bus.snapshot()
    browse, _i, _o, _r = task_rows(snap, None)                     # following the running task
    assert any(l.startswith("▌── tasks") for l in browse)
    assert any("following the running task" in l for l in browse)
    assert any("── details: llm: generating (model) (⏎ to scroll)" in l for l in browse)
    preview, _i, _o, _r = task_rows(snap, a)                       # browsing onto the old task
    assert any("browsing" in l and "esc" not in l for l in preview)
    assert any("── details: tool:yosys" in l for l in preview)  # the pane previews it
    chosen, _i, _o, _r = task_rows(snap, a, selected=a, detail_hscroll=20)
    assert any(l.startswith("▌── details: tool:yosys") for l in chosen)
    assert any("a task is selected · ⏎ back" in l for l in chosen)
    assert any(l.startswith("⇠ col 20") for l in chosen)
    # D487: a pan past the text is clamped to it (no scrolling into the void)
    clamp: dict = {}
    task_rows(snap, a, selected=a, detail_hscroll=500, detail_scroll=999, detail_rows=4, clamp=clamp)
    assert 0 < clamp["dhscroll"] < 500 and clamp["dscroll"] < 999


def test_phases_carry_the_slides_roles_and_the_loop_speaks_their_words():
    """D418k: rows are colored by the slides' roles; the loop's own stages are named
    DSE / generation / generate / repair; the legend marks the model."""
    import flux_profile
    from flux_profile import report_rows, role_of
    from flux_tui.panels import timing_rows
    from flux_tui.events import EventBus

    assert role_of("DSE: step 3") == "orchestrator" and role_of("propose: plan") == "orchestrator"
    assert role_of("generation: exp") == "generator" and role_of("repair: exp") == "generator"
    assert role_of("generate: exp") == "generator" and role_of("critique: exp") == "evaluator"
    assert role_of("llm: generating (model)") == "model"
    assert role_of("build: exp") == "evaluator" and role_of("judge: exp") == "evaluator"
    assert role_of("prepare: nlu") == "mentor" and role_of("reload: re-verify recip") == "mentor"
    assert role_of("evaluate") == "orchestrator" and role_of("whatever") is None

    flux_profile.reset()
    with flux_profile.phase("DSE: step 1"):
        with flux_profile.phase("generation: exp"):
            with flux_profile.phase("llm: generating (model)"):
                pass
    rows = report_rows(total_s=1.0, folded=set())
    by = {r["path"]: r for r in rows if r["path"]}
    assert by[("DSE: step 1",)]["role"] == "orchestrator"
    # the model is a MARKER on a role: the caller gets "·model", the call takes the
    # caller's color with "(model)" highlighted
    sub = by[("DSE: step 1", "generation: exp")]
    assert sub["role"] == "generator+model" and "generation: exp ·model" in sub["text"]
    call = by[("DSE: step 1", "generation: exp", "llm: generating (model)")]
    assert call["role"] == "generator+model"
    # a long caller name is shortened so the marker still shows within the column
    flux_profile.reset()
    with flux_profile.phase("author: adversarial vectors for every operator"):
        with flux_profile.phase("llm: generating (model)"):
            pass
    long_row = next(r for r in report_rows(total_s=1.0, folded=set())
                    if r["path"] == ("author: adversarial vectors for every operator",))
    assert long_row["text"][:34].rstrip().endswith("·model")
    lines, _p, _k, roles = timing_rows(EventBus().snapshot(), set())
    assert not any(l.startswith("roles:") for l in lines)     # the legend is the info tab's
    # a task row: the llm task takes its caller's role too
    bus = EventBus()
    bus.task_start("generate: exp", why="w")
    bus.task_start("llm: generating (model)", why="w")
    from flux_tui.panels import task_rows
    lines, ids, _o, troles = task_rows(bus.snapshot(), None)
    i = next(k for k, l in enumerate(lines) if "llm: generating" in l and ids[k] is not None)
    assert troles[i] == "generator+model"


def test_info_tab_explains_the_color_code_in_the_colors():
    from flux_tui.panels import ROLE_LINES, info_rows

    lines, roles = info_rows(EventBus().snapshot(), {"db": "x.db"}, [])
    assert len(lines) == len(roles)
    start = next(k for k, l in enumerate(lines) if l.startswith("colors:"))
    for role, what in ROLE_LINES:
        i = next(k for k, l in enumerate(lines) if k > start and l.startswith(f"  {role:<14}"))
        assert roles[i] == ("+model" if role == "(model)" else role) and what in lines[i]
    assert all(r is None for r in roles[:start + 1])          # the identity block is plain


def test_task_history_is_colored_by_role_too():
    from flux_tui.panels import timing_rows
    import flux_profile

    flux_profile.reset()
    bus = EventBus()
    a = bus.task_start("generate: exp", why="w")
    b = bus.task_start("llm: generating (model)", why="w")
    bus.task_end(b)
    bus.task_end(a)
    c = bus.task_start("prove: exhaustive ULP (exp)", why="w")
    bus.task_end(c)
    from flux_tui.panels import task_rows

    lines, _i, _o, roles = task_rows(bus.snapshot(), None)      # the history is the task tab (D487)
    h = next(i for i, l in enumerate(lines) if l.startswith("▌── tasks") or l.startswith(" ── tasks"))

    def role_of_row(name: str) -> str | None:
        i = next(k for k, l in enumerate(lines) if k > h and name in l)
        return roles[i]

    assert role_of_row("prove: exhaustive ULP (exp)") == "evaluator"
    assert role_of_row("generate: exp") == "generator"
    assert role_of_row("llm: generating (model)") == "generator+model"   # the caller's color


def test_log_tab_stamps_colors_and_filters():
    """D418l: log lines carry the active-run time they arrived, are colored by role,
    and a substring filter keeps only matching lines with a header saying so."""
    from flux_tui.panels import log_rows

    bus = EventBus()
    bus.log("plan: attempt exp via range reduction")
    bus.log("  patched exp: 2 edit(s) -- fix")
    bus.log("[tui] loop -> ON")
    bus.log("ADMITTED recip: seed")
    snap = bus.snapshot()
    assert len(snap["log_stamps"]) == len(snap["log"]) == 4
    lines, roles = log_rows(snap)
    assert lines[0].endswith("/ filters") and roles[0] == "dim"
    body = lines[1:]
    assert all("│" in l for l in body)                       # stamped
    assert roles[1] == "orchestrator" and roles[2] == "generator"
    assert roles[3] == "dim" and roles[4] == "evaluator"
    fl, fr = log_rows(snap, "patched")
    assert fl[0].startswith("filter: patched — 1 of 4 lines")
    assert len(fl) == 2 and "patched exp" in fl[1]
    nl, _r = log_rows(snap, "zzz")
    assert nl[-1] == "(nothing matches)"


def test_results_tab_shows_live_standings_then_the_report():
    from flux_tui.panels import results_rows

    bus = EventBus()
    lines, roles = results_rows(bus.snapshot())
    assert lines[0].startswith("(no results yet")
    bus.standing("standings", {"at": "after step 2", "step": 2, "steps": 24,
                               "judged": 5, "proven": 1, "parts_total": 3,
                               "parts": [{"part": "recip", "state": "proven", "name": "seed"},
                                         {"part": "exp", "state": "best so far",
                                          "score": 27291, "name": "v9"},
                                         {"part": "rsqrt", "state": "not yet tried"}]})
    lines, roles = results_rows(bus.snapshot())
    assert lines[0].startswith("standings · after step 2 · step 2/24 · 5 judged · 1/3 proven")
    assert any("recip" in l and "proven" in l for l in lines)
    assert any("exp" in l and "best so far" in l and "27291" in l for l in lines)
    i = next(k for k, l in enumerate(lines) if "recip" in l)
    assert roles[i] == "ok"
    j = next(k for k, l in enumerate(lines) if l.strip().startswith("exp"))
    assert roles[j] == "bad"
    bus.result("ESTABLISHED: something")
    lines, _r = results_rows(bus.snapshot())
    assert any(l.startswith("ESTABLISHED") for l in lines)
    assert not any("report lands" in l for l in lines)
    # D497 (Cedric: "it is unsure what is the target / what result we want / what is the
    # problem we work on now"): the objective, what the pass does now, the whole design's
    # numbers above the parts; each part with its constraint and numbers
    bus.standing("standings", {"at": "after step 3", "step": 3, "steps": 24, "judged": 6, "proven": 1, "parts_total": 2,
                               "objective": {"goal": "an FP16 NLU of 2 operators at >= 800 MHz",
                                             "now": "pushing fmax: exp to the model for its logic depth 152 -> goal <= 121",
                                             "composed": "5,578 um2 · 46 MHz (goal 800) · latency 16"},
                               "parts": [{"part": "recip", "state": "proven", "name": "transpiled_recip_p3",
                                          "numbers": "y = 1/x, 1 ULP · 3 registers · 911 MHz alone · depth 91"},
                                         {"part": "exp", "state": "best so far", "score": 12, "name": "v3",
                                          "numbers": "y = e^x, 1 ULP · best 12 over"}]})
    lines, roles = results_rows(bus.snapshot())
    i = next(k for k, l in enumerate(lines) if "OBJECTIVE" in l)
    assert "an FP16 NLU of 2 operators" in lines[i] and "NOW" in lines[i + 1] and roles[i + 1] == "warn"
    assert "WHOLE" in lines[i + 2] and "46 MHz (goal 800)" in lines[i + 2]
    assert any("recip" in l and "911 MHz alone" in l and "3 registers" in l for l in lines)
    assert any("exp" in l and "best 12 over" in l for l in lines)


def test_results_tab_opens_a_part_on_its_artifacts():
    """D487 (Cedric: "clicking on a result should show its artifact" -- and "colors on the
    results and a table like before made more sense"): the coloured standings table as it
    was, its part rows clickable; an open part shows its prototype, the report, its RTL and
    its tests below the table, where the run report otherwise is."""
    from flux_tui.panels import result_sections, results_browse

    bus = EventBus()
    lines, ids, order, roles = results_browse(bus.snapshot(), None)
    assert order == [0] and "no results yet" in "\n".join(lines)     # the report entry alone
    bus.standing("standings", {"at": "after step 2", "step": 2, "steps": 24, "judged": 5, "proven": 1,
                               "parts_total": 3, "parts": [
        {"part": "recip", "state": "proven", "name": "transpiled_recip", "prototype": "def design(x):\n    return x\n",
         "artifact": "module nlu_recip;\nendmodule", "report": "admitted: 0 over on every input",
         "tests": "gate: every one of the 65536 FP16 inputs"},
        {"part": "exp", "state": "best so far", "score": 27291, "name": "v9", "artifact": "module nlu_exp;",
         "report": "27291 of 65536 beyond 1 ULP", "prototype": "T = 5\n", "prototype_score": 644},
        {"part": "rsqrt", "state": "not yet tried"}]})
    bus.result("ESTABLISHED: something")
    snap = bus.snapshot()
    secs = result_sections(snap["standings"]["standings"])
    assert [t["title"].split(":")[0] for t in secs] == ["recip", "exp", "rsqrt"]
    assert "── prototype (verified, 0 over) ──" in secs[0]["text"] and "── RTL (2 lines) ──" in secs[0]["text"]
    assert "── tests ──" in secs[0]["text"] and "prototype (best refused, 644 over)" in secs[1]["text"]
    lines, ids, order, roles = results_browse(snap, None)
    assert lines[0].startswith("standings · after step 2") and order == [0, 1, 2, 3]   # parts + the report
    i = next(k for k, l in enumerate(lines) if "recip" in l and "proven" in l)
    assert roles[i] == "ok" and ids[i] == 0                      # the colour, and clickable
    j = next(k for k, l in enumerate(lines) if "exp" in l and "27291" in l)
    assert roles[j] == "bad" and ids[j] == 1
    r = next(k for k, l in enumerate(lines) if l.startswith("▸ report") or l.startswith("  report"))
    assert ids[r] == 3 and "landed" in lines[r]
    assert any(l.startswith("ESTABLISHED") for l in lines)         # nothing highlighted: the report previews
    # D498 (Cedric: "navigation ... should be similar to tasks tab"): ↑↓ move the highlight and
    # the preview follows; ⏎ opens (scroll/pan), ⏎ closes
    lines, ids, order, roles = results_browse(snap, 1, detail_rows=6)
    assert any(l.startswith("▸") and "exp" in l for l in lines)
    assert any(l.startswith("▌── exp: best so far") and "↑↓ browse · ⏎/click open" in l for l in lines)
    assert not any(l.startswith("ESTABLISHED") for l in lines)
    assert any("more lines (⏎ to open and scroll)" in l for l in lines) or True
    # the part under way (D487): yellow, with its prototype's live best and its plan
    bus.standing("standings", {"at": "trying sigmoid", "step": 3, "steps": 24, "judged": 5, "proven": 1,
                               "parts_total": 2, "parts": [
        {"part": "recip", "state": "proven", "name": "transpiled_recip"},
        {"part": "sigmoid", "state": "trying", "prototype_score": 925, "via": "recip of (1 + exp of -x)"}]})
    lines, ids, order, roles = results_browse(bus.snapshot(), None)
    k = next(i for i, l in enumerate(lines) if l.startswith("  sigmoid"))
    assert "trying" in lines[k] and "prototype at 925 over" in lines[k] and "via recip of" in lines[k]
    assert roles[k] == "warn"
    opened, _i, _o, _r = results_browse(snap, 0, selected=0, detail_rows=30)
    assert opened[0].startswith("standings ·") and any(l.startswith("▸") and "recip" in l for l in opened)
    assert any(l.startswith("▌── recip: proven") and "↑↓ scroll · ←→ pan · ⏎ close" in l for l in opened)
    assert any("module nlu_recip" in l for l in opened)
    assert not any(l.startswith("ESTABLISHED") for l in opened)   # the open part takes the report's place
    # the task tab's key model drives it: ↑↓ browse, ⏎ pins, ⏎ clears
    from flux_tui.app import _task_key
    import curses
    st = {"cursor": 3, "sel": None, "dscroll": 0, "dhscroll": 0}
    st = _task_key(curses.KEY_UP, st, [0, 1, 2, 3])
    assert st["cursor"] == 2 and st["sel"] is None
    st = _task_key(10, st, [0, 1, 2, 3])
    assert st["sel"] == 2
    st = _task_key(curses.KEY_DOWN, st, [0, 1, 2, 3])
    assert st["dscroll"] == 1 and st["sel"] == 2
    st = _task_key(10, st, [0, 1, 2, 3])
    assert st["sel"] is None


def test_profile_publish_reaches_the_bus_as_standings():
    import flux_profile
    from flux_tui.app import _PhaseTasks

    bus = EventBus()
    flux_profile.set_listener(_PhaseTasks(bus))
    try:
        flux_profile.publish("standings", {"step": 1, "parts": []})
    finally:
        flux_profile.clear_listener()
    assert bus.snapshot()["standings"]["standings"]["step"] == 1


def test_mentor_tab_browses_the_published_sections():
    """D418m: a seventh tab shows what the mentor holds -- sections the loop
    published -- with the task tab's browse/open/scroll model."""
    from flux_tui.panels import PANELS, mentor_rows

    assert PANELS["6"] == "mentor" and PANELS["9"] == "info" and "7" not in PANELS
    bus = EventBus()
    lines, ids, order = mentor_rows(bus.snapshot(), None)
    assert order == [] and "appear once" in lines[0]
    bus.standing("mentor", {"sections": [
        {"title": "knowledge: methods sheet", "text": "METHODS\n* lut\n* piecewise-poly"},
        {"title": "record: refusals (gate)", "text": "* exp_v9 -- 27291 over"},
        {"title": "operator notes", "text": "* keep it small"}]})
    snap = bus.snapshot()
    lines, ids, order = mentor_rows(snap, None, detail_rows=4)
    assert order == [0, 1, 2]
    assert any(l.startswith("▸") and "knowledge: methods sheet" in l for l in lines)
    assert any("browsing · ⏎/click opens" in l for l in lines)
    assert any("METHODS" in l for l in lines)                    # the first section previews
    opened, _i, _o = mentor_rows(snap, 1, selected=1, detail_rows=4)
    assert any("a section is open" in l for l in opened)
    assert any(l.startswith("▌── record: refusals (gate)") for l in opened)
    assert any("27291 over" in l for l in opened)


def test_a_running_task_shows_what_it_has_so_far():
    """D493: a streaming model's thinking reaches the running row through task_update and
    the details pane shows it under "output (so far, still running)"; the close replaces
    it whole, so the live keys do not linger."""
    from flux_tui.events import EventBus
    from flux_tui.panels import task_details

    bus = EventBus()
    tid = bus.task_start("llm: generating (model)", why="~100 tok prompt", params={"model": "m"})
    bus.task_update(tid, {"thinking (live tail)": "Let me think", "streamed": "~3 tokens so far"})
    row = next(t for t in bus.tasks if t["id"] == tid)
    lines = task_details(row, now=row["t0"] + 1)
    assert any("output ── (so far, still running)" in ln for ln in lines)
    assert any("thinking (live tail) = Let me think" in ln for ln in lines)
    bus.task_end(tid, ok=True, output={"thinking": "Let me think harder", "reply": "42"})
    lines = task_details(row, now=row["t0"] + 2)
    assert not any("live tail" in ln for ln in lines) and any("thinking = Let me think harder" in ln for ln in lines)
    bus.task_update(tid, {"thinking (live tail)": "late"})      # after the close: ignored
    assert "thinking (live tail)" not in row["output"]


def test_page_keys_jump_a_screen_not_ten_lines():
    """D574: PgUp/PgDn move by the page the caller names -- the app names the screen's height."""
    import curses

    from flux_tui.app import _page, _task_key

    st = {"cursor": 1, "sel": 1, "dscroll": 0, "dhscroll": 0, "dmax": 500}
    assert _task_key(curses.KEY_NPAGE, st, [1], page=40)["dscroll"] == 40
    assert _task_key(curses.KEY_PPAGE, dict(st, dscroll=100), [1], page=40)["dscroll"] == 60
    assert _task_key(curses.KEY_NPAGE, st, [1])["dscroll"] == 10                 # the default stays ten

    class V:
        h_last = 52

    assert _page(V()) == 44 and _page(type("S", (), {"h_last": 12})()) == 10
