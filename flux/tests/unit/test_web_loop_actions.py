"""After-pass restarts wait for exit, retain budgets, and can be escalated or cancelled."""

import json
import os
import signal
from pathlib import Path

import pytest

from flux_web import create_app
from test_web_admin import H, _client, _loop, server  # noqa: F401
from test_web_restart import _completed, _ready, _wait, launcher  # noqa: F401


def started(app, passes=10, name="x"):
    client = _client(app, "bob", "another long secret")
    _loop(client, name)
    response = client.post(f"/api/apps/{name}/start", json={"passes": passes, "screen_only": True}, headers=H)
    assert response.status_code == 200, response.text
    _wait(lambda: _ready(app, "bob", name))
    return client, app.state.runs.latest(app.state.store.user(name="bob"), name)


def finish(app, run):
    os.killpg(run["pid"], signal.SIGINT)
    _wait(lambda: app.state.store.run(run["id"])["ended"] is not None)


@pytest.mark.parametrize("passes", [10, None])
def test_restart_waits_for_the_pass_and_reduces_only_completed_budget(launcher, passes):  # noqa: F811
    app, _ = launcher
    client, old = started(app, passes)
    _completed(app.state.runs, old, 5)
    result = client.post("/api/apps/x/restart", json={"now": False}, headers=H)
    assert result.status_code == 200 and "after this pass" in result.json()["ok"]
    state = client.get("/api/apps/x/state").json()
    assert state["running"] and state["stop_requested"] and state["restart_requested"]
    app.state.actions.tick()
    assert len(app.state.store.runs()) == 1 and app.state.runs.live(old), "request must not interrupt the pass"
    # The final pass finishes while a graceful stop is pending.
    _completed(app.state.runs, old, 6)
    finish(app, old)
    _wait(lambda: app.state.runs.latest(app.state.store.user(name="bob"), "x")["id"] != old["id"])
    _wait(lambda: _ready(app, "bob", "x"))
    new = app.state.runs.latest(app.state.store.user(name="bob"), "x")
    assert app.state.store.run(old["id"])["ended"] <= new["started"]
    options = json.loads(new["options"])
    assert options["passes"] == (4 if passes else None) and options["screen_only"]
    assert options["document"] == "x.problem.yaml"
    assert not client.get("/api/apps/x/state").json()["restart_requested"]


def test_second_restart_can_interrupt_without_spending_the_unfinished_pass(launcher):  # noqa: F811
    app, _ = launcher
    client, old = started(app)
    _completed(app.state.runs, old, 5)
    assert client.post("/api/apps/x/restart", json={"now": False}, headers=H).status_code == 200
    assert client.post("/api/apps/x/restart", json={"now": True}, headers=H).status_code == 200
    _wait(lambda: app.state.runs.latest(app.state.store.user(name="bob"), "x")["id"] != old["id"])
    new = app.state.runs.latest(app.state.store.user(name="bob"), "x")
    assert app.state.store.run(old["id"])["ended"] <= new["started"]
    assert json.loads(new["options"])["passes"] == 5


def test_stop_supersedes_a_queued_restart(launcher):  # noqa: F811
    app, _ = launcher
    client, old = started(app)
    client.post("/api/apps/x/restart", json={"now": False}, headers=H)
    assert client.post("/api/apps/x/stop", json={"now": False}, headers=H).status_code == 200
    assert not client.get("/api/apps/x/state").json()["restart_requested"]
    finish(app, old)
    app.state.actions.tick()
    assert len(app.state.store.runs()) == 1


@pytest.mark.parametrize("block", ["paused", "permissions", "disabled", "document", "exhausted"])
def test_boundary_rechecks_authorization_configuration_and_budget(launcher, block):  # noqa: F811
    app, _ = launcher
    client, old = started(app)
    client.post("/api/apps/x/restart", json={"now": False}, headers=H)
    if block == "paused":
        app.state.store.server_set("paused", "maintenance")
    elif block == "permissions":
        app.state.store.set_user("bob", permissions={"run_loops": False})
    elif block == "disabled":
        app.state.store.set_user("bob", disabled=True)
    elif block == "document":
        (Path(old["log"]).parent.parent / "x.problem.yaml").unlink()
    else:
        _completed(app.state.runs, old, 10)
    finish(app, old)
    app.state.actions.tick()
    assert len(app.state.store.runs()) == 1 and not app.state.store.server_get("loop_actions")


def test_restart_queue_survives_a_server_restart(launcher):  # noqa: F811
    app, tmp = launcher
    client, old = started(app)
    client.post("/api/apps/x/restart", json={"now": False}, headers=H)
    app.state.actions.close()
    recreated = create_app(tmp / "data", sandbox=False)
    try:
        finish(app, old)
        recreated.state.actions.resume()
        _wait(lambda: recreated.state.runs.latest(recreated.state.store.user(name="bob"), "x")["id"] != old["id"])
        assert recreated.state.store.run(old["id"])["ended"] <= recreated.state.runs.latest(recreated.state.store.user(name="bob"), "x")["started"]
    finally:
        recreated.state.actions.close()


@pytest.mark.parametrize("body", [None, {"now": False}])
def test_admin_bulk_restart_is_scheduled_and_idle_loops_are_untouched(launcher, body):  # noqa: F811
    app, _ = launcher
    bob, old = started(app)
    _loop(bob, "idle")
    ada = _client(app, "ada", "correct horse battery")
    got = ada.post("/api/admin/restart-all", json=body, headers=H).json()
    assert set(got["scheduled"]) == {"bob/x"} and got["failed"] == got["restarted"] == {}
    assert app.state.runs.live(old) and len(app.state.store.runs()) == 1
    finish(app, old)
    _wait(lambda: len(app.state.store.runs()) == 2)
    assert app.state.store.runs(app.state.store.user(name="bob"), "idle") == []


def test_restart_requires_run_access_and_mutation_header(launcher):  # noqa: F811
    app, _ = launcher
    bob, _old = started(app)
    app.state.store.add_user("cy", "another long secret")
    cy = _client(app, "cy", "another long secret")
    assert cy.post("/api/apps/x/restart?owner=bob", json={"now": False}, headers=H).status_code == 403
    assert bob.post("/api/apps/x/restart", json={"now": False}).status_code == 403
    app.state.store.set_user("bob", permissions={"run_loops": False})
    assert bob.post("/api/apps/x/restart", json={"now": False}, headers=H).status_code == 403
    assert bob.post("/api/apps/x/stop", json={"now": True}, headers=H).status_code == 200


def test_graceful_stop_waits_for_this_starts_registration(launcher):  # noqa: F811
    app, _ = launcher
    client, old = started(app)
    _completed(app.state.runs, old, 0, stale=True)
    _cid, directory = app.state.runs.campaign(old)
    stop = Path(directory) / "stop"
    assert client.post("/api/apps/x/stop", json={"now": False}, headers=H).status_code == 200
    app.state.actions.tick()
    assert app.state.runs.live(old) and not stop.exists(), "a stale registration must not be signalled"
    registration = Path(directory) / "run.json"
    doc = json.loads(registration.read_text())
    doc["started"] = old["started"] + 0.01
    registration.write_text(json.dumps(doc))
    app.state.actions.tick()
    assert stop.exists() and app.state.runs.live(old), "the pending stop reaches the new registration without interrupting"
    client.post("/api/apps/x/stop", json={"now": True}, headers=H)
    _wait(lambda: not app.state.runs.live(app.state.store.run(old["id"])))


def test_reset_with_retained_history_cancels_a_queued_restart(launcher):  # noqa: F811
    app, _ = launcher
    client, old = started(app)
    client.post("/api/apps/x/restart", json={"now": False}, headers=H)
    app.state.actions.close()
    finish(app, old)
    response = client.post("/api/apps/x/reset", json={"keep": ["history"]}, headers=H)
    assert response.status_code == 200, response.text
    app.state.actions.tick()
    assert len(app.state.store.runs()) == 1 and not app.state.store.server_get("loop_actions")


def test_a_temporarily_unavailable_signal_is_retried(launcher, monkeypatch):  # noqa: F811
    app, _ = launcher
    client, old = started(app)
    original = app.state.runs.stop
    attempts = []

    def signal_request(*args, **kwargs):
        attempts.append(True)
        return "not running" if len(attempts) == 1 else original(*args, **kwargs)

    monkeypatch.setattr(app.state.runs, "stop", signal_request)
    client.post("/api/apps/x/stop", json={"now": False}, headers=H)
    _wait(lambda: len(attempts) >= 2)
    _cid, directory = app.state.runs.campaign(old)
    _wait(lambda: (Path(directory) / "stop").exists())
    assert app.state.runs.live(old), "retrying a graceful request must still leave the pass running"


def test_a_manual_start_supersedes_the_queued_restart(launcher):  # noqa: F811
    app, _ = launcher
    client, old = started(app)
    client.post("/api/apps/x/restart", json={"now": False}, headers=H)
    app.state.actions.close()
    finish(app, old)
    response = client.post("/api/apps/x/start", json={"passes": 7}, headers=H)
    assert response.status_code == 200, response.text
    app.state.actions.tick()
    assert len(app.state.store.runs()) == 2
    assert json.loads(app.state.runs.latest(app.state.store.user(name="bob"), "x")["options"])["passes"] == 7
    assert not app.state.store.server_get("loop_actions")
