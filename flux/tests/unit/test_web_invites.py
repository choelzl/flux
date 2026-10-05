"""D818: a new user is invited -- a one-time link to choose their password; a reset is a link too.
The link works once, for a week, for that user alone; a new one replaces it; using it logs in and
ends the user's other sessions."""

from __future__ import annotations

import time

from fastapi.testclient import TestClient

from flux_web import create_app
from flux_web.store import Store

H = {"X-Flux": "1"}


def _app(tmp_path):
    store = Store(tmp_path / "data")
    store.add_user("ada", "correct horse battery", "admin")
    return store, create_app(tmp_path / "data", sandbox=False)


def test_an_invited_user_chooses_their_password_from_the_link(tmp_path):
    store, app = _app(tmp_path)
    ada = TestClient(app)
    ada.post("/api/login", json={"name": "ada", "password": "correct horse battery"}, headers=H)
    got = ada.post("/api/users", json={"name": "bob", "role": "internal"}, headers=H).json()
    assert got["kind"] == "invite" and len(got["token"]) > 30
    assert {u["name"]: u["pending"] for u in ada.get("/api/users").json()} == {"ada": False, "bob": True}
    anon = TestClient(app)
    assert anon.post("/api/login", json={"name": "bob", "password": ""}, headers=H).status_code == 401, "no password works before"
    info = anon.get(f"/api/invite/{got['token']}").json()
    assert info["name"] == "bob" and info["kind"] == "invite" and info["expires"] > time.time() + 6 * 86400
    assert anon.post(f"/api/invite/{got['token']}", json={"text": "short"}, headers=H).status_code == 400
    r = anon.post(f"/api/invite/{got['token']}", json={"text": "bob's long secret"}, headers=H)
    assert r.status_code == 200 and r.json()["name"] == "bob"
    assert anon.get("/api/me").json()["name"] == "bob", "logged in by the link"
    assert anon.get(f"/api/invite/{got['token']}").status_code == 404, "once"
    assert TestClient(app).post("/api/login", json={"name": "bob", "password": "bob's long secret"}, headers=H).status_code == 200
    assert not store.pending("bob")


def test_a_reset_link_replaces_the_last_and_ends_the_sessions(tmp_path):
    store, app = _app(tmp_path)
    store.add_user("cy", "cy's first secret")
    cy = TestClient(app)
    cy.post("/api/login", json={"name": "cy", "password": "cy's first secret"}, headers=H)
    ada = TestClient(app)
    ada.post("/api/login", json={"name": "ada", "password": "correct horse battery"}, headers=H)
    assert cy.post("/api/users/cy/link", headers=H).status_code == 403, "an admin's"
    first = ada.post("/api/users/cy/link", headers=H).json()
    second = ada.post("/api/users/cy/link", headers=H).json()
    assert first["kind"] == second["kind"] == "reset"
    assert TestClient(app).get(f"/api/invite/{first['token']}").status_code == 404, "a new link replaces the last"
    assert cy.get("/api/me").status_code == 200, "the password and sessions hold until the link is used"
    anon = TestClient(app)
    assert anon.post(f"/api/invite/{second['token']}", json={"text": "cy's second secret"}, headers=H).status_code == 200
    assert cy.get("/api/me").status_code == 401, "the other sessions end"
    assert TestClient(app).post("/api/login", json={"name": "cy", "password": "cy's first secret"}, headers=H).status_code == 401
    assert ada.post("/api/users/nobody/link", headers=H).status_code == 404
    assert TestClient(app).get("/api/invite/not-a-token").status_code == 404


def test_the_command_line_invites_too(tmp_path, capsys):
    from flux_cli.main import main

    Store(tmp_path / "data")
    assert main(["user", "add", "dee", "--invite", "--url", "https://flux.example", "--data", str(tmp_path / "data")]) == 0
    out = capsys.readouterr().out
    assert "https://flux.example/#/invite/" in out and Store(tmp_path / "data").pending("dee")
