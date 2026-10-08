"""Admin restarts preserve launch settings and never overlap old and new processes."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

from test_web_admin import H, _client, _loop, _running, server  # noqa: F401 -- shared fixture


def _wait(check, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.05)
    raise AssertionError("run did not reach the expected state")


@pytest.fixture()
def launcher(server, monkeypatch, tmp_path):  # noqa: F811
    from flux_web import runs

    app, _tmp = server
    fake = tmp_path / "flux-stand-in"
    fake.write_text(f"#!{sys.executable}\n" + """
import json, os, signal, sys, time
from flux_loop import ops
ops.register('test-' + os.getenv('FLUX_TEST_OWNER') + '-' + os.path.basename(os.getcwd()),
             os.getcwd(), db=sys.argv[sys.argv.index('--db') + 1])
def stopped(*_):
    if os.getenv('FLUX_TEST_FINISH_ON_STOP') == 'yes':
        ops.pass_ended()
    sys.exit(130)
signal.signal(signal.SIGINT, stopped)
print(json.dumps({'ready': True, 'argv': sys.argv[1:], 'owner': os.getenv('FLUX_TEST_OWNER'),
                  'revision': os.getenv('FLUX_SETTING_REV')}), flush=True)
while True:
    time.sleep(0.1)
""")
    fake.chmod(0o755)
    which = runs.shutil.which
    monkeypatch.setattr(runs.shutil, "which", lambda name, *a, **kw: str(fake) if name == "flux" else which(name, *a, **kw))
    monkeypatch.setattr(runs, "home_ready", lambda *_: None)
    monkeypatch.setattr(runs, "run_env", lambda store, user, name: {**os.environ, "FLUX_TEST_OWNER": user.name,
                                                                   "FLUX_SETTING_REV": str(store.server_get("test-revision") or 1),
                                                                   "FLUX_TEST_FINISH_ON_STOP": store.server_get("test-finish-on-stop") or "no"})
    try:
        yield app, tmp_path
    finally:
        for run in app.state.store.runs():
            if not run.get("ended"):
                app.state.runs.stop(run, now=True)
        _wait(lambda: all(r.get("ended") for r in app.state.store.runs()))


def _ready(app, user, name):
    run = app.state.runs.latest(app.state.store.user(name=user), name)
    return '"ready": true' in Path(run["log"]).read_text()[json.loads(run["options"]).get("log_offset", 0):]


def _completed(runs, run, count, *, stale=False):
    _cid, directory = runs.campaign(run)
    path = Path(directory) / "run.json"
    registration = json.loads(path.read_text())
    registration["passes"] = count
    if stale:
        registration["started"] = run["started"] - 60
    path.write_text(json.dumps(registration))


def test_restart_all_preserves_finite_unlimited_screen_and_document_options(launcher):
    app, tmp = launcher
    store, runs = app.state.store, app.state.runs
    bob, ada = _client(app, "bob", "another long secret"), _client(app, "ada", "correct horse battery")
    _loop(bob, "finite")
    _loop(bob, "idle")
    _loop(ada, "forever")
    assert bob.put("/api/apps/finite/file?path=other.problem.yaml", json={"text": "statement: other\n"}, headers=H).status_code == 200
    settings = {"finite": {"passes": 10, "screen_only": True, "document": "other.problem.yaml"},
                "forever": {"passes": None, "screen_only": False, "document": "forever.problem.yaml"}}
    for owner, client, name in (("bob", bob, "finite"), ("ada", ada, "forever")):
        response = client.post(f"/api/apps/{name}/start", json=settings[name], headers=H)
        assert response.status_code == 200, response.text
        _wait(lambda: _ready(app, owner, name))
        home = tmp / "data/users" / owner / "apps" / name
        (home / "out/keep.txt").write_text("prior results")
        (home / "workbench").mkdir(exist_ok=True)
        (home / "workbench/keep.txt").write_text("existing design")
    old = {r["app"]: r for r in store.runs()}
    _completed(runs, old["finite"], 5)
    _completed(runs, old["forever"], 100)
    expected = {**settings, "finite": {**settings["finite"], "passes": 5}}
    # A restart uses the recorded document, even if metadata changed in the meantime.
    from flux_web.workspace import Workspace

    Workspace(store.data, "bob").set_meta("finite", document="finite.problem.yaml")
    store.server_set("test-revision", 2)
    result = ada.post("/api/admin/restart-all", headers=H)
    assert result.status_code == 200, result.text
    got = result.json()
    assert got["failed"] == got["skipped"] == {}
    assert got["restarted"] == {"bob/finite": expected["finite"], "ada/forever": expected["forever"]}
    for owner, name in (("bob", "finite"), ("ada", "forever")):
        _wait(lambda: _ready(app, owner, name))
        new = runs.latest(store.user(name=owner), name)
        previous = store.run(old[name]["id"])
        assert new["id"] != previous["id"] and new["pid"] != previous["pid"]
        assert previous["ended"] <= new["started"] and previous["rc"] == 130
        argv = json.loads(previous["argv"])
        if name == "finite":
            argv[argv.index("--passes") + 1] = "5"
        assert json.loads(new["argv"]) == argv and new["db"] == previous["db"]
        options, previous_options = json.loads(new["options"]), json.loads(previous["options"])
        assert {k: options[k] for k in settings[name]} == expected[name]
        assert runs.completed_passes(new) == 0, "replacement registration starts its own counter"
        assert options["log_offset"] > previous_options["log_offset"]
        home = tmp / "data/users" / owner / "apps" / name
        assert (home / "out/keep.txt").read_text() == "prior results"
        assert (home / "workbench/keep.txt").read_text() == "existing design"
        log = (home / "runs/loop.log").read_text()
        assert log.count("── started ") == 2 and log.count('"ready": true') == 2
        assert f'"owner": "{owner}", "revision": "2"' in log
        assert "by ada" in log[options["log_offset"]:]
    assert store.runs(store.user(name="bob"), "idle") == []
    assert any(a["action"] == "restart all" for a in ada.get("/api/audit").json())
    _completed(runs, runs.latest(store.user(name="bob"), "finite"), 2)
    again = ada.post("/api/admin/restart-all", headers=H)
    assert again.status_code == 200, again.text
    assert again.json()["restarted"]["bob/finite"]["passes"] == 3, "repeated restarts retain the remaining budget"
    assert again.json()["restarted"]["ada/forever"]["passes"] is None


@pytest.mark.parametrize("completed,stale,finish,remaining", [(0, False, False, 10), (5, True, False, 10),
                                                           (4, False, True, 5), (10, False, False, 0), (12, False, False, 0)])
def test_restart_budget_uses_only_this_start_and_final_completion_count(launcher, completed, stale, finish, remaining):
    app, _tmp = launcher
    store, runs = app.state.store, app.state.runs
    bob, ada = _client(app, "bob", "another long secret"), _client(app, "ada", "correct horse battery")
    _loop(bob, "budget")
    store.server_set("test-finish-on-stop", "yes" if finish else "no")
    assert bob.post("/api/apps/budget/start", json={"passes": 10}, headers=H).status_code == 200
    _wait(lambda: _ready(app, "bob", "budget"))
    old = runs.latest(store.user(name="bob"), "budget")
    _completed(runs, old, completed, stale=stale)
    assert runs.state(store.user(name="bob"), "budget").get("passes") == (None if stale else completed)
    result = ada.post("/api/admin/restart-all", headers=H).json()
    assert result["failed"] == {}
    if remaining:
        assert result["skipped"] == {}
        assert result["restarted"]["bob/budget"]["passes"] == remaining
        new = runs.latest(store.user(name="bob"), "budget")
        assert json.loads(new["options"])["passes"] == remaining
    else:
        assert result["restarted"] == {} and "budget completed" in result["skipped"]["bob/budget"]
        assert runs.latest(store.user(name="bob"), "budget")["id"] == old["id"]
        assert not runs.live(store.run(old["id"]))


def test_restart_all_requires_admin_and_mutation_header(server):  # noqa: F811
    app, _tmp = server
    bob, ada = _client(app, "bob", "another long secret"), _client(app, "ada", "correct horse battery")
    assert bob.post("/api/admin/restart-all", headers=H).status_code == 403
    assert ada.post("/api/admin/restart-all").status_code == 403
    assert ada.post("/api/admin/restart-all", headers=H).json() == {"restarted": {}, "failed": {}, "skipped": {}}


@pytest.mark.parametrize("blocked", ["paused", "limit", "document"])
def test_restart_preflight_leaves_blocked_loop_running(server, blocked):  # noqa: F811
    app, tmp = server
    bob, ada = _client(app, "bob", "another long secret"), _client(app, "ada", "correct horse battery")
    _loop(bob, "x")
    proc = _running(app, tmp, "bob", "x")
    try:
        if blocked == "paused":
            app.state.store.server_set("paused", "maintenance")
        elif blocked == "limit":
            app.state.store.server_set("max_running:bob", 0)
        else:
            (tmp / "data/users/bob/apps/x/x.problem.yaml").unlink()
        result = ada.post("/api/admin/restart-all", headers=H)
        if blocked == "paused":
            assert result.status_code == 409 and "maintenance" in result.json()["detail"]
        else:
            assert result.status_code == 200 and set(result.json()["failed"]) == {"bob/x"}
            assert result.json()["restarted"] == {}
        assert proc.poll() is None and len(app.state.store.runs()) == 1
    finally:
        proc.kill()
        proc.wait()


def test_a_slow_stop_never_launches_an_overlapping_replacement(server, monkeypatch):  # noqa: F811
    from flux_web import routes_admin

    app, tmp = server
    bob, ada = _client(app, "bob", "another long secret"), _client(app, "ada", "correct horse battery")
    _loop(bob, "x")
    proc = _running(app, tmp, "bob", "x")
    monkeypatch.setattr(app.state.runs, "stop", lambda *_a, **_kw: "still stopping")
    monkeypatch.setattr(routes_admin, "RESTART_WAIT_S", 0)
    try:
        result = ada.post("/api/admin/restart-all", headers=H).json()
        assert result["restarted"] == {} and "still stopping" in result["failed"]["bob/x"]
        assert proc.poll() is None and len(app.state.store.runs()) == 1
    finally:
        proc.kill()
        proc.wait()
