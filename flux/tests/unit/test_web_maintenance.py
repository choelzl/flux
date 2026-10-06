"""D885: maintenance tasks an admin schedules (Gitea's cron tasks). Each does what it says on what is
idle, leaves a running loop alone, records what it did -- a scheduled run that found nothing moves
the clock only -- and a loop's tasks run on one loop for its owner."""

from __future__ import annotations

import gzip
import os
import sqlite3
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from flux_web import create_app
from flux_web.maintenance import TASKS, Maintenance
from flux_web.store import Store

H = {"X-Flux": "1"}
OLD = time.time() - 30 * 86400


def _store(tmp_path):
    store = Store(tmp_path / "data")
    store.add_user("ada", "correct horse battery", "admin")
    store.add_user("bob", "another long secret")
    return store


def _loop(tmp_path, name="x"):
    d = tmp_path / "loops" / name
    (d / "runs").mkdir(parents=True)
    (d / "out").mkdir()
    return d


def _m(store, loops, live=()):
    return Maintenance(store, lambda u, a: (u, a) in live, lambda: loops)


def _age(p: Path):
    for top, dirs, files in os.walk(p):
        for n in (*dirs, *files):
            os.utime(os.path.join(top, n), (OLD, OLD))
    os.utime(p, (OLD, OLD))


def test_scratch_removes_what_is_old_and_unused_and_keeps_the_rest(tmp_path, monkeypatch):
    root = tmp_path / "tmp"
    root.mkdir()
    for name in ("old-check", "claude-1", "fresh", "in-use"):
        (root / name).mkdir()
        (root / name / "f.txt").write_text("x" * 1000)
    for name in ("old-check", "claude-1", "in-use"):
        _age(root / name)
    monkeypatch.setenv("FLUX_TMPDIR", str(root))
    monkeypatch.chdir(root / "in-use")              # this process works there: in use
    got = _m(_store(tmp_path), []).run("scratch", by="ada")
    assert got["ok"] and sorted(p.name for p in root.iterdir()) == ["claude-1", "fresh", "in-use"], got
    assert got["said"].startswith("1 entry removed")


def test_compact_leaves_a_running_loops_record_and_vacuums_the_rest(tmp_path):
    store = _store(tmp_path)
    idle, busy = _loop(tmp_path, "idle"), _loop(tmp_path, "busy")
    for d in (idle, busy):
        con = sqlite3.connect(d / "out" / "r.db")
        con.execute("CREATE TABLE t (v BLOB)")
        con.executemany("INSERT INTO t VALUES (?)", [(b"x" * 4000,)] * 300)
        con.commit()
        con.execute("DELETE FROM t")
        con.commit()
        con.close()
    size = (busy / "out" / "r.db").stat().st_size
    m = _m(store, [("bob", "idle", idle), ("bob", "busy", busy)], live={("bob", "busy")})
    got = m.run("compact", by="ada")
    assert "2 database(s) compacted" in got["said"] and "1 running, left alone (bob/busy)" in got["said"], got
    assert (idle / "out" / "r.db").stat().st_size < size / 10, "vacuumed"
    assert (busy / "out" / "r.db").stat().st_size == size, "a running loop's record is not touched"


def test_a_long_log_keeps_its_recent_part_and_gzips_the_rest(tmp_path):
    d = _loop(tmp_path)
    log = d / "runs" / "loop.log"
    text = "".join(f"\n── started 2026-10-0{i} by bob\n" + "line\n" * 200_000 for i in range(1, 4))
    log.write_text(text)
    m = _m(_store(tmp_path), [("bob", "x", d)])
    m.set_config("logs", None, None, {"max_mb": 1, "keep_mb": 1})
    got = m.run("logs", by="ada")
    assert got["said"].startswith("1 log(s) condensed"), got
    kept = log.read_text()
    assert kept.startswith("(the log before this line is in loop.log.1.gz")
    assert "started 2026-10-01" not in kept and "started 2026-10-02" not in kept, "only the recent part"
    whole = gzip.open(d / "runs" / "loop.log.1.gz").read().decode() + kept.split("\n", 1)[1]
    assert whole == text, "nothing lost: the gzip and the log are the old log"


def test_server_tables_lose_what_expired(tmp_path):
    store = _store(tmp_path)
    with store._db() as db:
        db.execute("INSERT INTO sessions VALUES ('old', 1, 0, 1)")
        db.execute("INSERT INTO failures(name, t) VALUES ('x', ?)", (OLD - 86400 * 100,))
        db.execute("INSERT INTO audit(t, user, action) VALUES (?, 'ada', 'ancient')", (OLD - 86400 * 400,))
    store.server_set("notices:bob", [{"text": "old", "t": OLD}, {"text": "new", "t": time.time()}])
    got = _m(store, []).run("tables", by="ada")
    assert got["said"].startswith("1 session(s), 0 invitation(s), 1 login failure(s), 1 notification(s), 1 audit line(s)"), got
    assert [n["text"] for n in store.server_get("notices:bob")] == ["new"]


def test_the_disk_alert_tells_the_admins_once(tmp_path):
    store = _store(tmp_path)
    m = _m(store, [])
    m.set_config("disk", None, None, {"min_free_pct": 101})       # every disk is "low"
    assert "admins told" in m.run("disk", by="schedule")["said"]
    assert "admins told" not in m.run("disk", by="schedule")["said"], "said once"
    assert [n["kind"] for n in store.take_notices("ada")] == ["bad"] and not store.take_notices("bob")


def test_scratch_never_cleans_a_folder_that_is_not_scratch(tmp_path, monkeypatch):
    """The first version took an unset FLUX_TMPDIR as `Path("")` -- the working folder -- and a test
    run from the repository deleted its pyproject.toml, pytest.ini and a 1.5 GB record there."""
    project = tmp_path / "project"
    project.mkdir()
    (project / "pyproject.toml").write_text("[project]\n")
    (project / "old.db").write_text("keep me")
    _age(project / "old.db")
    monkeypatch.chdir(project)
    store = _store(tmp_path)
    for value in (None, "", "   ", ".", "relative/dir", str(project), str(Path.home()), "/", str(tmp_path)):
        if value is None:
            monkeypatch.delenv("FLUX_TMPDIR", raising=False)
        else:
            monkeypatch.setenv("FLUX_TMPDIR", value)
        got = _m(store, []).run("scratch", by="ada")
        assert "nothing" in got["said"] and (project / "old.db").exists() and (project / "pyproject.toml").exists(), (value, got)


def test_a_schedule_run_that_found_nothing_moves_the_clock_only(tmp_path, monkeypatch):
    (tmp_path / "scratch").mkdir()
    monkeypatch.setenv("FLUX_TMPDIR", str(tmp_path / "scratch"))          # empty: nothing to do
    store = _store(tmp_path)
    m = _m(store, [])
    assert "scratch" not in m.due() and "stale" not in m.due(), "machine-wide scratch and stale rows: off by default"
    m.set_config("scratch", True, None, None)
    assert "scratch" in m.due()
    m.run("scratch", by="schedule")
    assert m.history("scratch") == [] and m.checked("scratch"), "nothing done: no history, the clock moved"
    assert "scratch" not in m.due()
    assert "scratch" in m.due(now=time.time() + TASKS["scratch"].every_h * 3600 + 1)
    m.run("scratch", by="ada")
    assert len(m.history("scratch")) == 1, "a run by hand is kept"


def test_settings_are_checked(tmp_path):
    m = _m(_store(tmp_path), [])
    with pytest.raises(ValueError, match="no setting"):
        m.set_config("scratch", None, None, {"nope": 1})
    with pytest.raises(ValueError, match="a number"):
        m.set_config("scratch", None, None, {"days": "soon"})
    with pytest.raises(ValueError, match="from a minute"):
        m.set_config("scratch", None, 0.001, None)
    with pytest.raises(ValueError, match="the server's, not a loop's"):
        m.run("tables", loop=("bob", "x"))


def test_the_admin_runs_tasks_and_an_owner_runs_a_loops_task_on_their_loop_only(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    store = _store(tmp_path)
    store.add_user("cy", "a third long secret")
    app = create_app(tmp_path / "data", sandbox=False)
    c = {}
    for who, pw in (("ada", "correct horse battery"), ("bob", "another long secret"), ("cy", "a third long secret")):
        c[who] = TestClient(app)
        assert c[who].post("/api/login", json={"name": who, "password": pw}, headers=H).status_code == 200
    files = [("files", ("problem.yaml", b"statement: s\n"))]
    assert c["bob"].post("/api/apps", data={"name": "x"}, files=files, headers=H).status_code == 200
    got = c["ada"].get("/api/admin/maintenance").json()
    assert {t["key"] for t in got["tasks"]} == set(TASKS) and "bob/x" in got["loops"]
    assert c["bob"].get("/api/admin/maintenance").status_code == 403
    assert c["ada"].put("/api/admin/maintenance/logs", json={"every_h": 12, "params": {"max_mb": 5}}, headers=H).json()["params"]["max_mb"] == 5
    assert c["ada"].put("/api/admin/maintenance/logs", json={"params": {"bad": 1}}, headers=H).status_code == 400
    r = c["ada"].post("/api/admin/maintenance/compact/run", json={"loop": "bob/x"}, headers=H).json()
    assert r["ok"] and r["loop"] == "bob/x" and r["by"] == "ada"
    mine = c["bob"].get("/api/apps/x/maintenance").json()["tasks"]
    assert {t["key"] for t in mine} == {k for k, t in TASKS.items() if t.per_loop}
    assert c["bob"].post("/api/apps/x/maintenance/compact", headers=H).json()["by"] == "bob"
    assert c["bob"].post("/api/apps/x/maintenance/tables", headers=H).status_code == 404, "a server task is the admin's"
    assert c["cy"].post("/api/apps/x/maintenance/compact", params={"owner": "bob"}, headers=H).status_code in (403, 404)
