"""Every API route, taken from the app itself -- so a route added tomorrow is checked too: without a
login it answers 401 (but the few that are for everyone); a change without the `X-Flux` header is
refused before anything runs (403); a route an admin's dependency guards refuses a user (403), and
those are the /api/admin/ routes and the user management ones."""

from __future__ import annotations

import re

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from flux_web import create_app
from flux_web.store import Store

H = {"X-Flux": "1"}
#: What answers without a login: logging in and out.
PUBLIC = {("POST", "/api/login"), ("POST", "/api/logout")}


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    data = tmp_path_factory.mktemp("routes") / "data"
    store = Store(data)
    store.add_user("ada", "correct horse battery", "admin")
    store.add_user("bob", "another long secret")
    return create_app(data, sandbox=False)


def _routes(app):
    out = []
    for r in app.routes:
        if isinstance(r, APIRoute) and r.path.startswith("/api/"):
            for m in sorted(r.methods - {"HEAD", "OPTIONS"}):
                out.append((m, r.path, r))
    return out


def _url(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "x", path)


def _guarded_by(route, name: str) -> bool:
    todo = list(route.dependant.dependencies)
    while todo:
        d = todo.pop()
        if getattr(d.call, "__name__", "") == name:
            return True
        todo += d.dependencies
    return False


def _call(c, method, path, headers=None):
    return c.request(method, _url(path), headers=headers or {}, json={} if method != "GET" else None)


def test_there_are_routes_to_check(app):
    got = _routes(app)
    assert len(got) > 80 and any(p.startswith("/api/admin/") for _m, p, _r in got), len(got)


def test_without_a_login_every_route_but_the_public_ones_answers_401(app):
    c = TestClient(app)
    assert PUBLIC <= {(m, p) for m, p, _r in _routes(app)}
    open_ = set()
    for m, p, _r in _routes(app):
        if (m, p) in PUBLIC:
            continue
        got = _call(c, m, p, H)
        if got.status_code != 401:
            open_.add((m, p, got.status_code))
    assert not open_, f"answered without a login: {sorted(open_)}"


def test_a_change_without_the_header_is_refused_before_anything_runs(app):
    c = TestClient(app)
    assert c.post("/api/login", json={"name": "ada", "password": "correct horse battery"}, headers=H).status_code == 200
    let_through = [(m, p, _call(c, m, p).status_code) for m, p, _r in _routes(app) if m != "GET"]
    assert let_through and all(s == 403 for _m, _p, s in let_through), [x for x in let_through if x[2] != 403]


def test_the_admins_routes_refuse_a_user_and_are_the_admin_and_user_management_ones(app):
    c = TestClient(app)
    assert c.post("/api/login", json={"name": "bob", "password": "another long secret"}, headers=H).status_code == 200
    guarded = [(m, p) for m, p, r in _routes(app) if _guarded_by(r, "admin_of")]
    assert guarded, "no route uses admin_of?"
    wrong = [(m, p, s) for m, p in guarded if (s := _call(c, m, p, H).status_code) != 403]
    assert not wrong, f"a user got past an admin's route: {wrong}"
    unguarded_admin = [(m, p) for m, p, r in _routes(app) if p.startswith("/api/admin/") and not _guarded_by(r, "admin_of")]
    assert not unguarded_admin, f"an /api/admin/ route without the admin's guard: {unguarded_admin}"
