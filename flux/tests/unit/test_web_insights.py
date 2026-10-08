"""D766: Admin › Insights from what the server keeps already -- the turns read per day, user, agent
and loop; each endpoint and agent as its turns found it; the hosts refused; the disk by user."""

from __future__ import annotations

import json
import types

import pytest

from flux_web import insights as ins

DAY = ins.DAY
NOW = 100 * DAY + 3600


def _turns(tmp_path):
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
    return [("ada", "nlu", *t) for t in ins._rows(str(p))]


def test_turns_by_day_user_agent_and_loop(tmp_path):
    u = ins.usage_by_day(_turns(tmp_path), days=7, now=NOW)
    assert len(u["days"]) == 7 and u["users"]["ada"]["turns"][-2:] == [1, 3]
    assert u["agents"]["claude"]["tokens"][-1] == 1200 and u["agents"]["qwen"]["tokens"][-2] == 330
    assert u["top"][0] == {"user": "ada", "app": "nlu", "turns": 4, "tokens": 1530.0, "cost": 0.5, "seconds": 82.0}
    assert ins.usage_by_day(_turns(tmp_path), days=7, now=NOW + 30 * DAY)["users"] == {}, "older than the days: not counted"


def test_each_endpoint_and_agent_as_its_turns_found_it(tmp_path):
    eps = {e["where"]: e for e in ins.endpoints(_turns(tmp_path), since=NOW - 2 * DAY)}
    assert set(eps) == {"claude-opus", "codex", "ai.example.org · qwen"}
    q = eps["ai.example.org · qwen"]
    assert (q["turns"], q["failed"], q["rate"], q["last_error"]) == (2, 1, 0.5, "timed out")
    assert eps["codex"]["last_error"] == "exit 1: bwrap: denied" and eps["claude-opus"]["failed"] == 0
    assert ins.endpoints(_turns(tmp_path), since=NOW) == [], "none since"


@pytest.mark.parametrize("stderr", ["the agent ran past 900s and was stopped", ""])
def test_agent_time_limits_do_not_count_as_endpoint_failures(tmp_path, stderr):
    path = tmp_path / "turns.jsonl"
    turns = [{"ts": NOW - 60, "kind": "agent", "agent": "claude", "ok": False, "rc": 124,
              "stderr": stderr, "seconds": 900, "tokens_in": 1000, "tokens_out": 200, "cost_usd": 0.5}]
    path.write_text("".join(json.dumps(t) + "\n" for t in turns))
    rows = [("ada", "nlu", *t) for t in ins._rows(str(path))]
    assert rows[0][6] is None, "a time limit is neutral, not a successful reply"
    endpoint, = ins.endpoints(rows, since=NOW - DAY)
    assert (endpoint["turns"], endpoint["failed"], endpoint["rate"]) == (1, 0, 0)
    assert endpoint["last_error"] == "" and endpoint["last_error_at"] is None
    assert endpoint["p50"] == endpoint["p95"] == 900 and endpoint["last"] == NOW - 60
    usage = ins.usage_by_day(rows, days=1, now=NOW)
    assert usage["top"][0] == {"user": "ada", "app": "nlu", "turns": 1, "tokens": 1200.0, "cost": 0.5, "seconds": 900.0}
    rate = ins.token_rate(rows, hours=1, now=NOW)
    assert sum(p["in_agent"] for p in rate) * 20 == pytest.approx(1000)
    assert sum(p["out_agent"] for p in rate) * 20 == pytest.approx(200)

    # A newer time limit must not replace or hide a real agent failure.
    with path.open("a") as fh:
        fh.write(json.dumps({"ts": NOW - 120, "kind": "agent", "agent": "claude", "ok": False,
                             "rc": 1, "stderr": "connection timed out", "seconds": 10}) + "\n")
    rows = [("ada", "nlu", *t) for t in ins._rows(str(path))]
    endpoint, = ins.endpoints(rows, since=NOW - DAY)
    assert (endpoint["turns"], endpoint["failed"], endpoint["rate"]) == (2, 1, 0.5)
    assert endpoint["last_error"] == "exit 1: connection timed out" and endpoint["last_error_at"] == NOW - 120


def test_a_model_request_timeout_still_counts_as_an_endpoint_failure(tmp_path):
    path = tmp_path / "turns.jsonl"
    path.write_text(json.dumps({"ts": NOW - 60, "kind": "turn", "model": "qwen", "server": "https://ai.example.org/v1",
                                "rc": 124, "error": "timed out", "seconds": 30}) + "\n")
    rows = [("ada", "nlu", *t) for t in ins._rows(str(path))]
    endpoint, = ins.endpoints(rows, since=NOW - DAY)
    assert (endpoint["turns"], endpoint["failed"], endpoint["rate"]) == (1, 1, 1)
    assert endpoint["last_error"] == "timed out" and endpoint["last_error_at"] == NOW - 60


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


def test_token_rate_spreads_each_turn_over_its_time():
    """D838: tokens per second, a turn's tokens spread evenly over how long it took, agents' and
    Flux's model's apart; outside the window, nothing."""
    from flux_web.insights import token_rate

    now = 10_000.0
    rows = [("bob", "x", now, "agent", "claude", "", True, 600.0, 60_000, 6_000, 0.0, ""),   # 10 minutes, the last ones
            ("bob", "x", now - 30, "model", "qwen", "", True, 30.0, 3_000, 300, 0.0, ""),
            ("bob", "x", now - 7200, "model", "qwen", "", True, 10.0, 999, 999, 0.0, "")]            # before the hour
    got = token_rate(rows, hours=1, points=6, now=now)                # 10-minute buckets
    assert len(got) == 6 and got[-1]["t"] == now
    last = got[-1]
    assert abs(last["in_agent"] - 100.0) < 1e-6 and abs(last["out_agent"] - 10.0) < 1e-6, last
    assert abs(last["in_model"] - 3_000 / 600) < 1e-6 and abs(last["out_model"] - 0.5) < 1e-6, last
    assert all(b["in_agent"] == b["in_model"] == 0 for b in got[:-1])
    assert sum(b["in_agent"] for b in got) * 600 == 60_000, "every token counted once"


def test_token_rate_preserves_subsecond_turns_at_a_bucket_boundary():
    # The model finishes 100 ms after a boundary, so its 50 ms call belongs wholly
    # to the new bucket. Stretching it to a second leaks tokens into the old bucket.
    rows = [("bob", "x", 3600.1, "model", "qwen", "", True, 0.05, 100, 10, 0.0, "")]
    got = ins.token_rate(rows, hours=1, points=6, now=4200)
    assert got[-1]["start_t"] == 3600 and got[-1]["t"] == 4200
    assert got[-1]["in_model"] == pytest.approx(100 / 600)
    assert got[-1]["out_model"] == pytest.approx(10 / 600)
    assert all(p["in_model"] == p["out_model"] == 0 for p in got[:-1])


@pytest.mark.parametrize("points", [6, 60, 180])
def test_token_rate_conserves_overlapping_turns_and_clips_the_window(points):
    row = lambda ts, kind, seconds, tin, tout: ("bob", "x", ts, kind, "", "", True, seconds, tin, tout, 0.0, "")  # noqa: E731
    rows = [row(4200, "agent", 1200, 12000, 1200), row(4200, "model", 600, 6000, 600),
            row(1800, "agent", 2400, 2400, 240),  # only the last 1200s lie in the window
            row(4201, "agent", 100, 999999, 999999)]  # future transcript: not counted
    got = ins.token_rate(rows, hours=1, points=points, now=4200)
    size = 3600 / points
    assert sum(p["in_agent"] for p in got) * size == pytest.approx(13200)
    assert sum(p["out_agent"] for p in got) * size == pytest.approx(1320)
    assert sum(p["in_model"] for p in got) * size == pytest.approx(6000)
    assert sum(p["out_model"] for p in got) * size == pytest.approx(600)
    assert got[-1]["in_agent"] == pytest.approx(10)
    assert got[-1]["in_model"] == pytest.approx(10)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf")])
def test_token_rate_skips_nonfinite_transcript_values(bad):
    base = ("bob", "x", 4200, "agent", "", "", True, 10, 100, 10, 0.0, "")
    for column in (2, 7, 8, 9):
        row = list(base)
        row[column] = bad
        got = ins.token_rate([tuple(row), base], hours=1, now=4200)
        assert sum(p["in_agent"] for p in got) * 20 == pytest.approx(100)
        assert sum(p["out_agent"] for p in got) * 20 == pytest.approx(10)


def test_token_rate_accepts_epoch_zero_and_untimed_legacy_turns():
    rows = [("bob", "x", 0, "model", "qwen", "", True, 0, 100, 10, 0.0, "")]
    got = ins.token_rate(rows, hours=1, now=0)
    assert got[-1]["t"] == 0
    assert sum(p["in_model"] for p in got) * 20 == pytest.approx(100)
