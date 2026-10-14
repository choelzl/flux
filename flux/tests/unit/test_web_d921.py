"""D921: Admin's home reads the pause from the controls, never Resources' walk; Agents draws without
waiting on `--version` probes, which come apart; a transcript's sums grow with it -- one turn
appended is one turn read -- and a transcript replaced or cut is read again."""

from __future__ import annotations

import json
import os

from test_web import H, _client, server  # noqa: F401 -- the fixture

from flux_web import usage as us


def _turn(i, **kw):
    return json.dumps({"ts": 1000 + i, "kind": "agent", "agent": "claude", "seconds": 2, "tokens_in": 10, "tokens_out": 1, "cost_usd": 0.5, **kw}) + "\n"


def _fresh(path, db):
    from flux_web.usage_log import UsageLog

    log = UsageLog(db, clock=lambda: 2000)
    log.ingest([("a", "b", str(path))])
    return log.loop(str(path))


def test_a_transcript_is_summed_as_it_grows(tmp_path, monkeypatch):
    from flux_web.usage_log import UsageLog

    p = tmp_path / "turns.jsonl"
    p.write_text("".join(_turn(i) for i in range(5)))
    log = UsageLog(tmp_path / "usage.db", clock=lambda: 2000)
    counted = lambda: log.usage_by_day(1, 2000)["top"][0]["turns"]   # noqa: E731 -- D1069: the usage log's sums
    loop = lambda: log.loop(str(p))["total"]                         # noqa: E731 -- D1072: the loop's own sums
    assert log.ingest([("a", "b", str(p))]) == 5 and counted() == 5 and loop()["turns"] == 5
    read = []
    real = us.Lines
    monkeypatch.setattr(us, "Lines", lambda path, at: read.append(at) or real(path, at))
    with p.open("a") as fh:
        fh.write(_turn(5, kind="turn", model="qwen", error="timed out") + _turn(6)[:20])       # one whole turn, one being written
    assert log.ingest([("a", "b", str(p))]) == 1 and counted() == 6
    assert read and all(at > 0 for at in read), "read from where it was, not from the start"
    got = log.loop(str(p))
    assert got["total"]["turns"] == 6 and got["total"]["errors"] == 1
    monkeypatch.setattr(us, "Lines", real)
    assert got == _fresh(p, tmp_path / "whole.db"), "the same sums as read whole"
    with p.open("a") as fh:
        fh.write(_turn(6)[20:])                                   # the rest of the line
    assert log.ingest([("a", "b", str(p))]) == 1 and counted() == 7 and loop()["turns"] == 7
    # replaced (as pricing does: another file in its place), then cut: counted again from the start, not twice
    tmp = tmp_path / "t.tmp"
    tmp.write_text("".join(_turn(i, cost_usd=2.0) for i in range(7)))
    os.replace(tmp, p)
    log.ingest([("a", "b", str(p))])
    assert loop()["cost_usd"] == 14.0 and log.usage_by_day(1, 2000)["top"][0]["cost"] == 14.0 and counted() == 7
    p.write_text(_turn(0))
    log.ingest([("a", "b", str(p))])
    assert loop()["turns"] == 1 and counted() == 1


def test_admin_home_and_agents_draw_without_the_slow_probes(server, monkeypatch):  # noqa: F811
    app, _ = server
    ada = _client(app, "ada", "correct horse battery")
    assert ada.put("/api/admin/paused", json={"reason": "upgrade"}, headers=H).status_code == 200
    from flux_web import admin as adm

    monkeypatch.setattr(adm, "containers", lambda: (_ for _ in ()).throw(AssertionError("no container asked")))
    monkeypatch.setattr(adm, "dir_size", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no disk walked")))
    got = ada.get("/api/admin/controls").json()
    assert got["paused"] == "upgrade" and "bob" in got["limits"] and got["max_running"] >= 1
    from flux_web import agents as ag

    monkeypatch.setattr(ag, "_VERSIONS", {})
    monkeypatch.setattr(ag.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no --version run")))
    fast = ada.get("/api/admin/agents", params={"probe": "false"})
    assert fast.status_code == 200 and all(a["version"] is None for a in fast.json()["agents"] if a["found"])
    ag._VERSIONS.update({(a["found"], os.stat(a["found"]).st_mtime): "9.9" for a in fast.json()["agents"] if a["found"]})
    assert all(a["version"] == "9.9" for a in ada.get("/api/admin/agents", params={"probe": "false"}).json()["agents"] if a["found"]), \
        "a version asked already is said at once"


def test_containers_are_asked_at_most_every_few_seconds(monkeypatch):
    from flux_web import admin as adm

    asked = []
    monkeypatch.setattr(adm, "containers", lambda: asked.append(1) or {"engine": "x", "containers": [], "error": None})
    adm._SEEN[1] = None
    a, t1 = adm.containers_seen()
    b, t2 = adm.containers_seen()
    assert len(asked) == 1 and t1 == t2 and a is b
    adm._SEEN[0] -= 60
    adm.containers_seen()
    assert len(asked) == 2


def test_an_agents_reported_tokens_count_as_they_come_and_once(tmp_path):
    """D1085: the tokens an agent reports on its journal row are counted in the minutes they come (read from
    where the journal was when first seen); its turn's transcript line, `streamed`, is not spread again."""
    from flux_web.usage_log import UsageLog

    j, p = tmp_path / "events.jsonl", tmp_path / "turns.jsonl"
    j.write_text(json.dumps({"t": 100, "ev": "start", "id": 1, "name": "agent: claude"}) + "\n"
                 + json.dumps({"t": 110, "ev": "update", "id": 1, "fields": {"tokens (reported)": "5,000 in, 10 out"}}) + "\n")
    log = UsageLog(tmp_path / "usage.db", clock=lambda: 4000)
    assert log.live([str(j)]) == 0, "first seen: from where it is -- its past is the transcripts'"
    with j.open("a") as fh:
        fh.write(json.dumps({"t": 3000, "ev": "start", "id": 2, "name": "agent: claude"}) + "\n")
        fh.write(json.dumps({"t": 3010, "ev": "update", "id": 2, "fields": {"tokens (reported)": "1,200 in, 30 out"}}) + "\n")
        fh.write(json.dumps({"t": 3070, "ev": "update", "id": 2, "fields": {"tokens (reported)": "2,000 in, 90 out"}}) + "\n")
        fh.write(json.dumps({"t": 3100, "ev": "update", "id": 1, "fields": {"tokens (reported)": "6,000 in, 20 out"}}) + "\n")
        fh.write(json.dumps({"t": 3125, "ev": "end", "id": 2, "output": {"tokens (reported)": "2,500 in, 100 out"}}) + "\n")
    assert log.live([str(j)]) == 3, "row 1 began before the first look: from its next report on"
    with log._con() as c:
        got = dict(((t, (tin, tout)) for t, tin, tout in c.execute("SELECT t, tin, tout FROM usage_rate ORDER BY t")))
    assert got == {3000: (1200.0, 30.0), 3060: (800.0, 60.0), 3120: (500.0, 10.0)}, got   # the minutes they came in: 2,500 / 100
    p.write_text(json.dumps({"ts": 3125, "kind": "agent", "agent": "claude", "seconds": 125, "tokens_in": 2500,
                             "tokens_out": 100, "streamed": True}) + "\n")
    log.ingest([("u", "a", str(p))])
    with log._con() as c:
        assert c.execute("SELECT SUM(tin) FROM usage_rate").fetchone()[0] == 2500.0, "the transcript's line not spread again"
        assert c.execute("SELECT SUM(tin) FROM usage_turns").fetchone()[0] == 2500.0, "but its turn counted"
