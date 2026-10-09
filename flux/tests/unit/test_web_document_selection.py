"""Switching problem files updates the polled selection and the document actually launched."""

import json

import pytest

from test_web_admin import H, _client, _loop, server  # noqa: F401 -- launcher fixture dependency
from test_web_restart import _ready, _wait, launcher  # noqa: F401 -- shared process fixture


@pytest.mark.parametrize("as_admin", [False, True])
def test_start_switches_selected_document_and_record(launcher, as_admin):  # noqa: F811
    app, _tmp = launcher
    bob = _client(app, "bob", "another long secret")
    ada = _client(app, "ada", "correct horse battery")
    _loop(bob, "selection")
    assert bob.put("/api/apps/selection/file", params={"path": "other.problem.yaml"},
                   json={"text": "statement: alternate\n"}, headers=H).status_code == 200
    client, params = (ada, {"owner": "bob"}) if as_admin else (bob, {})
    base = "/api/apps/selection"
    assert client.get(base + "/state", params=params).json()["document"] == "selection.problem.yaml"
    owner = app.state.store.user(name="bob")
    directory = app.state.store.data / "users/bob/apps/selection"
    original = (directory / "selection.problem.yaml").read_text()

    for document, record in (("other.problem.yaml", "selection.other"), ("selection.problem.yaml", "selection.selection")):
        response = client.post(base + "/start", params=params,
                               json={"passes": 3, "document": document}, headers=H)
        assert response.status_code == 200, response.text
        _wait(lambda: _ready(app, "bob", "selection"))
        run = app.state.runs.latest(owner, "selection")
        argv = json.loads(run["argv"])
        assert argv[argv.index("run") + 1] == str(directory / document)
        assert run["db"] == str(directory / "out" / f"{record}.db")
        for observer, query in ((bob, {}), (ada, {"owner": "bob"})):
            state = observer.get(base + "/state", params=query).json()
            assert state["running"] and state["document"] == document
            assert observer.get(base, params=query).json()["document"] == document
        assert client.get(base + "/preflight", params=params).json()["document"] == document
        assert client.get(base + "/document", params=params).json()["document"] == document
        app.state.runs.stop(run, now=True)
        _wait(lambda: bool(app.state.store.run(run["id"]).get("ended")))
        state = client.get(base + "/state", params=params).json()
        assert not state["running"] and state["document"] == document

    assert (directory / "selection.problem.yaml").read_text() == original


def test_invalid_document_keeps_current_selection(launcher):  # noqa: F811
    app, _tmp = launcher
    bob = _client(app, "bob", "another long secret")
    _loop(bob, "selection")
    response = bob.post("/api/apps/selection/start", json={"document": "missing.problem.yaml"}, headers=H)
    assert response.status_code == 400
    state = bob.get("/api/apps/selection/state").json()
    assert state["document"] == "selection.problem.yaml" and not state["running"]
