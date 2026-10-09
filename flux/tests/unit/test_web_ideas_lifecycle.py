"""Ideas stay with a loop through ownership changes and obey current access rights."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from flux_loop.ideas import bind, propose
from flux_loop.types import Candidate
from flux_records import Records

from test_web import H, _client, server  # noqa: F401 -- shared fixture
from test_web_relocate import moving_loop  # noqa: F401 -- shared fixture
from test_web_ideas import saved_ideas  # noqa: F401 -- shared fixture


@pytest.fixture()
def moving_notebook(moving_loop):  # noqa: F811
    app, bob, cy, ada, _w, _d, _cache, ids = moving_loop
    links = {}
    for rid, cid in zip(ids, ("x", "x.alt")):
        rec = Records(app.state.store.run(rid)["db"], {"study": cid}, name=cid)
        try:
            st = SimpleNamespace(records=rec, say=lambda _: None)
            cand = Candidate(f"{cid}#idea", "PRIVATE_SOURCE", subgoal="core")
            ident, = bind(st, cand, {"idea": {"title": f"{cid} hypothesis", "hypothesis": "Reduce latency"}})
            propose(st, "core", {"title": f"{cid} alternative", "hypothesis": "Try a different structure"})
            rec.trial(cand.to_record(), cand.name, stage="gate", strategy="loop", metrics=None, error="incorrect corner")
            rec.trial(cand.to_record(), cand.name, stage="bench", strategy="loop", metrics={"cycles": 4})
            links[rid] = ident
        finally:
            rec.close("paused")
            rec.store.close()
    return app, bob, cy, ada, ids, links


def assert_notebook(client, name, params, ident):
    response = client.get(f"/api/apps/{name}/ideas", params=params)
    assert response.status_code == 200, response.text
    ideas = response.json()["ideas"]
    tested = next(i for i in ideas if i["id"] == ident)
    assert len(ideas) == 2 and tested["status"] == "measured"
    assert tested["evaluations"][0]["error"] == "incorrect corner"
    assert tested["evaluations"][1]["metrics"] == {"cycles": 4}
    assert next(i for i in ideas if i["id"] != ident)["evaluations"] == []
    assert "PRIVATE_SOURCE" not in response.text


@pytest.mark.parametrize("name", ["Renamed-2", "new_name"])
def test_rename_preserves_ids_evidence_and_shared_access_in_each_campaign(moving_notebook, name):
    _app, bob, cy, _ada, ids, links = moving_notebook
    response = bob.post("/api/apps/x/rename", json={"to": name}, headers=H)
    assert response.status_code == 200, response.text
    assert bob.get("/api/apps/x/ideas").status_code == 404
    for rid, cid in zip(ids, (name, name + ".alt")):
        args = {"run_id": rid, "campaign": cid}
        assert_notebook(bob, name, args, links[rid])
        assert_notebook(cy, name, {**args, "owner": "bob"}, links[rid])


@pytest.mark.parametrize("keep_permissions", [False, True])
def test_admin_transfer_keeps_ideas_independently_of_sandbox_permissions(moving_notebook, keep_permissions):
    app, bob, cy, ada, ids, links = moving_notebook
    response = ada.post("/api/apps/x/transfer", params={"owner": "bob"},
                        json={"user": "cy", "to": "Transferred_2", "keep_permissions": keep_permissions}, headers=H)
    assert response.status_code == 200, response.text
    for rid, cid in zip(ids, ("Transferred_2", "Transferred_2.alt")):
        assert_notebook(cy, "Transferred_2", {"run_id": rid, "campaign": cid}, links[rid])
        assert bob.get("/api/apps/Transferred_2/ideas", params={"owner": "cy", "run_id": rid}).status_code == 403
    advanced = app.state.store.server_get("adv:cy:Transferred_2") or {}
    assert (advanced.get("sandbox") is False) == keep_permissions
    assert bool(advanced.get("mounts")) == keep_permissions
    assert bool(advanced.get("raw_network")) == keep_permissions


@pytest.mark.parametrize("workbench", [False, True])
def test_cloning_a_shared_loop_does_not_copy_ideas_or_evaluations(moving_notebook, workbench):
    app, _bob, cy, _ada, ids, links = moving_notebook
    response = cy.post("/api/apps/x/clone", params={"owner": "bob"},
                       json={"to": "Cloned_2", "workbench": workbench}, headers=H)
    assert response.status_code == 200, response.text
    assert cy.get("/api/apps/Cloned_2/ideas").json() == {"campaign": None, "ideas": []}
    assert app.state.store.runs(app.state.store.user(name="cy"), "Cloned_2") == []
    assert_notebook(cy, "x", {"owner": "bob", "run_id": ids[0], "campaign": "x"}, links[ids[0]])


def test_view_as_user_obeys_the_targets_notebook_access_and_share_revocation(saved_ideas):  # noqa: F811
    app, bob, _d, _ids, campaigns, ident = saved_ideas
    app.state.store.add_user("cy", "cy has a long secret")
    ada = _client(app, "ada", "correct horse battery")
    params = {"owner": "bob", "campaign": campaigns[0]}
    assert ada.get("/api/apps/past/ideas", params=params).status_code == 200
    assert ada.post("/api/users/cy/impersonate", headers=H).status_code == 200
    assert ada.get("/api/apps/past/ideas", params=params).status_code == 403
    assert bob.put("/api/apps/past/shares", json={"user": "cy", "perm": "watch"}, headers=H).status_code == 200
    visible = ada.get("/api/apps/past/ideas", params=params)
    assert visible.status_code == 200 and visible.json()["ideas"][0]["id"] == ident
    assert bob.put("/api/apps/past/shares", json={"user": "cy", "perm": None}, headers=H).status_code == 200
    assert ada.get("/api/apps/past/ideas", params=params).status_code == 403
    assert ada.delete("/api/impersonation", headers=H).status_code == 200
    assert ada.get("/api/apps/past/ideas", params=params).json()["ideas"][0]["id"] == ident


def test_disabled_users_cannot_read_saved_notebooks_with_existing_sessions(saved_ideas):  # noqa: F811
    app, bob, _d, _ids, campaigns, _ident = saved_ideas
    assert bob.get("/api/apps/past/ideas", params={"campaign": campaigns[0]}).status_code == 200
    app.state.store.set_user("bob", disabled=True)
    assert bob.get("/api/apps/past/ideas", params={"campaign": campaigns[0]}).status_code == 401
