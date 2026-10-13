"""D1043: a result rejected -- by a critic or by hand -- is an event on the record: shown, never chosen;
restored by hand, or resolved when the R pass measures it again."""

from flux_store import CampaignStore


def test_a_rejection_stands_until_restored_or_measured_again(tmp_path):
    store = CampaignStore(str(tmp_path / "r.db"))
    cid, _new = store.start_campaign({"m": 1}, "h", "t")
    a = store.reject(cid, key="k1", name="d#1", why="wrong clock", by="bob", cleared=[{"stage": "s", "metric": "t", "value": 3.0}])
    b = store.reject(cid, key="k2", name="d#2", why="stale bench", by="the measure critic (s)", again=["s"])
    assert [(r["key"], r["state"]) for r in store.rejections()] == [("k1", "active"), ("k2", "active")]
    assert store.rejections()[0]["cleared"] == [{"stage": "s", "metric": "t", "value": 3.0}] and store.rejections()[1]["again"] == ["s"]
    assert store.unreject(cid, a, by="bob") and not store.unreject(cid, 999, by="bob")
    store.remeasured(cid, [b], ["k2"])
    assert store.rejections() == [] and [r["state"] for r in store.rejections(active=False)] == ["restored", "remeasured"]
    other, _new = store.start_campaign({"m": 1}, "h2", "t2")
    store.reject(other, key="k3", name="d#3", why="w", by="bob")
    assert [r["key"] for r in store.rejections()] == ["k3"], "every campaign of the record"
    store.close()
