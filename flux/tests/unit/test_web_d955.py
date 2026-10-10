"""D955: a loop's library digests are readable in the web (Results › Library), as `flux knowledge
show` printed them before D952 -- the record's own, owner-scoped, empty before any run."""
from __future__ import annotations

from test_web import H, _client, server  # noqa: F401 -- fixture
from test_web_run_history import _history


def test_the_records_digests_are_listed_and_scoped_to_who_may_see_the_loop(server):  # noqa: F811
    from flux_knowledge.digest import RECIPE
    from flux_store import CampaignStore

    app, _ = server
    bob = _client(app, "bob", "another long secret")
    assert bob.post("/api/apps", data={"name": "fresh"}, files=[("files", ("problem.yaml", b"statement: s\n"))],
                    headers=H).status_code == 200
    assert bob.get("/api/apps/fresh/digests").json() == {"digests": []}, "no run, no record: nothing"

    bob, d, _ids, _campaigns, _ = _history(app)
    store = CampaignStore(str(d / "out/history.db"))
    store.results.put_document("digest", {"source": "library/PACE.pdf", "hash": "h", "recipe": RECIPE, "chars": 1234,
                                          "model": "agent:claude", "digest": "PACE: a method\n- 1 ULP at 16 segments"})
    store.results.put_document("digest", {"source": "library/old.pdf", "hash": "o", "recipe": "an older recipe",
                                          "chars": 9, "model": "m", "digest": "never shown"})
    store.close()
    got = bob.get("/api/apps/past/digests")
    assert got.status_code == 200, got.text
    (one,) = got.json()["digests"]
    assert one == {"source": "library/PACE.pdf", "digest": "PACE: a method\n- 1 ULP at 16 segments", "chars": 1234,
                   "model": "agent:claude", "reused": False}
    ada = _client(app, "ada", "correct horse battery")
    assert ada.get("/api/apps/past/digests").status_code == 404, "another user's loop is not theirs to list"
    assert ada.get("/api/apps/past/digests?owner=bob").status_code == 200, "an admin reads it by owner"
