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
    assert stage["params"]["part"] == "exp" and all(e["params"]["part"] == "exp" for e in tools)
    decide = next(e for e in starts if e["name"] == "decide")
    assert decide["parent"] is None and "part" not in decide["params"], "the tag ends with its block"
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
    at, before = window_start(str(p), 30)
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
    _time.sleep(1.2)
    live = json.loads((tmp_path / "live.json").read_text())
    assert live["updates"][str(tok)]["i"] == 49, "the latest"
    j.phase_end(tok, "agent: opencode", 1.0, False, {"steps": [{"text": "y" * 4000}] * 60})
    events, _ = read_events(str(tmp_path / "events.jsonl"))
    assert [e["ev"] for e in events] == ["start", "end"]
    assert len(json.dumps(events[1]["output"])) <= END_MAX, "an end's output is capped"
    assert (tmp_path / "events.jsonl").stat().st_size < 60_000
