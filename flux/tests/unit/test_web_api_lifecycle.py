"""Existing sessions recheck sharing and account status for historical data and mutations."""

from __future__ import annotations

import pytest

from test_web import H, _client, server  # noqa: F401 -- shared fixture
from test_web_run_history import _history


@pytest.fixture
def shared_history(server):  # noqa: F811
    app, _tmp = server
    bob, folder, starts, campaigns, _log = _history(app)
    app.state.store.add_user("cy", "cy has a long secret")
    cy = _client(app, "cy", "cy has a long secret")
    params = {"owner": "bob", "run_id": starts[1], "start_id": starts[0], "campaign": campaigns[0]}
    return app, bob, cy, folder, params


READS = [("runs", {}), ("log/raw", {}), ("file", {"path": "problem.yaml", "raw": True}),
         ("run-data", {"kind": "events"}), ("run-data", {"kind": "turns"}),
         ("turns", {}), ("results", {}), ("design", {"design": "same-name"}), ("report", {})]


@pytest.mark.parametrize("perm", ["watch", "edit"])
@pytest.mark.parametrize("path,extra", READS, ids=["starts", "raw-log", "raw-file", "journal", "transcript", "turns", "results", "source", "report"])
def test_revoking_and_restoring_a_share_takes_effect_without_logging_in_again(shared_history, perm, path, extra):
    _app, bob, cy, _folder, params = shared_history
    share = lambda permission: bob.put("/api/apps/past/shares", json={"user": "cy", "perm": permission}, headers=H)  # noqa: E731
    assert share(perm).status_code == 200
    before = cy.get(f"/api/apps/past/{path}", params={**params, **extra})
    assert before.status_code == 200, before.text
    assert share(None).status_code == 200
    assert cy.get("/api/me").status_code == 200, "revoking a loop does not log the user out"
    denied = cy.get(f"/api/apps/past/{path}", params={**params, **extra})
    assert denied.status_code == 403
    assert not any(secret in denied.text for secret in ("old source", "old prompt", "OLD tool output"))
    assert cy.get("/api/shared").json() == []
    assert share(perm).status_code == 200
    assert cy.get(f"/api/apps/past/{path}", params={**params, **extra}).status_code == 200


MUTATIONS = [("PUT", "file", {"path": "notes.txt"}, {"text": "forbidden"}),
             ("DELETE", "file", {"path": "notes.txt"}, None),
             ("POST", "move", {}, {"path": "notes.txt", "to": "moved.txt"}),
             ("PUT", "env", {}, {"name": "MY_VAR", "value": "forbidden", "secret": False}),
             ("POST", "notes", {}, {"text": "forbidden"}),
             ("POST", "start", {}, {"passes": 1}), ("POST", "stop", {}, {"now": True}),
             ("PUT", "document", {}, {"text": "statement: forbidden\n"})]


@pytest.mark.parametrize("method,path,extra,body", MUTATIONS, ids=["save", "delete", "move", "env", "note", "start", "stop", "document"])
def test_downgrading_an_editor_rejects_pending_mutations_without_side_effects(shared_history, monkeypatch, method, path, extra, body):
    app, bob, cy, folder, _params = shared_history
    assert bob.put("/api/apps/past/shares", json={"user": "cy", "perm": "edit"}, headers=H).status_code == 200
    assert cy.put("/api/apps/past/file", params={"owner": "bob", "path": "notes.txt"}, json={"text": "saved"}, headers=H).status_code == 200
    app.state.store.set_env("loop:bob:past", "MY_VAR", "saved", False)
    files = {str(p.relative_to(folder)): p.read_bytes() for p in folder.rglob("*") if p.is_file()}
    starts = app.state.store.runs()
    notes = bob.get("/api/apps/past/notes").json()
    monkeypatch.setattr(app.state.runs, "start", lambda *_a, **_k: pytest.fail("a downgraded editor started a process"))
    monkeypatch.setattr(app.state.runs, "stop", lambda *_a, **_k: pytest.fail("a downgraded editor stopped a process"))
    assert bob.put("/api/apps/past/shares", json={"user": "cy", "perm": "watch"}, headers=H).status_code == 200
    response = cy.request(method, f"/api/apps/past/{path}", params={"owner": "bob", **extra}, json=body, headers=H)
    assert response.status_code == 403, response.text
    assert {str(p.relative_to(folder)): p.read_bytes() for p in folder.rglob("*") if p.is_file()} == files
    assert app.state.store.runs() == starts and bob.get("/api/apps/past/notes").json() == notes
    assert app.state.store.env("loop:bob:past")["MY_VAR"]["value"] == "saved"
    assert cy.get("/api/apps/past/file", params={"owner": "bob", "path": "notes.txt"}).text == "saved"


def test_disabling_an_account_invalidates_existing_sessions_and_raw_downloads(shared_history):
    app, bob, cy, _folder, params = shared_history
    assert bob.put("/api/apps/past/shares", json={"user": "cy", "perm": "watch"}, headers=H).status_code == 200
    assert cy.get("/api/apps/past/log/raw", params=params).status_code == 200
    ada = _client(app, "ada", "correct horse battery")
    assert ada.patch("/api/users/cy", json={"disabled": True}, headers=H).status_code == 200
    for path, extra in READS:
        assert cy.get(f"/api/apps/past/{path}", params={**params, **extra}).status_code == 401
    assert cy.get("/api/me").status_code == 401
    assert bob.get("/api/apps/past/runs").status_code == 200
    assert app.state.store.shares("bob", "past")["cy"] == "watch", "the loop's sharing and records survive"
