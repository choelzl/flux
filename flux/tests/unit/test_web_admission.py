"""D854: a start is admitted atomically. An external review found two starts at once could both
pass the checks (the loop not running, the user under their limit) and both launch the same loop --
one record, one output, two runs. The reservation is now one write transaction."""

from __future__ import annotations

import threading
import time

import pytest

from flux_web.runs import RunManager
from flux_web.store import Store


def _store(tmp_path):
    store = Store(tmp_path / "data")
    store.add_user("bob", "another long secret")
    return store, store.user(name="bob")


def _many(fn, n):
    """`fn(i)` from `n` threads released together: (successes, failures)."""
    go, ok, bad = threading.Barrier(n), [], []

    def one(i):
        go.wait()
        try:
            ok.append(fn(i))
        except ValueError as exc:
            bad.append(str(exc))

    ts = [threading.Thread(target=one, args=(i,)) for i in range(n)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    return ok, bad


def test_two_starts_of_one_loop_at_once_admit_one(tmp_path):
    store, bob = _store(tmp_path)
    ok, bad = _many(lambda i: store.reserve_run(bob, "x", 5, "db", "log", ["x"], {}), 8)
    assert len(ok) == 1 and len(bad) == 7 and all("is running" in b for b in bad), (ok, bad)


def test_starts_of_different_loops_stop_at_the_users_limit(tmp_path):
    store, bob = _store(tmp_path)
    ok, bad = _many(lambda i: store.reserve_run(bob, f"loop{i}", 2, "db", "log", ["x"], {}), 6)
    assert len(ok) == 2 and all("at most 2 loop(s)" in b for b in bad), (ok, bad)


def test_a_reservation_counts_until_its_process_starts_and_lapses_after(tmp_path, monkeypatch):
    store, bob = _store(tmp_path)
    rid = store.reserve_run(bob, "x", 5, "db", "log", ["x"], {})
    rm = RunManager(store, sandbox=False)
    assert rm.live(store.run(rid)), "starting: counted as running"
    with pytest.raises(ValueError, match="is running"):
        store.reserve_run(bob, "x", 5, "db", "log", ["x"], {})
    monkeypatch.setattr(Store, "STARTING_S", 0.0)          # a reservation a crash left behind lapses
    time.sleep(0.01)
    assert not rm.live(store.run(rid))
    assert store.reserve_run(bob, "x", 5, "db", "log", ["x"], {})


def test_a_launch_that_fails_gives_its_place_back(tmp_path, monkeypatch):
    """The review's third case: a failed process launch, then a retry that succeeds."""
    import subprocess

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))     # the run's traces, the test's own
    store, bob = _store(tmp_path)
    app = store.data / "users" / "bob" / "apps" / "x"
    app.mkdir(parents=True)
    (app / "problem.yaml").write_text("statement: s\n")
    rm = RunManager(store, sandbox=False)
    real = subprocess.Popen

    def broken(*a, **k):
        raise OSError("no such interpreter")

    monkeypatch.setattr(subprocess, "Popen", broken)
    with pytest.raises(OSError):
        rm.start(bob, "x", app, "problem.yaml", "x", {"passes": 1})
    (row,) = store.runs(bob)
    assert row["ended"] and row["rc"] == -1, "released"
    monkeypatch.setattr(subprocess, "Popen", real)
    rm.start(bob, "x", app, "problem.yaml", "x", {"passes": 1})
    latest = rm.latest(bob, "x")
    try:
        # launched: a process, not the failed launch's mark -- not "still alive", which raced with a
        # run that ends at once (this document is no task: it exits in 0.4 s, before a busy CI looked)
        assert latest["pid"] and latest["rc"] != -1
        for _ in range(100):                                 # and its end is recorded when it ends
            row = rm.latest(bob, "x")
            if row["ended"]:
                break
            time.sleep(0.1)
        assert row["ended"] and row["rc"] is not None and row["rc"] != -1, row
    finally:
        import os
        import signal

        try:
            os.killpg(latest["pid"], signal.SIGKILL)          # the retried run, ended with the test
        except (ProcessLookupError, PermissionError):
            pass
