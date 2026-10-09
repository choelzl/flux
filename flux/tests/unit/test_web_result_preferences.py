"""Project display settings are durable, shared, isolated and permission checked."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from flux_web import create_app

from test_web_admin import H, _client, _loop, server  # noqa: F401

PATH = "/api/apps/search/preferences"
CHOICES = {"graphs": {"x": "latency", "y": "power", "metrics": ["latency"], "paretoFocus": True},
           "hiddenMetrics": ["area"], "mainMetrics": ["power"], "relativeMetrics": {"power": True},
           "valuesMode": "configured", "dictionaryMetrics": {"timings": {"selected": "timings.fast", "expanded": True}}}


def test_project_preferences_survive_sessions_and_server_restarts(server):  # noqa: F811
    app, tmp = server
    bob = _client(app, "bob", "another long secret")
    _loop(bob, "search")
    _loop(bob, "other")
    assert bob.get(PATH).json() == {"preferences": {}}
    assert bob.patch(PATH, json=CHOICES, headers=H).json() == {"preferences": CHOICES}
    assert _client(app, "bob", "another long secret").get(PATH).json()["preferences"] == CHOICES
    rebuilt = create_app(tmp / "data", sandbox=False)
    assert _client(rebuilt, "bob", "another long secret").get(PATH).json()["preferences"] == CHOICES
    assert bob.get("/api/apps/search").json()["result_preferences"] == CHOICES
    rows = {r["name"]: r for r in bob.get("/api/apps").json()}
    assert rows["search"]["result_preferences"] == CHOICES
    assert rows["other"]["result_preferences"] == {}
    ada = _client(app, "ada", "correct horse battery")
    _loop(ada, "search")
    assert ada.get(PATH).json()["preferences"] == {}
    assert next(r for r in ada.get("/api/admin/apps").json() if r["owner"] == "bob" and r["name"] == "search")["result_preferences"] == CHOICES


def test_shared_preferences_use_loop_permissions(server):  # noqa: F811
    app, _ = server
    bob = _client(app, "bob", "another long secret")
    ada = _client(app, "ada", "correct horse battery")
    app.state.store.add_user("viewer", "another long secret", "external")
    viewer = _client(app, "viewer", "another long secret")
    _loop(bob, "search")
    bob.patch(PATH, json=CHOICES, headers=H)
    remote = PATH + "?owner=bob"
    assert viewer.get(remote).status_code == 403
    app.state.store.set_share("bob", "search", "viewer", "watch")
    assert viewer.get(remote).json()["preferences"] == CHOICES
    assert viewer.get("/api/shared").json()[0]["result_preferences"] == CHOICES
    assert viewer.patch(remote, json={"graphs": {"x": "area"}}, headers=H).status_code == 403
    assert viewer.delete(remote, headers=H).status_code == 403
    app.state.store.set_share("bob", "search", "viewer", "edit")
    assert viewer.patch(remote, json={"graphs": {"x": "area"}}, headers=H).status_code == 200
    assert bob.get(PATH).json()["preferences"]["graphs"] == {**CHOICES["graphs"], "x": "area"}
    assert ada.patch(remote, json={"valuesMode": "absolute"}, headers=H).status_code == 200
    assert bob.get(PATH).json()["preferences"]["valuesMode"] == "absolute"
    assert viewer.delete(remote, headers=H).json() == {"preferences": {}}


def test_patches_preserve_unrelated_and_nested_settings(server):  # noqa: F811
    app, _ = server
    bob = _client(app, "bob", "another long secret")
    _loop(bob, "search")
    bob.patch(PATH, json=CHOICES, headers=H)
    with ThreadPoolExecutor(max_workers=3) as pool:
        answers = list(pool.map(lambda patch: bob.patch(PATH, json=patch, headers=H), [
            {"graphs": {"timeStage": "bench", "metrics": []}},
            {"relativeMetrics": {"latency": False}},
            {"dictionaryMetrics": {"timings": {"expanded": False}}},
        ]))
    assert all(a.status_code == 200 for a in answers)
    saved = bob.get(PATH).json()["preferences"]
    assert saved["graphs"] == {**CHOICES["graphs"], "timeStage": "bench", "metrics": []}
    assert saved["relativeMetrics"] == {"power": True, "latency": False}
    assert saved["dictionaryMetrics"]["timings"] == {"selected": "timings.fast", "expanded": False}
    assert saved["hiddenMetrics"] == ["area"]


@pytest.mark.parametrize("patch", [
    {"owner": "ada"}, {"hiddenMetrics": 42}, {"mainMetrics": [1]}, {"hiddenMetrics": None},
    {"graphs": {"x": 1}}, {"graphs": {"paretoFocus": "yes"}}, {"graphs": {"unknown": "x"}},
    {"relativeMetrics": {"area": 1}}, {"dictionaryMetrics": {"timings": {"expanded": "true"}}},
    {"showHiddenMetrics": 1}, {"valuesMode": "unknown"}, {"graphs": {"x": "x" * 513}},
    {"hiddenMetrics": ["x"] * 1025},
])
def test_invalid_patch_is_rejected_without_changes(server, patch):  # noqa: F811
    app, _ = server
    bob = _client(app, "bob", "another long secret")
    _loop(bob, "search")
    bob.patch(PATH, json=CHOICES, headers=H)
    assert bob.patch(PATH, json=patch, headers=H).status_code == 422
    assert bob.get(PATH).json()["preferences"] == CHOICES


def test_preferences_follow_rename_transfer_clone_and_reset(server):  # noqa: F811
    app, _ = server
    bob = _client(app, "bob", "another long secret")
    ada = _client(app, "ada", "correct horse battery")
    _loop(bob, "search")
    bob.patch(PATH, json=CHOICES, headers=H)
    assert bob.post("/api/apps/search/clone", json={"to": "copy"}, headers=H).status_code == 200
    copy = "/api/apps/copy/preferences"
    assert bob.get(copy).json()["preferences"] == CHOICES
    bob.patch(copy, json={"graphs": {"x": "different"}}, headers=H)
    assert bob.get(PATH).json()["preferences"] == CHOICES
    assert bob.post("/api/apps/search/rename", json={"to": "renamed"}, headers=H).status_code == 200
    renamed = "/api/apps/renamed/preferences"
    assert bob.get(renamed).json()["preferences"] == CHOICES
    assert bob.post("/api/apps/renamed/transfer", json={"user": "ada"}, headers=H).status_code == 200
    assert ada.get(renamed).json()["preferences"] == CHOICES
    assert ada.post("/api/apps/renamed/reset", json={}, headers=H).status_code == 200
    assert ada.get(renamed).json()["preferences"] == CHOICES
    assert ada.delete("/api/apps/renamed", headers=H).status_code == 200
    _loop(ada, "renamed")
    assert ada.get(renamed).json()["preferences"] == {}


def test_authentication_csrf_missing_loop_and_impersonation(server):  # noqa: F811
    app, _ = server
    anonymous = TestClient(app)
    assert anonymous.get(PATH).status_code == 401
    assert anonymous.patch(PATH, json=CHOICES, headers=H).status_code == 401
    bob = _client(app, "bob", "another long secret")
    assert bob.get(PATH).status_code == 404
    assert bob.patch(PATH, json=CHOICES, headers=H).status_code == 404
    _loop(bob, "search")
    assert bob.patch(PATH, json=CHOICES).status_code == 403
    assert bob.delete(PATH).status_code == 403
    bob.patch(PATH, json=CHOICES, headers=H)
    ada = _client(app, "ada", "correct horse battery")
    assert ada.post("/api/users/bob/impersonate", headers=H).status_code == 200
    assert ada.get(PATH).json()["preferences"] == CHOICES
    assert ada.patch(PATH, json={"valuesMode": "relative"}, headers=H).status_code == 403
    assert ada.delete(PATH, headers=H).status_code == 403
