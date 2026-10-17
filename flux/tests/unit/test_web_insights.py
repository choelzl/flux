"""D766: Admin › Insights from what the server keeps already -- the turns read per day, user, agent
and loop; each endpoint and agent as its turns found it; the hosts refused; the disk by user."""

from __future__ import annotations

import json
import re
import types

import pytest

from flux_web import insights as ins
from flux_web.usage_log import UsageLog

DAY = ins.DAY
NOW = 100 * DAY + 3600


def _write(tmp_path):
    rows = [
        {"ts": NOW - 60, "kind": "agent", "agent": "claude", "about": "claude-opus, 3 steps", "ok": True, "seconds": 40,
         "tokens_in": 1000, "tokens_out": 200, "cost_usd": 0.5},
        {"ts": NOW - 120, "kind": "agent", "agent": "codex", "ok": False, "rc": 1, "stderr": "\x1b[91m[1mbwrap:[0m   denied\n", "seconds": 2},
        {"ts": NOW - DAY - 5, "kind": "turn", "model": "qwen", "server": "https://ai.example.org/v1", "seconds": 10,
         "notes": {"input_tokens": 300, "output_tokens": 30}},
        {"ts": NOW - 30, "kind": "turn", "model": "qwen", "server": "https://ai.example.org/v1", "seconds": 30, "error": "timed out"},
    ]
    p = tmp_path / "turns.jsonl"
    p.write_text("".join(json.dumps(r) + "\n" for r in rows) + "not json\n")
    return p


def _log(tmp_path, *paths):
    """D1069: the transcripts read into a usage log of their own, at their own time."""
    log = UsageLog(tmp_path / "usage.db", clock=lambda: NOW)
    log.ingest([("ada", "nlu", str(p)) for p in paths])
    return log


def test_turns_by_day_user_agent_and_loop(tmp_path):
    log = _log(tmp_path, _write(tmp_path))
    u = log.usage_by_day(7, NOW)
    assert len(u["days"]) == 7 and u["users"]["ada"]["turns"][-2:] == [1, 3]
    assert u["agents"]["claude"]["tokens"][-1] == 1200 and u["agents"]["qwen"]["tokens"][-2] == 330
    assert u["top"][0] == {"user": "ada", "app": "nlu", "turns": 4, "tokens": 1530.0, "cost": 0.5, "seconds": 82.0}
    assert log.usage_by_day(7, NOW + 30 * DAY)["users"] == {}, "older than the days: not counted"


def test_each_endpoint_and_agent_as_its_turns_found_it(tmp_path):
    log = _log(tmp_path, _write(tmp_path))
    eps = {e["where"]: e for e in log.endpoints(NOW - 2 * DAY, NOW)}
    assert set(eps) == {"claude-opus", "codex", "ai.example.org · qwen"}
    q = eps["ai.example.org · qwen"]
    assert (q["turns"], q["failed"], q["rate"], q["last_error"]) == (2, 1, 0.5, "timed out")
    assert eps["codex"]["last_error"] == "exit 1: bwrap: denied" and eps["claude-opus"]["failed"] == 0
    assert log.endpoints(NOW, NOW + 1) == [], "none since"


@pytest.mark.parametrize("stderr", ["the agent ran past 900s and was stopped", ""])
def test_agent_time_limits_do_not_count_as_endpoint_failures(tmp_path, stderr):
    path = tmp_path / "turns.jsonl"
    turns = [{"ts": NOW - 60, "kind": "agent", "agent": "claude", "ok": False, "rc": 124,
              "stderr": stderr, "seconds": 900, "tokens_in": 1000, "tokens_out": 200, "cost_usd": 0.5}]
    path.write_text("".join(json.dumps(t) + "\n" for t in turns))
    assert ins.turn_of(turns[0])[4] is None, "a time limit is neutral, not a successful reply"
    log = _log(tmp_path, path)
    endpoint, = log.endpoints(NOW - DAY, NOW)
    assert (endpoint["turns"], endpoint["failed"], endpoint["rate"]) == (1, 0, 0)
    assert endpoint["last_error"] == "" and endpoint["last_error_at"] is None
    assert endpoint["p50"] == pytest.approx(900, rel=0.2) and endpoint["p95"] == endpoint["p50"] and endpoint["last"] == NOW - 60
    usage = log.usage_by_day(1, NOW)
    assert usage["top"][0] == {"user": "ada", "app": "nlu", "turns": 1, "tokens": 1200.0, "cost": 0.5, "seconds": 900.0}
    rate = log.token_rate(1, NOW)
    assert sum(p["in_agent"] for p in rate) * 20 == pytest.approx(1000)
    assert sum(p["out_agent"] for p in rate) * 20 == pytest.approx(200)

    # A newer time limit must not replace or hide a real agent failure.
    with path.open("a") as fh:
        fh.write(json.dumps({"ts": NOW - 120, "kind": "agent", "agent": "claude", "ok": False,
                             "rc": 1, "stderr": "connection timed out", "seconds": 10}) + "\n")
    log.ingest([("ada", "nlu", str(path))])
    endpoint, = log.endpoints(NOW - DAY, NOW)
    assert (endpoint["turns"], endpoint["failed"], endpoint["rate"]) == (2, 1, 0.5)
    assert endpoint["last_error"] == "exit 1: connection timed out" and endpoint["last_error_at"] == NOW - 120


def test_a_model_request_timeout_still_counts_as_an_endpoint_failure(tmp_path):
    path = tmp_path / "turns.jsonl"
    path.write_text(json.dumps({"ts": NOW - 60, "kind": "turn", "model": "qwen", "server": "https://ai.example.org/v1",
                                "rc": 124, "error": "timed out", "seconds": 30}) + "\n")
    endpoint, = _log(tmp_path, path).endpoints(NOW - DAY, NOW)
    assert (endpoint["turns"], endpoint["failed"], endpoint["rate"]) == (1, 1, 1)
    assert endpoint["last_error"] == "timed out" and endpoint["last_error_at"] == NOW - 60


def test_the_usage_log_reads_each_turn_once_and_sums_old_minutes_into_hours(tmp_path):
    """D1069: a turn appended is read once; a transcript thinned (replaced) is not counted again; minutes
    older than two days are summed into hours, the totals the same."""
    path = _write(tmp_path)
    log = _log(tmp_path, path)
    log.ingest([("ada", "nlu", str(path))])                           # nothing new: nothing counted
    assert log.usage_by_day(7, NOW)["top"][0]["turns"] == 4
    thinned = tmp_path / "thinned.jsonl"
    thinned.write_text(path.read_text())
    import os
    os.replace(thinned, path)                                         # a maintenance pass rewrote it
    log.ingest([("ada", "nlu", str(path))])
    assert log.usage_by_day(7, NOW)["top"][0]["turns"] == 4, "a rewritten transcript is not counted twice"
    later = UsageLog(tmp_path / "usage.db", clock=lambda: NOW + 5 * DAY)
    later.compact()
    import sqlite3
    with sqlite3.connect(tmp_path / "usage.db") as c:
        assert {s for (s,) in c.execute("SELECT DISTINCT span FROM usage_turns")} == {3600}
    assert later.usage_by_day(7, NOW)["top"][0] == {"user": "ada", "app": "nlu", "turns": 4, "tokens": 1530.0, "cost": 0.5, "seconds": 82.0}


def test_a_transcript_thinned_keeps_what_usage_counts(tmp_path):
    """D1069: a turn older than the days kept loses its prompt, reply and tool calls, not its usage."""
    from flux_web.maintenance import _thin_transcript

    path = tmp_path / "turns.jsonl"
    old = {"ts": NOW - 30 * DAY, "kind": "agent", "agent": "claude", "ok": True, "seconds": 40, "tokens_in": 1000,
           "tokens_out": 200, "cost_usd": 0.5, "prompt": "x" * 5000, "reply": "y" * 5000, "steps": ["z" * 3000]}
    new = {**old, "ts": NOW - 60}
    path.write_text(json.dumps(old) + "\n" + json.dumps(new) + "\n")
    assert _thin_transcript(path, NOW - 14 * DAY) > 10000
    a, b = [json.loads(x) for x in path.read_text().splitlines()]
    assert a["thinned"] and "prompt" not in a and ins.turn_of(a)[6:9] == (1000.0, 200.0, 0.5)
    assert b == new, "a newer turn stays whole"
    assert _thin_transcript(path, NOW - 14 * DAY) == 0, "thinned once"


def test_the_hosts_refused_most_first_with_their_loops(tmp_path):
    p = tmp_path / "refused.jsonl"
    rows = [{"t": NOW, "host": "pypi.org", "port": 443, "app": "nlu"}] * 3 + [{"t": NOW, "host": "x.io", "port": 80, "app": "a"},
                                                                             {"t": 1, "host": "old.io", "port": 443, "app": "a"}]
    p.write_text("".join(json.dumps(r) + "\n" for r in rows))
    got = ins.network(str(p), since=NOW - DAY)
    assert [(n["host"], n["count"], n["loops"]) for n in got] == [("pypi.org", 3, [("nlu", 3)]), ("x.io", 1, [("a", 1)])]
    assert ins.network(str(tmp_path / "none.jsonl"), 0) == []


def test_the_disk_by_user_largest_first(tmp_path):
    (tmp_path / "users" / "ada" / "home").mkdir(parents=True)
    (tmp_path / "users" / "ada" / "home" / "f").write_bytes(b"x" * 5000)
    store = types.SimpleNamespace(data=tmp_path, users=lambda: [types.SimpleNamespace(name="ada"), types.SimpleNamespace(name="bob")])
    loops = [{"user": "bob", "app": "big", "total": 9000}, {"user": "bob", "app": "small", "total": 100},
             {"user": "ada", "app": "nlu", "total": 10}]
    got = ins.disk(store, loops)
    assert [d["user"] for d in got] == ["bob", "ada"]
    assert got[0]["largest"] == {"app": "big", "size": 9000} and got[0]["count"] == 2 and got[0]["total"] == 9100
    assert got[1]["home"] >= 5000 and got[1]["total"] == got[1]["home"] + 10


def _rate(tmp_path, turns, hours, points, now):
    """D1069: the turns in a transcript, read into a usage log (per minute), its token rate."""
    p = tmp_path / "turns.jsonl"
    p.write_text("".join(json.dumps({"ts": ts, "kind": "agent" if kind == "agent" else "turn", "model": "qwen", "seconds": secs,
                                     "tokens_in": tin, "tokens_out": tout}) + "\n" for ts, kind, secs, tin, tout in turns))
    log = UsageLog(tmp_path / "usage.db", clock=lambda: now)
    log.ingest([("bob", "x", str(p))])
    return log.token_rate(hours, now, points)


def test_token_rate_spreads_each_turn_over_its_time(tmp_path):
    """D838: tokens per second, a turn's tokens spread evenly over how long it took, agents' and
    Flux's model's apart; outside the window, nothing. D1069: kept per minute."""
    now = 9960.0                                                     # on a minute
    got = _rate(tmp_path, [(now, "agent", 600.0, 60_000, 6_000),    # 10 minutes, the last ones
                           (now - 30, "model", 30.0, 3_000, 300),
                           (now - 7200, "model", 10.0, 999, 999)], 1, 6, now)   # before the hour
    assert len(got) == 6 and got[-1]["t"] == now
    last = got[-1]
    assert last["in_agent"] == pytest.approx(100.0) and last["out_agent"] == pytest.approx(10.0), last
    assert last["in_model"] == pytest.approx(3_000 / 600) and last["out_model"] == pytest.approx(0.5), last
    assert all(b["in_agent"] == b["in_model"] == 0 for b in got[:-1])
    assert sum(b["in_agent"] for b in got) * 600 == pytest.approx(60_000), "every token counted once"


def test_token_rate_puts_a_short_turn_in_its_own_minute(tmp_path):
    # A 50 ms call 100 ms after a bucket's start is the new bucket's, none of it the old one's
    got = _rate(tmp_path, [(3600.1, "model", 0.05, 100, 10)], 1, 6, 4200)
    assert got[-1]["start_t"] == 3600 and got[-1]["t"] == 4200
    assert got[-1]["in_model"] == pytest.approx(100 / 600) and got[-1]["out_model"] == pytest.approx(10 / 600)
    assert all(p["in_model"] == p["out_model"] == 0 for p in got[:-1])


@pytest.mark.parametrize("points", [6, 60, 180])
def test_token_rate_conserves_overlapping_turns_and_clips_the_window(tmp_path, points):
    got = _rate(tmp_path, [(4200, "agent", 1200, 12000, 1200), (4200, "model", 600, 6000, 600),
                           (1800, "agent", 2400, 2400, 240),          # only the last 1200s lie in the window
                           (4201, "agent", 100, 999999, 999999)], 1, points, 4200)   # a future transcript: not counted
    size = 3600 / points
    assert sum(p["in_agent"] for p in got) * size == pytest.approx(13200)
    assert sum(p["out_agent"] for p in got) * size == pytest.approx(1320)
    assert sum(p["in_model"] for p in got) * size == pytest.approx(6000)
    assert sum(p["out_model"] for p in got) * size == pytest.approx(600)
    assert got[-1]["in_agent"] == pytest.approx(10) and got[-1]["in_model"] == pytest.approx(10)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf")])
def test_token_rate_skips_nonfinite_transcript_values(tmp_path, bad):
    base = (4200, "agent", 10, 100, 10)
    for column in (0, 2, 3, 4):
        row = list(base)
        row[column] = bad
        sub = tmp_path / str(column)
        sub.mkdir()
        got = _rate(sub, [tuple(row), base], 1, 180, 4200)
        assert sum(p["in_agent"] for p in got) * 20 == pytest.approx(100)
        assert sum(p["out_agent"] for p in got) * 20 == pytest.approx(10)


def test_token_rate_accepts_epoch_zero_and_untimed_legacy_turns(tmp_path):
    got = _rate(tmp_path, [(0, "model", 0, 100, 10)], 1, 180, 0)
    assert got[-1]["t"] == 0
    assert sum(p["in_model"] for p in got) * 20 == pytest.approx(100)


def test_a_failure_says_its_paths_short():
    """Review of Oct 17: Insights › Failures said "/nix/store/8z15…-python3-3.13.13-env/bin/python3: can't open file
    '/…/users/bob/apps/broken_gen/nope.py'" over three lines -- a Nix program by its name, a loop's file by its path there."""
    from flux_web.runs import short_paths

    loop = "/data/users/bob/apps/broken_gen"
    said = ("algorithm=a: the generator exited 2: /nix/store/8z15s70cdnpl6g8imgg9hikgq3845hz0-python3-3.13.13-env/bin/python3: "
            f"can't open file '{loop}/scripts/nope.py': [Errno 2] No such file or directory")
    assert short_paths(said, (loop, "/cache/bob-broken_gen")) == (
        "algorithm=a: the generator exited 2: python3: can't open file 'scripts/nope.py': [Errno 2] No such file or directory")
    assert short_paths("/usr/bin/false exited 1", (loop,)) == "/usr/bin/false exited 1", "a path elsewhere stays whole"


def test_usage_days_are_said_as_dates(tmp_path):
    """Review of Oct 17: the columns said "10-11 – 10-17", a date read as neither; now "Oct 11"."""
    log = UsageLog(tmp_path / "u.db", clock=lambda: NOW)
    days = log.usage_by_day(7, NOW)["days"]
    assert len(days) == 7 and all(re.fullmatch(r"[A-Z][a-z]{2} \d{1,2}", d) for d in days), days
    assert all(re.fullmatch(r"\d\d:\d\d", h) for h in log.usage_by_day(1, NOW)["days"])
