"""The run's journal (D683): the live task tree as JSON lines another process can follow."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import flux_profile
from flux_loop.journal import Journal, attach, read_events


def test_the_journal_writes_the_tree_beside_the_tui(tmp_path):
    seen = []

    class Tui:
        def phase_start(self, name, why, params):
            seen.append(("start", name))
            return name

        def phase_end(self, token, name, seconds, failed, output):
            seen.append(("end", token))

    j = Journal(str(tmp_path / "events.jsonl"))
    flux_profile.set_listener(Tui())
    flux_profile.add_listener(j)
    try:
        with flux_profile.phase("pass", why="1") as out:
            with flux_profile.phase("gate", why="x"):
                flux_profile.progress(tail="a" * 10_000)
                flux_profile.progress(tail="ignored: under a second")
            out["decision"] = "d#1"
        flux_profile.publish("best", {"x": 1})

        def worker():
            with flux_profile.phase("in a thread"):
                pass

        t = threading.Thread(target=worker)
        t.start()
        t.join()
    finally:
        flux_profile.clear_listener()
        flux_profile.remove_listener(j)
    assert ("start", "pass") in seen and ("end", "gate") in seen, "the TUI still hears everything"
    events, offset = read_events(str(tmp_path / "events.jsonl"))
    kinds = [(e["ev"], e.get("name") or e.get("key")) for e in events]
    assert kinds == [("start", "pass"), ("start", "gate"), ("end", "gate"), ("end", "pass"),
                     ("start", "in a thread"), ("end", "in a thread")], "D761: the structure only, appended"
    start_gate = events[1]
    assert start_gate["parent"] == events[0]["id"] and events[4]["parent"] is None
    assert events[3]["output"] == {"decision": "d#1"}
    import time as _time

    _time.sleep(1.2)                                          # the flusher writes within a second
    live = json.loads((tmp_path / "live.json").read_text())
    assert live["publish"] == {"best": {"x": 1}} and live["updates"] == {}, "the latest only; an ended phase's fields gone"
    assert offset == (tmp_path / "events.jsonl").stat().st_size
    with open(tmp_path / "events.jsonl", "a") as fh:
        fh.write('{"ev": "mark", "name": "half')              # a line still being written
    more, off2 = read_events(str(tmp_path / "events.jsonl"), offset)
    assert more == [] and off2 == offset


def test_attach_once_per_run_and_a_new_run_replaces_it(tmp_path):
    a = attach(str(tmp_path / "a"))
    assert attach(str(tmp_path / "a")) is a
    b = attach(str(tmp_path / "b"))
    try:
        with flux_profile.phase("x"):
            pass
    finally:
        flux_profile.remove_listener(b)
    lines = [json.loads(ln) for ln in (Path(tmp_path) / "b" / "events.jsonl").read_text().splitlines()]
    assert lines[0]["ev"] == "hello" and [ln["ev"] for ln in lines[1:]] == ["start", "end"]
    assert [json.loads(ln)["ev"] for ln in (Path(tmp_path) / "a" / "events.jsonl").read_text().splitlines()] == ["hello"]


def test_a_workers_phases_sit_under_the_phase_that_handed_them_out_with_its_part(tmp_path):
    """D739: a stage's measurements on the pool's threads were roots with no parent; carried,
    they sit under the stage, and a part's tag reaches every phase of its work, threads too."""
    from flux_loop.pool import run_parallel

    j = Journal(str(tmp_path / "events.jsonl"))
    flux_profile.add_listener(j)
    try:
        with flux_profile.tagged(part="exp"), flux_profile.phase("simulation: bench"):
            def one(i):
                with flux_profile.phase("tool:python3", why=f"stage bench d{i}"):
                    return i

            assert [r for r, _e in run_parallel(range(3), one, 3)] == [0, 1, 2]
        with flux_profile.phase("decide"):
            pass
        t = threading.Thread(target=lambda: flux_profile.phase("alone").__enter__())   # nothing handed: no parent
        t.start()
        t.join()
    finally:
        flux_profile.remove_listener(j)
    starts = [e for e in read_events(str(tmp_path / "events.jsonl"))[0] if e["ev"] == "start"]
    stage = starts[0]
    tools = [e for e in starts if e["name"] == "tool:python3"]
    assert len(tools) == 3 and all(e["parent"] == stage["id"] for e in tools)
    assert stage["tags"]["part"] == "exp" and all(e["tags"]["part"] == "exp" for e in tools)
    decide = next(e for e in starts if e["name"] == "decide")
    assert decide["parent"] is None and "tags" not in decide, "the tag ends with its block"
    assert next(e for e in starts if e["name"] == "alone")["parent"] is None


def test_passes_mark_each_pass_and_why_the_run_ended(tmp_path):
    """D739: the live tree hangs its branches on `pass` marks and its end on `ended`."""
    from dataclasses import make_dataclass
    from types import SimpleNamespace

    from flux_loop.passes import run_passes

    request = make_dataclass("Request", [("passes", int, 0), ("explore", int, 0)])()
    j = Journal(str(tmp_path / "events.jsonl"))
    flux_profile.add_listener(j)
    try:
        run_passes(lambda _r, _f: SimpleNamespace(at_rest=False, explorable=True), request, passes=3, say=lambda _m: None)
    finally:
        flux_profile.remove_listener(j)
    marks = [(e["name"], json.loads(e["why"])) for e in read_events(str(tmp_path / "events.jsonl"))[0] if e["ev"] == "mark"]
    assert [m for m in marks if m[0] == "pass"] == [("pass", {"n": 1, "explore": 0}), ("pass", {"n": 2, "explore": 0}),
                                                   ("pass", {"n": 3, "explore": 0})]
    assert marks[-1] == ("ended", {"why": "3 passes done"})


def test_a_long_starts_last_passes_are_found_from_the_end(tmp_path):
    """D759: a day-long journal opens on its latest start's last passes, found reading backwards,
    with how many came before; a start shorter than the window opens whole."""
    from flux_loop.journal import window_start

    p = tmp_path / "events.jsonl"
    rows = [{"t": 0, "ev": "hello"}, {"t": 1, "ev": "mark", "name": "pass", "why": json.dumps({"n": 1})}]
    rows += [{"t": 2, "ev": "hello"}]                                     # a new start
    for n in range(1, 101):
        rows += [{"t": 10 + n, "ev": "mark", "name": "pass", "why": json.dumps({"n": n, "explore": 0})},
                 {"t": 10 + n, "ev": "start", "id": n, "parent": None, "name": "DSE: batch", "why": "x" * 300, "params": {}}]
    p.write_text("".join(json.dumps(r) + "\n" for r in rows))
    at, before, inside = window_start(str(p), 30)
    assert inside is None
    assert before == 70
    events, _end = read_events(str(p), at)
    assert json.loads(events[0]["why"])["n"] == 71 and sum(e.get("name") == "pass" for e in events) == 30
    assert window_start(str(p), 200) is None, "the start is shorter than the window"
    events, end = read_events(str(p), 0, limit=500)
    assert 0 < end <= 500 and events, "read in slices, each ending on a whole line"


def test_a_long_running_phase_keeps_one_live_snapshot_not_thousands(tmp_path):
    """D761: an hour-long agent turn sent its fields every second into the journal; now the journal
    has its start and its end, and live.json its latest fields while it runs."""
    import time as _time

    from flux_loop.journal import END_MAX

    j = Journal(str(tmp_path / "events.jsonl"))
    tok = j.phase_start("agent: opencode", "draft", {})
    for i in range(50):
        j.phase_update(tok, "agent: opencode", {"steps": [{"text": "x" * 3000}] * 40, "i": i})
    deadline = _time.time() + 10                          # D1070: written about a second later -- later on a loaded machine
    live = {}
    while _time.time() < deadline:
        try:
            live = json.loads((tmp_path / "live.json").read_text())
        except (OSError, ValueError):
            live = {}
        if live.get("updates", {}).get(str(tok), {}).get("i") == 49:
            break
        _time.sleep(0.1)
    assert live["updates"][str(tok)]["i"] == 49, "the latest"
    j.phase_end(tok, "agent: opencode", 1.0, False, {"steps": [{"text": "y" * 4000}] * 60})
    events, _ = read_events(str(tmp_path / "events.jsonl"))
    assert [e["ev"] for e in events] == ["start", "end"]
    assert len(json.dumps(events[1]["output"])) <= END_MAX, "an end's output is capped"
    assert (tmp_path / "events.jsonl").stat().st_size < 60_000


def test_a_start_of_few_long_passes_opens_within_the_budget(tmp_path):
    """D762: three passes of hours each are gigabytes though fewer than the window: the page's first
    look stops at the budget -- at the oldest whole pass in it, or inside the newest, its mark said."""
    from flux_loop.journal import window_start

    p = tmp_path / "events.jsonl"
    rows = [{"t": 0, "ev": "hello"}]
    for n in range(1, 4):
        rows.append({"t": n, "ev": "mark", "name": "pass", "why": json.dumps({"n": n})})
        rows += [{"t": n, "ev": "start", "id": n * 1000 + k, "parent": None, "name": "x", "why": "y" * 900, "params": {}}
                 for k in range(200)]                                     # ~200 KB a pass
    p.write_text("".join(json.dumps(r) + "\n" for r in rows))
    assert window_start(str(p), 30) is None, "within both: whole"
    at, before, inside = window_start(str(p), 30, budget=450_000)
    events, _ = read_events(str(p), at)
    assert (before, inside) == (1, None) and json.loads(events[0]["why"])["n"] == 2, "two whole passes fit"
    at, before, inside = window_start(str(p), 30, budget=50_000)
    events, _ = read_events(str(p), at)
    assert before == 2 and inside is not None and len(events) < 60, "inside the newest pass: its tail"
    assert json.loads(read_events(str(p), inside, limit=2000)[0][0]["why"])["n"] == 3, "and its mark"
    assert p.stat().st_size - at <= 50_000


def test_a_slice_keeps_what_the_tree_needs():
    """D762: a run from before D761 wrote each streamed field as an update -- a slice sends one
    per running phase, merged, none for a phase that ended in it; a publish at its latest."""
    from flux_loop.journal import compact

    ev = [{"ev": "start", "id": 1}, {"ev": "start", "id": 2}]
    ev += [{"ev": "update", "id": 1, "fields": {"text": "a" * i}} for i in range(500)]
    ev += [{"ev": "update", "id": 2, "fields": {"tokens": i}} for i in range(500)] + [{"ev": "update", "id": 2, "fields": {"model": "m"}}]
    ev += [{"ev": "publish", "key": "standings", "payload": i} for i in range(50)] + [{"ev": "end", "id": 1, "output": {}}]
    got = compact(ev)
    assert [e["ev"] for e in got] == ["start", "start", "update", "publish", "end"]
    assert got[2] == {"ev": "update", "id": 2, "fields": {"tokens": 499, "model": "m"}} and got[3]["payload"] == 49



def test_the_window_is_found_in_the_marks_index_as_by_reading_back(tmp_path):
    """D762: a journal written now keeps `marks.jsonl` -- where each start and pass begins -- and
    the window from it is the window read backwards from a journal without one."""
    from flux_loop.journal import Journal, window_start

    d = tmp_path / "run"
    d.mkdir()
    j = Journal(str(d / "events.jsonl"))
    j._write({"ev": "hello", "pid": 1})
    for n in range(1, 6):
        j.mark("pass", json.dumps({"n": n}))
        for k in range(100):
            j._write({"ev": "start", "id": n * 1000 + k, "parent": None, "name": "x", "why": "y" * 900, "params": {}})
    rows = [json.loads(x) for x in (d / "marks.jsonl").read_text().splitlines()]
    assert [r["ev"] for r in rows] == ["hello"] + ["pass"] * 5 and [r["n"] for r in rows[1:]] == [1, 2, 3, 4, 5]
    cases = [(3, 24 << 20), (30, 250_000), (30, 40_000), (30, 1 << 30)]
    indexed = [window_start(str(d / "events.jsonl"), w, budget=b) for w, b in cases]
    (d / "marks.jsonl").unlink()
    assert indexed == [window_start(str(d / "events.jsonl"), w, budget=b) for w, b in cases]
    assert indexed[0][1] == 2 and indexed[3] is None and indexed[2][2] is not None, indexed


def test_a_quiet_step_that_holds_a_task_is_its_parent(tmp_path):
    """D1143: a quiet step (D1047) had no task while it ran -- the agent turn the workbench's README took in
    `knowledge: workbench` was a top task, drawn as an agent's Design, and the step itself came after it. A quiet
    step a task opens under is a task from its own start, the task under it; one with none is still a note."""
    j = Journal(str(tmp_path / "events.jsonl"))
    flux_profile.add_listener(j)
    try:
        with flux_profile.phase("pass"):
            with flux_profile.phase("knowledge: prepare", quiet=True):
                pass
            with flux_profile.phase("knowledge: workbench", why="its README", quiet=True) as out:
                import time as _time

                _time.sleep(0.05)
                with flux_profile.phase("agent: claude", why="digest workbench"):
                    pass
                with flux_profile.phase("frontier", quiet=True):        # quiet inside it, after its task: a note on it
                    pass
                out["files"] = 2
    finally:
        flux_profile.remove_listener(j)
    events, _ = read_events(str(tmp_path / "events.jsonl"))
    by = {(e["ev"], e.get("name")): e for e in events}
    step, turn = by[("start", "knowledge: workbench")], by[("start", "agent: claude")]
    assert turn["parent"] == step["id"] and step["parent"] == by[("start", "pass")]["id"]
    assert step["t"] <= turn["t"] - 0.04, "the step's start is its own, not its first task's"
    assert by[("end", "knowledge: workbench")]["output"] == {"files": 2}
    assert by[("note", "frontier")]["parent"] == step["id"]
    assert ("note", "knowledge: prepare") in by and ("start", "knowledge: prepare") not in by
    assert [e["ev"] for e in events if e.get("name") == "knowledge: workbench"] == ["start", "end"]


def test_a_generation_that_made_no_design_says_why():
    """D1181: a run that drafted no design ended failed with an empty output -- Live's Design line was ✗ with no
    word why, over tries that each passed. Its reason is on it, on one line."""
    from flux_loop.loop import _draft

    class Gen:
        name = "x"

        def generate(self, sg, method, state, human):
            return None, None, "nothing built in 5 tries\n0 failing of 0"

    seen = []

    class Listener:
        def phase_start(self, name, why, params):
            return name

        def phase_end(self, token, name, seconds, failed, output):
            seen.append((name, failed, dict(output)))

    flux_profile.set_listener(Listener())
    try:
        _draft(Gen(), None, None, "", None)
    finally:
        flux_profile.clear_listener()
    assert seen == [("generation: x", True, {"error": "nothing built in 5 tries 0 failing of 0"})]


def test_a_phase_says_its_facts_as_tags_its_inner_phases_and_workers_too(tmp_path):
    """(D1233) The page guessed a task's box, design and check from its name and its words; the engine says
    them. A phase's tag params (`box`, `design`, `step`, `try`, `worker`...) are tags on its start, and on every
    phase inside it -- a worker's too -- apart from its inputs; its end's outcome, the check it stopped at and
    those it never reached are tags of its end. The journal says its format."""
    from flux_loop.pool import run_parallel

    j = attach(str(tmp_path / "run"))
    try:
        with flux_profile.phase("test: build x", why="x#1", box="check", design="x#1", step="build", command="make") as out:
            def one(i):
                with flux_profile.phase("tool:check.py", why="test x#1", step=f"t{i}"):
                    return i

            run_parallel(range(2), one, 2)
            flux_profile.said(**{"failed at": "t1", "not reached": ["t2"]})
            out["exit"] = 1
        with flux_profile.phase("knowledge: mentor", quiet=True, box="setup", step="mentor"):
            pass
        with flux_profile.phase("decide"):
            pass
    finally:
        flux_profile.remove_listener(j)
        from flux_loop import journal as J

        J._ATTACHED.clear()
    events, _ = read_events(str(tmp_path / "run" / "events.jsonl"))
    assert events[0]["ev"] == "hello" and events[0]["format"] == 2
    starts = {e["name"]: e for e in events if e["ev"] == "start"}
    build, tools = starts["test: build x"], [e for e in events if e["ev"] == "start" and e["name"] == "tool:check.py"]
    assert build["tags"] == {"box": "check", "design": "x#1", "step": "build"} and build["params"] == {"command": "make"}
    assert len(tools) == 2 and all(t["tags"]["box"] == "check" and t["tags"]["design"] == "x#1" for t in tools)
    assert sorted(t["tags"]["step"] for t in tools) == ["t0", "t1"], "a tag of its own wins over the one it inherits"
    end = next(e for e in events if e["ev"] == "end" and e["name"] == "test: build x")
    assert end["tags"] == {"failed at": "t1", "not reached": ["t2"]} and end["output"] == {"exit": 1}
    note = next(e for e in events if e["ev"] == "note")
    assert note["tags"] == {"box": "setup", "step": "mentor"}
    assert "tags" not in starts["decide"], "the tags end with their phase"


def test_every_task_of_a_run_says_its_box_and_its_pass(tmp_path, monkeypatch):
    """(D1233) A real run (the digits example, a scripted model): every task and note says its box and its pass,
    the first pass's too (it has no mark); a draft and the checks after it say their try."""
    from flux_cli.main import main

    monkeypatch.setenv("FLUX_TRACE_ROOT", str(tmp_path / "traces"))
    digits = Path(__file__).resolve().parents[2] / "core/loop/examples/digits/problem.json"
    good = "\n".join(str(i) for i in range(10)) + "\n"
    replies = tmp_path / "replies.json"
    replies.write_text(json.dumps([json.dumps({"artifact": "0\n1\n"}), json.dumps({"artifact": good})]))
    try:
        assert main(["task", "run", str(digits), "--db", str(tmp_path / "d.db"), "--replies", str(replies), "--passes", "2"]) == 0
    finally:
        from flux_loop import journal as J

        for lis in list(J._ATTACHED.values()):
            flux_profile.remove_listener(lis)
        J._ATTACHED.clear()
    path = next((tmp_path / "traces").rglob("events.jsonl"))
    events, _ = read_events(str(path))
    assert events[0].get("format") == 2
    tasks = [e for e in events if e["ev"] in ("start", "note")]
    assert tasks and all(e.get("tags", {}).get("box") in ("setup", "plan", "search", "design", "check", "measure", "choose")
                         for e in tasks), [e["name"] for e in tasks if not e.get("tags", {}).get("box")]
    assert {e["tags"].get("pass") for e in tasks} >= {1}, "the first pass is said, though never marked"
    assert all(e["tags"].get("pass") in (1, 2) for e in tasks)
    drafts = [e for e in tasks if e["name"].split(":")[0] in ("generate", "repair")]
    assert drafts and all(isinstance(e["tags"].get("try"), int) for e in drafts)


def test_a_runs_journal_holds_its_work_and_how_each_try_came_out(tmp_path, monkeypatch):
    """(D1235) The right tasks: no `evaluation` around the stages, no decide inside a decide; a try's outcome is a
    `try` note on its run, one per try the drafts say; a check of one command is one task, the command its own."""
    from flux_cli.main import main

    monkeypatch.setenv("FLUX_TRACE_ROOT", str(tmp_path / "traces"))
    digits = Path(__file__).resolve().parents[2] / "core/loop/examples/digits/problem.json"
    good = "\n".join(str(i) for i in range(10)) + "\n"
    replies = tmp_path / "replies.json"
    replies.write_text(json.dumps([json.dumps({"artifact": "0\n1\n"}), json.dumps({"artifact": good})]))
    try:
        assert main(["task", "run", str(digits), "--db", str(tmp_path / "d.db"), "--replies", str(replies), "--passes", "1"]) == 0
    finally:
        from flux_loop import journal as J

        for lis in list(J._ATTACHED.values()):
            flux_profile.remove_listener(lis)
        J._ATTACHED.clear()
    events, _ = read_events(str(next((tmp_path / "traces").rglob("events.jsonl"))))
    starts = {e["id"]: e for e in events if e["ev"] == "start"}
    assert not any(e["name"] == "evaluation" for e in starts.values())
    decides = [e for e in starts.values() if e["name"] == "decide"]
    assert all(starts.get(e["parent"], {}).get("name") != "decide" for e in decides)
    tries = {e["tags"]["try"] for e in starts.values() if e.get("tags", {}).get("try") is not None}
    notes = [e for e in events if e["ev"] == "note" and e["name"] == "try"]
    assert tries and {n["tags"]["try"] for n in notes} == tries
    assert {n["tags"]["outcome"] for n in notes} <= {"passed", "check failed", "not built", "no design", "drafted", "out of time"}
    assert notes[-1]["tags"]["outcome"] == "passed"
    assert all(starts[e["parent"]]["name"].split(":")[0] != "test" for e in starts.values()
               if e["name"].startswith("tool:") and e["parent"] in starts), "a check's one command is the check's own task"
