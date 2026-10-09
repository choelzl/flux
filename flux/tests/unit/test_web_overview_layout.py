"""Overview layouts are validated personal preferences, persisted across browsers and loops."""

from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from flux_web import create_app
from flux_web.models import OVERVIEW_DEFAULT

from test_web_admin import H, _client, server  # noqa: F401

PATH = "/api/preferences/overview"
CUSTOM = {"stats": ["tokens_out", "state", "cost"], "columns": [["best", "usage"], ["decision"]]}


def test_layout_persists_across_sessions_and_server_restarts(server):  # noqa: F811
    app, tmp = server
    bob = _client(app, "bob", "another long secret")
    initial = bob.get(PATH).json()
    assert initial["layout"] == OVERVIEW_DEFAULT and initial["default"] == OVERVIEW_DEFAULT
    assert set(initial["small_cards"]) >= set(CUSTOM["stats"])
    assert set(initial["large_cards"]) >= set(sum(CUSTOM["columns"], []))
    assert bob.put(PATH, json=CUSTOM, headers=H).json() == {"layout": CUSTOM}
    another_browser = _client(app, "bob", "another long secret")
    assert another_browser.get(PATH).json()["layout"] == CUSTOM
    recreated = create_app(tmp / "data", sandbox=False)
    assert _client(recreated, "bob", "another long secret").get(PATH).json()["layout"] == CUSTOM
    ada = _client(app, "ada", "correct horse battery")
    assert ada.get(PATH).json()["layout"] == OVERVIEW_DEFAULT
    assert ada.put(PATH, json={**CUSTOM, "stats": ["designs", "state", "passes", "tokens_in"]}, headers=H).status_code == 200
    assert bob.get(PATH).json()["layout"] == CUSTOM
    assert another_browser.delete(PATH, headers=H).json() == {"layout": OVERVIEW_DEFAULT}
    assert bob.get(PATH).json()["layout"] == OVERVIEW_DEFAULT
    assert ada.get(PATH).json()["layout"] != OVERVIEW_DEFAULT


@pytest.mark.parametrize("mutation", [
    {"stats": ["state", "passes"]},
    {"stats": ["state", "passes", "designs", "usage", "objective", "cost"]},
    {"stats": ["state", "state", "designs"]},
    {"stats": ["state", "passes", "unknown"]},
    {"stats": "state"},
    {"columns": [["best"]]},
    {"columns": [[], [], []]},
    {"columns": [["best"], ["best"]]},
    {"columns": [["decision", "decision"], []]},
    {"columns": [["unknown"], []]},
    {"columns": [["tokens_in"], []]},
    {"owner": "ada"},
])
def test_invalid_layout_never_overwrites_saved_preferences(server, mutation):  # noqa: F811
    app, _ = server
    bob = _client(app, "bob", "another long secret")
    assert bob.put(PATH, json=CUSTOM, headers=H).status_code == 200
    assert bob.put(PATH, json={**deepcopy(CUSTOM), **mutation}, headers=H).status_code == 422
    assert bob.get(PATH).json()["layout"] == CUSTOM


def test_authentication_csrf_and_complete_body_required(server):  # noqa: F811
    app, _ = server
    anonymous = TestClient(app)
    assert anonymous.get(PATH).status_code == 401
    assert anonymous.put(PATH, json=CUSTOM, headers=H).status_code == 401
    assert anonymous.delete(PATH, headers=H).status_code == 401
    bob = _client(app, "bob", "another long secret")
    assert bob.put(PATH, json=CUSTOM).status_code == 403
    assert bob.delete(PATH).status_code == 403
    assert bob.put(PATH, json={"stats": CUSTOM["stats"]}, headers=H).status_code == 422
    assert bob.get(PATH).json()["layout"] == OVERVIEW_DEFAULT


def test_preferences_do_not_require_loop_or_server_access(server):  # noqa: F811
    app, _ = server
    app.state.store.add_user("limited", "another long secret", "external", permissions={"create_loops": False, "run_loops": False})
    limited = _client(app, "limited", "another long secret")
    assert limited.put(PATH, json={**CUSTOM, "columns": [[], []]}, headers=H).status_code == 200
    assert limited.get(PATH).json()["layout"]["columns"] == [[], []]


def test_impersonation_reads_target_layout_but_cannot_change_it(server):  # noqa: F811
    app, _ = server
    bob = _client(app, "bob", "another long secret")
    bob.put(PATH, json=CUSTOM, headers=H)
    ada = _client(app, "ada", "correct horse battery")
    assert ada.post("/api/users/bob/impersonate", headers=H).status_code == 200
    assert ada.get(PATH).json()["layout"] == CUSTOM
    assert ada.put(PATH, json=OVERVIEW_DEFAULT, headers=H).status_code == 403
    assert ada.delete(PATH, headers=H).status_code == 403
    assert ada.delete("/api/impersonation", headers=H).status_code == 200
    assert ada.get(PATH).json()["layout"] == OVERVIEW_DEFAULT
    assert bob.get(PATH).json()["layout"] == CUSTOM


def test_corrupt_saved_layout_falls_back_to_defaults(server):  # noqa: F811
    app, _ = server
    user = app.state.store.user(name="bob")
    app.state.store.server_set(f"overview:user:{user.id}", {"stats": ["removed"], "columns": None})
    bob = _client(app, "bob", "another long secret")
    assert bob.get(PATH).json()["layout"] == OVERVIEW_DEFAULT
