"""D921: Admin's home reads the pause from the controls, never Resources' walk; Agents draws without
waiting on `--version` probes, which come apart; a transcript's sums grow with it -- one turn
appended is one turn read -- and a transcript replaced or cut is read again."""

from __future__ import annotations

import json
import os

from test_web import H, _client, server  # noqa: F401 -- the fixture

from flux_web import insights as ins
from flux_web import usage as us


def _turn(i, **kw):
    return json.dumps({"ts": 1000 + i, "kind": "agent", "agent": "claude", "seconds": 2, "tokens_in": 10, "tokens_out": 1, "cost_usd": 0.5, **kw}) + "\n"


def _fresh(path):
    us._CACHE.pop(str(path), None)
    ins._TURNS.pop(str(path), None)
    return us.usage(str(path)), list(ins._rows(str(path)))


def test_a_transcript_is_summed_as_it_grows(tmp_path, monkeypatch):
    p = tmp_path / "turns.jsonl"
    p.write_text("".join(_turn(i) for i in range(5)))
    assert us.usage(str(p))["total"]["turns"] == 5 and len(ins._rows(str(p))) == 5
    read = []
    real = us.grown
    monkeypatch.setattr(us, "grown", lambda path, at: read.append(at) or real(path, at))
    with p.open("a") as fh:
        fh.write(_turn(5, kind="turn", model="qwen", error="timed out") + _turn(6)[:20])       # one whole turn, one being written
    got, rows = us.usage(str(p)), list(ins._rows(str(p)))
    assert read and all(at > 0 for at in read), "read from where it was, not from the start"
    assert got["total"]["turns"] == 6 and got["total"]["errors"] == 1 and len(rows) == 6
    assert (got, rows) == _fresh(p), "the same sums as read whole"
    with p.open("a") as fh:
        fh.write(_turn(6)[20:])                                   # the rest of the line
    assert us.usage(str(p))["total"]["turns"] == 7 and len(ins._rows(str(p))) == 7
    # replaced (as pricing does: another file in its place), then cut: read again from the start
    tmp = tmp_path / "t.tmp"
    tmp.write_text("".join(_turn(i, cost_usd=2.0) for i in range(7)))
    os.replace(tmp, p)
    assert us.usage(str(p))["total"]["cost_usd"] == 14.0 and ins._rows(str(p))[0][8] == 2.0
    p.write_text(_turn(0))
    assert us.usage(str(p))["total"]["turns"] == 1 and len(ins._rows(str(p))) == 1


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
