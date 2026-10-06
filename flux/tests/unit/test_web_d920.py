"""D920: "Over the last" is one interval -- 24 h, 7 or 30 days back from now, rolling -- applied
before every historical count (never widened to seven days), said back as `range`; buckets only for
the sparklines; the disk is current and measured apart, with its time; a part asked alone."""

from __future__ import annotations

import json
import time

from test_web import H, _client, server  # noqa: F401 -- the fixture

from flux_web import insights as ins

DAY = ins.DAY
NOW = 100 * DAY + 12 * 3600                                      # noon UTC


def _rows(ages_and_tokens):
    return [("ada", "nlu", NOW - age, "turn", "qwen", "x · qwen", True, 1.0, float(tok), 0.0, tok / 100, "")
            for age, tok in ages_and_tokens]


def test_one_day_is_the_last_24_hours_not_seven_days():
    """The review's fixture: activity 0.1, 3, 20 and 45 days old with 100 / 1,000 / 10,000 /
    100,000 tokens. A day counted 1,100 (seven days' worth); now 100."""
    rows = _rows([(0.1 * DAY, 100), (3 * DAY, 1000), (20 * DAY, 10000), (45 * DAY, 100000)])
    total = lambda u: sum(u["users"]["ada"]["tokens"]) if u["users"] else 0   # noqa: E731
    assert total(ins.usage_by_day(rows, 1, NOW)) == 100
    assert total(ins.usage_by_day(rows, 7, NOW)) == 1100
    assert total(ins.usage_by_day(rows, 30, NOW)) == 11100
    one = ins.usage_by_day(rows, 1, NOW)
    assert one["bucket"] == "hour" and len(one["days"]) == 24 and one["top"][0]["tokens"] == 100
    seven = ins.usage_by_day(rows, 7, NOW)
    assert seven["bucket"] == "day" and len(seven["days"]) == 7


def test_the_interval_has_both_ends():
    """Rolling: 24 h back from noon is yesterday noon, not today's midnight; a turn just inside
    counts, just outside not; a turn after now (a clock ahead) not."""
    rows = _rows([(DAY - 1, 1), (DAY + 1, 10), (-60, 1000), (0, 5)])
    u = ins.usage_by_day(rows, 1, NOW)
    assert sum(u["users"]["ada"]["tokens"]) == 6, u["users"]
    assert u["users"]["ada"]["tokens"][-1] == 5 and u["users"]["ada"]["tokens"][0] == 1
    assert ins.window(1, NOW) == (NOW - DAY, NOW)


def test_the_api_cuts_every_historical_part_by_one_interval(server, tmp_path):  # noqa: F811
    app, _ = server
    store = app.state.store
    ada = _client(app, "ada", "correct horse battery")
    now = time.time()
    with open(store.refusals_file, "a") as fh:
        for age in (0.5 * DAY, 3 * DAY):
            fh.write(json.dumps({"t": now - age, "host": f"h{int(age)}.io", "port": 443, "app": "x"}) + "\n")
    store.server_set("agent-test:ada:claude", {"when": now - 45 * DAY, "ok": False, "steps": [{"step": "login", "ok": False, "said": "no"}]})
    one = ada.get("/api/admin/insights", params={"days": 1}).json()
    assert one["days"] == 1 and one["range"]["timezone"] == "UTC" and abs(one["range"]["end"] - one["range"]["start"] - DAY) < 1e-6
    assert [n["host"] for n in one["network"]] == [f"h{int(0.5 * DAY)}.io"], "a refusal 3 days old is not the last day's"
    assert one["usage"]["bucket"] == "hour" and "disk" not in one, "the disk is not historical: measured apart"
    assert one["failures"]["tests"] == [], "a Test that failed 45 days ago is not this period's"
    thirty = ada.get("/api/admin/insights", params={"days": 30}).json()
    assert thirty["failures"]["tests"] == [] and len(thirty["network"]) == 2
    assert len(ada.get("/api/admin/insights", params={"days": 60}).json()["failures"]["tests"]) == 1
    only = ada.get("/api/admin/insights", params={"days": 7, "part": "usage"}).json()
    assert "usage" in only and "failures" not in only and "endpoints" not in only
    disk = ada.get("/api/admin/insights/disk").json()
    assert {d["user"] for d in disk["disk"]} == {"ada", "bob"} and disk["at"] <= time.time()
    assert ada.post("/api/admin/insights/forget", json={"kind": "network", "key": "x"}, headers=H).status_code == 200
