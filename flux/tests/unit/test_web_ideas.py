"""The ideas view obeys loop permissions and historical campaign/start boundaries."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from test_web import H, _client, server  # noqa: F401 -- fixture
from test_web_run_history import _history

from flux_records import Records
from flux_loop.ideas import bind
from flux_loop.types import Candidate


@pytest.fixture()
def saved_ideas(server):  # noqa: F811
    app, _ = server
    bob, d, ids, campaigns, _ = _history(app)
    rec = Records(str(d / "out/history.db"), {"study": "old"}, name="old")
    st = SimpleNamespace(records=rec, say=lambda _: None)
    cand = Candidate("experiment", "PRIVATE_IMPLEMENTATION")
    ident, = bind(st, cand, {"idea": {"title": "A better approach", "hypothesis": "Lower latency"}})
    rec.trial(cand.to_record(), cand.name, stage="bench", strategy="loop", metrics={"cycles": 8})
    rec.close("paused")
    rec.store.close()
    return app, bob, d, ids, campaigns, ident


def test_ideas_are_available_before_any_result_and_are_owner_scoped(server):  # noqa: F811
    app, _ = server
    bob = _client(app, "bob", "another long secret")
    assert bob.post("/api/apps", data={"name": "ideas"}, files=[("files", ("problem.yaml", b"statement: ideas\n"))], headers=H).status_code == 200
    assert bob.get("/api/apps/ideas/ideas").json() == {"campaign": None, "ideas": []}
    ada = _client(app, "ada", "correct horse battery")
    assert ada.get("/api/apps/ideas/ideas").status_code == 404
    assert ada.get("/api/apps/ideas/ideas?owner=bob").status_code == 200


def test_old_campaign_ideas_and_evaluations_are_retained_and_selected_explicitly(server):  # noqa: F811
    app, _ = server
    bob, d, ids, campaigns, _old_log = _history(app)
    for campaign, title in zip(campaigns, ("Old idea", "New idea")):
        rec = Records(str(d / "out/history.db"), {"study": campaign}, name=campaign)
        rec.store.append_event(campaign, "idea", {"id": f"idea-{campaign}", "part": "", "title": title, "hypothesis": "A useful hypothesis"})
        rec.trial({"name": title, "artifact": "SECRET_SOURCE", "meta": {"idea_ids": [f"idea-{campaign}"]}}, title,
                  stage="bench", strategy="loop", metrics={"time_ms": 5})
        rec.close("paused")
    old = bob.get("/api/apps/past/ideas", params={"run_id": ids[0], "campaign": campaigns[0]})
    assert old.status_code == 200, old.text
    idea, = old.json()["ideas"]
    assert idea["title"] == "Old idea" and idea["evaluations"][0]["metrics"] == {"time_ms": 5}
    assert "SECRET_SOURCE" not in old.text and "New idea" not in old.text
    latest = bob.get("/api/apps/past/ideas", params={"campaign": campaigns[1]})
    assert latest.json()["ideas"][0]["title"] == "New idea"
    # Those notes were written after the historical start ended, so its snapshot cannot show them.
    snapshot = bob.get("/api/apps/past/ideas", params={"run_id": ids[1], "start_id": ids[0], "campaign": campaigns[0]})
    assert snapshot.json()["ideas"] == []
    assert bob.get("/api/apps/past/ideas", params={"campaign": "missing"}).status_code == 404
    assert bob.get("/api/apps/past/ideas", params={"run_id": ids[1] + 100}).status_code == 404


def test_shared_watchers_read_ideas_and_revocation_takes_effect_immediately(saved_ideas):
    app, bob, _d, _ids, campaigns, ident = saved_ideas
    app.state.store.add_user("cy", "cy has a long secret")
    cy = _client(app, "cy", "cy has a long secret")
    params = {"owner": "bob", "campaign": campaigns[0]}
    assert cy.get("/api/apps/past/ideas", params=params).status_code == 403
    assert bob.put("/api/apps/past/shares", json={"user": "cy", "perm": "watch"}, headers=H).status_code == 200
    data = cy.get("/api/apps/past/ideas", params=params)
    assert data.status_code == 200 and data.json()["ideas"][0]["id"] == ident
    assert data.json()["ideas"][0]["evaluations"][0]["metrics"] == {"cycles": 8}
    assert "PRIVATE_IMPLEMENTATION" not in data.text
    assert bob.put("/api/apps/past/shares", json={"user": "cy", "perm": None}, headers=H).status_code == 200
    assert cy.get("/api/apps/past/ideas", params=params).status_code == 403


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_ideas_api_is_read_only_even_for_the_owner(saved_ideas, method):
    _app, bob, _d, _ids, campaigns, ident = saved_ideas
    params = {"campaign": campaigns[0]}
    before = bob.get("/api/apps/past/ideas", params=params).json()
    response = bob.request(method, "/api/apps/past/ideas", params=params, json={"ideas": []}, headers=H)
    assert response.status_code == 405
    after = bob.get("/api/apps/past/ideas", params=params).json()
    assert after == before and after["ideas"][0]["id"] == ident


def test_ideas_require_authentication_even_with_explicit_history_selectors(saved_ideas):
    app, _bob, _d, ids, campaigns, _ident = saved_ideas
    client = TestClient(app)
    response = client.get("/api/apps/past/ideas", params={"owner": "bob", "run_id": ids[0], "campaign": campaigns[0]})
    assert response.status_code == 401 and "PRIVATE_IMPLEMENTATION" not in response.text


def test_another_users_or_another_apps_run_cannot_select_idea_records(saved_ideas):
    app, bob, _d, _ids, campaigns, _ident = saved_ideas
    store = app.state.store
    for owner, name in (("ada", "past"), ("bob", "other")):
        foreign = store.add_run(store.user(name=owner), name, "other.db", "other.log", [], {})
        for selector in ("run_id", "start_id"):
            response = bob.get("/api/apps/past/ideas", params={selector: foreign, "campaign": campaigns[0]})
            assert response.status_code == 404


def test_historical_snapshot_keeps_earlier_ideas_and_excludes_later_evaluations(saved_ideas):
    _app, bob, d, ids, campaigns, _ident = saved_ideas
    rec = Records(str(d / "out/history.db"), {"study": "old"}, name="old")
    st = SimpleNamespace(records=rec, say=lambda _: None)
    try:
        for at, title in ((1050, "Early hypothesis"), (2050, "Later hypothesis")):
            timestamp = datetime.fromtimestamp(at, timezone.utc).isoformat()
            with patch("flux_store.campaign._now", return_value=timestamp), patch("flux_store.store._now", return_value=timestamp):
                cand = Candidate(title, "PRIVATE_SOURCE")
                bind(st, cand, {"idea": {"title": title, "hypothesis": "Compare latency"}})
                rec.trial(cand.to_record(), title, stage="bench", strategy="loop", metrics={"cycles": at})
                if at == 1050:
                    early = cand
                else:
                    rec.trial(early.to_record(), early.name, stage="bench", strategy="loop", metrics={"cycles": 1})
        args = {"run_id": ids[1], "start_id": ids[0], "campaign": campaigns[0]}
        response = bob.get("/api/apps/past/ideas", params=args)
        assert response.status_code == 200
        idea, = response.json()["ideas"]
        assert idea["title"] == "Early hypothesis"
        assert [r["metrics"] for r in idea["evaluations"]] == [{"cycles": 1050}]
        full = bob.get("/api/apps/past/ideas", params={"campaign": campaigns[0]}).json()
        assert len(full["ideas"]) == 3
        assert "PRIVATE_SOURCE" not in response.text
    finally:
        rec.close("paused")
        rec.store.close()


@pytest.mark.parametrize("keep_history", [True, False])
def test_reset_preserves_or_clears_the_notebook_with_history(saved_ideas, monkeypatch, tmp_path, keep_history):
    _app, bob, _d, _ids, campaigns, ident = saved_ideas
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    response = bob.post("/api/apps/past/reset", json={"keep": ["history"] if keep_history else []}, headers=H)
    assert response.status_code == 200, response.text
    args = {"campaign": campaigns[0]} if keep_history else {}
    data = bob.get("/api/apps/past/ideas", params=args)
    assert data.status_code == 200, data.text
    if keep_history:
        assert data.json()["ideas"][0]["id"] == ident
        assert data.json()["ideas"][0]["evaluations"][0]["metrics"] == {"cycles": 8}
    else:
        assert data.json() == {"campaign": None, "ideas": []}
