"""D766: Admin › Insights from what the server keeps already -- the turns read per day, user, agent
and loop; each endpoint and agent as its turns found it; the hosts refused; the disk by user."""

from __future__ import annotations

import json
import types

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
