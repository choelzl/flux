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
    assert kinds == [("start", "pass"), ("start", "gate"), ("update", "gate"), ("end", "gate"), ("end", "pass"),
                     ("publish", "best"), ("start", "in a thread"), ("end", "in a thread")]
    start_gate = events[1]
    assert start_gate["parent"] == events[0]["id"] and events[6]["parent"] is None
    assert len(events[2]["fields"]["tail"]) == 4003 and events[4]["output"] == {"decision": "d#1"}
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
