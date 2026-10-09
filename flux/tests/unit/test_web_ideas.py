"""The ideas view obeys loop permissions and historical campaign/start boundaries."""
from __future__ import annotations

from test_web import H, _client, server  # noqa: F401 -- fixture
from test_web_run_history import _history

from flux_records import Records


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
