"""Resource history keeps a month, progressively compacts older samples, and preserves outages."""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from flux_web import create_app
from flux_web.history import DAY, KEEP_S, History
from flux_web.store import Store

H = {"X-Flux": "1"}


def test_outages_survive_thinning_without_averaging_across_them(tmp_path):
    hist = History(tmp_path / "h.db")
    now = time.time()
    for offset, load in [(-3600, 1), (-3540, 1), (-3480, 1), (-600, 9), (-540, 9), (-480, 9)]:
        hist.add({"load1": load}, t=now + offset)
    raw = hist.read(points=100)
    assert [s["t"] for s in raw if s.get("gap_before")] == [now - 600]
    # A single nominal bucket straddles the outage and must split into two.
    thinned = hist.read(points=1)
    assert [s["load1"] for s in thinned] == [1, 9]
    assert thinned[1]["gap_before"] and thinned[1]["start_t"] == now - 600


def test_samples_are_kept_a_month_and_thinned_with_their_bursts(tmp_path):
    hist = History(tmp_path / "h.db")
    now = time.time()
    hist.add({"load1": 9.0}, t=now - KEEP_S - 60)
    for i in range(300):
        hist.add({"load1": 50.0 if i == 150 else 1.0, "mem_used": 2.0, "disks": {"data": 0.5}}, t=now - 300 * 60 + i * 60)
    got = hist.read(hours=24, points=100)
    assert len(got) == 100 and got == sorted(got, key=lambda s: s["t"])
    assert max(s["load1"] for s in got) == 50.0, "a one-minute burst survives the thinning"
    assert all(abs(s["mem_used"] - 2.0) < 1e-9 and abs(s["disks"]["data"] - 0.5) < 1e-9 for s in got)
    assert len(hist.read(hours=1, points=1000)) <= 61
    assert all(s["load1"] != 9.0 for s in hist.read(hours=720, points=5000)), "older than a month: gone"


NOW = 100 * DAY


def seed(hist, samples):
    # Also exercises legacy rows: no compaction metadata or schema migration required.
    with hist._con() as c:
        c.executemany("INSERT INTO samples VALUES (?, ?)", [(t, json.dumps(s)) for t, s in samples])


def rows(hist):
    with hist._con() as c:
        return c.execute("SELECT t, data FROM samples ORDER BY t").fetchall()


def test_a_month_of_minute_samples_compacts_progressively_and_keeps_peaks(tmp_path, monkeypatch):
    monkeypatch.setattr("flux_web.history.time.time", lambda: NOW)
    hist = History(tmp_path / "h.db")
    seed(hist, ((t, {"load1": 50 if t == NOW - 20 * DAY else 1,
                     "cpu": 77 if t == NOW - 2 * DAY else 0,
                     "mem_used": 10 if t >= NOW - DAY else 20, "disks": {"data": .5}})
                for t in range(NOW - KEEP_S - 60, NOW, 60)))
    hist.compact()
    stored = rows(hist)
    old = [(t, json.loads(d)) for t, d in stored if t < NOW - 7 * DAY]
    middle = [(t, json.loads(d)) for t, d in stored if NOW - 7 * DAY <= t < NOW - DAY]
    recent = [(t, json.loads(d)) for t, d in stored if t >= NOW - DAY]
    assert len(old) == 23 * 48 and all(s["_count"] == 30 for _, s in old)
    assert len(middle) == 6 * 288 and all(s["_count"] == 5 for _, s in middle)
    assert len(recent) == 1440 and all("_count" not in s for _, s in recent)
    assert len(stored) < 4300, "a month needs about a tenth of the minute-only rows"
    got = hist.read(hours=720, points=5000)
    assert not any(s.get("gap_before") for s in got), "coarser samples do not invent outages"
    assert all(not any(k.startswith("_") for k in s) for s in got), "weights stay internal"
    thinned = hist.read(hours=720, points=1)
    assert len(thinned) == 1 and thinned[0]["load1"] == 50 and thinned[0]["cpu"] == 77
    assert thinned[0]["mem_used"] == pytest.approx((29 * 20 + 10) / 30)
    assert thinned[0]["disks"]["data"] == .5
    hist.compact()
    assert rows(hist) == stored, "repeated compaction is idempotent"
    with hist._con() as c:
        hist._compact(c, NOW)
        assert c.total_changes == 0, "already compacted buckets produce no database writes"
        hist._compact(c, NOW + 3600)
        assert 0 < c.total_changes < 150, "aging another hour only rewrites the affected buckets"


def test_uneven_buckets_keep_original_weights_when_they_age_to_the_next_tier(tmp_path, monkeypatch):
    clock = [NOW]
    monkeypatch.setattr("flux_web.history.time.time", lambda: clock[0])
    hist = History(tmp_path / "h.db")
    base = NOW - 2 * DAY
    seed(hist, [(base + i * 60, {"mem_used": 10, "disks": {"data": .1}}) for i in range(5)]
              + [(base + 300, {"mem_used": 100, "disks": {"data": .7}})])
    hist.compact()
    assert [json.loads(d)["_count"] for _, d in rows(hist)] == [5, 1]
    assert hist.read(hours=720, points=1)[0]["mem_used"] == 25
    clock[0] += 6 * DAY
    hist.compact()
    assert len(rows(hist)) == 1 and json.loads(rows(hist)[0][1])["_count"] == 6
    got = hist.read(hours=720)[0]
    assert got["mem_used"] == 25 and got["disks"]["data"] == pytest.approx(.2)
    stored = rows(hist)
    hist.compact()
    assert rows(hist) == stored


@pytest.mark.parametrize("age", [2, 10])
def test_outages_inside_compaction_buckets_remain_empty(tmp_path, monkeypatch, age):
    monkeypatch.setattr("flux_web.history.time.time", lambda: NOW)
    hist = History(tmp_path / "h.db")
    base = NOW - age * DAY
    # The 240-second outage fits inside both the five- and thirty-minute tiers.
    seed(hist, [(base, {"load1": 1}), (base + 240, {"load1": 9}), (base + 300, {"load1": 9})])
    hist.compact()
    got = hist.read(hours=720, points=1)
    assert [s["load1"] for s in got] == [1, 9]
    assert got[1]["gap_before"] and got[1]["start_t"] == base + 240
    hist.compact()
    assert hist.read(hours=720, points=1) == got


def test_sampler_compacts_hourly_and_ignores_future_samples(tmp_path, monkeypatch):
    clock = [NOW]
    monkeypatch.setattr("flux_web.history.time.time", lambda: clock[0])
    hist = History(tmp_path / "h.db")
    compact = hist._compact
    calls = []

    def counted(connection, now):
        calls.append(now)
        compact(connection, now)

    monkeypatch.setattr(hist, "_compact", counted)
    hist.add({"load1": 1})
    clock[0] += 60
    hist.add({"load1": 2})
    hist.add({"load1": 99}, t=NOW + DAY)
    assert calls == [NOW] and len(hist.read()) == 2
    clock[0] = NOW + 3600
    hist.add({"load1": 3})
    assert calls == [NOW, NOW + 3600]


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
    app.state.history.add({**sample, "load1": 42}, t=time.time() - 20 * DAY)
    month = ada.get("/api/admin/history", params={"hours": 720}).json()
    assert month["hours"] == 720 and any(s["load1"] == 42 for s in month["samples"])
    for route in ("history", "token-rate"):
        assert bob.get(f"/api/admin/{route}", params={"hours": 720}).status_code == 403
        for requested, expected in ((720, 720), (9999, 720), (0, .25)):
            response = ada.get(f"/api/admin/{route}", params={"hours": requested})
            assert response.status_code == 200 and response.json()["hours"] == expected
            if route == "token-rate":
                assert response.json()["bucket_seconds"] == expected * 3600 / 180
