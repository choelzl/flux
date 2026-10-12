"""D987: a start stopped before it did anything is not kept -- not listed, its row gone, and when it
is the latest, its part of the log and of the journal cut back."""

import json

from flux_web.run_history import empty_start, forget_start


class Runs:
    def __init__(self, root):
        self.root = str(root)

    def roots(self, run):
        return (self.root,)


def journal(tmp_path, starts):
    """events.jsonl and marks.jsonl with one hello per start, each followed by its top-level tasks."""
    d = tmp_path / "trace"
    d.mkdir()
    ev, marks = d / "events.jsonl", d / "marks.jsonl"
    with open(ev, "wb") as e, open(marks, "w") as m:
        for t, names in starts:
            m.write(json.dumps({"at": e.tell(), "ev": "hello", "n": 0}) + "\n")
            e.write((json.dumps({"ev": "hello", "t": t}) + "\n").encode())
            for i, n in enumerate(names):
                e.write((json.dumps({"ev": "start", "id": i + 1, "parent": None, "name": n, "t": t + i}) + "\n").encode())
    (tmp_path / "run.db.runs.json").write_text(json.dumps({"c": str(d)}))
    return ev, marks


def run(tmp_path, started, ended, **kw):
    return {"id": 1, "db": str(tmp_path / "run.db"), "started": started, "ended": ended, "rc": -15, **kw}


def test_a_start_that_only_set_up_is_empty_and_one_that_searched_is_not(tmp_path):
    journal(tmp_path, [(100, ["gate: tools", "DSE: batch"]), (200, ["gate: tools", "propose: decompose", "knowledge: prepare x"])])
    r = Runs(tmp_path)
    assert not empty_start(run(tmp_path, 99, 150), r)
    assert empty_start(run(tmp_path, 199, 205), r)
    assert not empty_start(run(tmp_path, 199, None), r), "a running start is never empty"


def test_forgetting_the_latest_empty_start_cuts_its_log_and_journal_back(tmp_path):
    ev, marks = journal(tmp_path, [(100, ["DSE: batch"]), (200, ["gate: tools"])])
    log = tmp_path / "loop.log"
    log.write_text("first start's output\n── started …\nsecond, stopped at once\n")
    offset = len("first start's output\n")
    gone = []
    store = type("S", (), {"delete_run": lambda self, i: gone.append(i)})()
    r = run(tmp_path, 199, 203, log=str(log), options=json.dumps({"log_offset": offset}))
    forget_start(r, Runs(tmp_path), store, last=True)
    assert gone == [1] and log.read_text() == "first start's output\n"
    assert [json.loads(x)["ev"] for x in ev.read_text().splitlines()] == ["hello", "start"]
    assert len(marks.read_text().splitlines()) == 1


def test_an_earlier_empty_start_loses_only_its_row(tmp_path):
    ev, _marks = journal(tmp_path, [(100, ["gate: tools"]), (200, ["DSE: batch"])])
    log = tmp_path / "loop.log"
    log.write_text("a\nb\n")
    store = type("S", (), {"delete_run": lambda self, i: None})()
    forget_start(run(tmp_path, 99, 103, log=str(log), options=json.dumps({"log_offset": 0})), Runs(tmp_path), store, last=False)
    assert log.read_text() == "a\nb\n" and len(ev.read_text().splitlines()) == 4


def test_a_start_without_a_journal_is_kept(tmp_path):
    assert not empty_start(run(tmp_path, 100, 103), Runs(tmp_path)), "no journal: no telling, kept"
