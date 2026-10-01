"""D699: the machine over time -- a sample a minute in the server's database, a week kept, read
back thinned with bursts kept -- for admins only."""

from __future__ import annotations

import time

from fastapi.testclient import TestClient

from flux_web import create_app
from flux_web.history import KEEP_S, History
from flux_web.store import Store

H = {"X-Flux": "1"}


def test_samples_are_kept_a_week_and_thinned_with_their_bursts(tmp_path):
    hist = History(tmp_path / "h.db")
    now = time.time()
    hist.add({"load1": 9.0}, t=now - KEEP_S - 60)                    # past the week: dropped at the next add
    for i in range(300):
        hist.add({"load1": 50.0 if i == 150 else 1.0, "mem_used": 2.0, "disks": {"data": 0.5}}, t=now - 300 * 60 + i * 60)
    got = hist.read(hours=24, points=100)
    assert len(got) == 100 and got == sorted(got, key=lambda s: s["t"])
    assert max(s["load1"] for s in got) == 50.0, "a one-minute burst survives the thinning"
    assert all(abs(s["mem_used"] - 2.0) < 1e-9 and abs(s["disks"]["data"] - 0.5) < 1e-9 for s in got)
    assert len(hist.read(hours=1, points=1000)) <= 61
    assert all(s["load1"] != 9.0 for s in hist.read(hours=24 * 8, points=5000)), "older than a week: gone"


def test_the_history_is_the_admins_and_a_sample_says_the_machine(tmp_path):
    store = Store(tmp_path / "data")
    store.add_user("ada", "correct horse battery", "admin")
    store.add_user("bob", "another long secret")
    app = create_app(tmp_path / "data", sandbox=False)
    sample = app.state.sample()
    assert sample["cpus"] >= 1 and sample["mem_total"] > 0 and sample["loops"] == 0 and sample["disks"]
    app.state.history.add(sample)
    ada, bob = TestClient(app), TestClient(app)
    ada.post("/api/login", json={"name": "ada", "password": "correct horse battery"}, headers=H)
    bob.post("/api/login", json={"name": "bob", "password": "another long secret"}, headers=H)
    assert bob.get("/api/admin/history").status_code == 403
    got = ada.get("/api/admin/history", params={"hours": 1}).json()
    assert len(got["samples"]) == 1 and got["sampling"] is False, "only `flux serve` samples"
