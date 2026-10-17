"""D694: what a loop's turns cost (tokens as the agents and the model reported them), the charts'
measurements thinned, and a restarted server finding a running loop again. Where a start's time went is
Live's Timeline, from the journal's tags (the server's own timeline, flux_web/timeline.py, retired: D1240)."""

from __future__ import annotations

import json
import types


from flux_web.results import thin
from flux_web.usage_log import UsageLog


def _journal(path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def test_usage_adds_the_agents_and_the_models_tokens(tmp_path):
    p = tmp_path / "turns.jsonl"
    p.write_text("\n".join(json.dumps(t) for t in [
        {"ts": 1, "kind": "agent", "agent": "claude", "ok": True, "seconds": 30, "tokens_in": 1000, "tokens_out": 200,
         "tokens_cached": 800, "cost_usd": 0.25},
        {"ts": 2, "kind": "agent", "agent": "opencode", "ok": False, "seconds": 10},                    # before D694
        {"ts": 3, "kind": "model", "model": "qwen", "seconds": 5, "notes": {"input_tokens": 50, "output_tokens": 7}},
        {"ts": 4, "kind": "model", "model": "qwen", "seconds": 5, "tokens_in": 90, "tokens_out": 9, "notes": {"input_tokens": 40}},
    ]) + "\nnot json\n")
    log = UsageLog(tmp_path / "usage.db")             # D1072: a loop's usage, summed as its transcript grows
    log.ingest([("u", "a", str(p))])
    u = log.loop(str(p))
    t = u["total"]
    assert t["turns"] == 4 and t["seconds"] == 50 and t["errors"] == 1 and t["counted"] == 3
    assert t["tokens_in"] == 1140 and t["tokens_out"] == 216 and t["cost_usd"] == 0.25
    assert [b["who"] for b in u["by"]] == ["claude", "opencode", "qwen"] and u["by"][2]["turns"] == 2
    assert log.loop(str(tmp_path / "none.jsonl"))["total"]["turns"] == 0
    with p.open("a") as fh:                            # a turn appended adds that turn
        fh.write(json.dumps({"ts": 5, "kind": "model", "model": "qwen", "seconds": 1, "tokens_in": 1}) + "\n")
    log.ingest([("u", "a", str(p))])
    assert log.loop(str(p))["total"]["turns"] == 5 and log.loop(str(p))["total"]["tokens_in"] == 1141


def test_an_agents_own_output_says_what_its_turn_cost():
    from flux_loop.agent import usage as agent_usage

    claude = "\n".join(json.dumps(e) for e in [
        {"type": "system", "subtype": "init", "session_id": "s"},
        {"type": "result", "result": "done", "session_id": "s", "total_cost_usd": 0.12,
         "usage": {"input_tokens": 10, "cache_read_input_tokens": 900, "cache_creation_input_tokens": 90, "output_tokens": 40}}])
    assert agent_usage("claude", claude) == {"tokens_in": 1000, "tokens_out": 40, "tokens_cached": 900, "cost_usd": 0.12}
    opencode = "\n".join(json.dumps(e) for e in [
        {"type": "step_finish", "part": {"tokens": {"input": 100, "output": 20, "reasoning": 5, "cache": {"read": 50, "write": 0}}, "cost": 0}},
        {"type": "text", "part": {"text": "hi"}},
        {"type": "step_finish", "part": {"tokens": {"input": 30, "output": 10, "reasoning": 0, "cache": {"read": 0, "write": 0}}, "cost": 0}}])
    assert agent_usage("opencode", opencode) == {"tokens_in": 180, "tokens_out": 35, "tokens_cached": 50}, "a zero cost is not a price"
    assert agent_usage("text", "whatever") == {}


def test_the_charts_keep_every_new_best_when_a_record_is_long():
    rows = [types.SimpleNamespace(when=i, stage="s", name=f"d{i}", part="", whole=True,
                                  metrics={"t": 1000 - i if i % 1000 == 0 else 5000 + i}) for i in range(10000)]
    got = thin(rows, [("t", "minimize")], cap=500)
    assert len(got) == 500 and [r["when"] for r in got] == sorted(r["when"] for r in got)
    kept = {r["when"] for r in got}
    assert {0, 1000, 2000, 9000} <= kept, "each new best is drawn"
    assert len(thin(rows[:100], [("t", "minimize")], cap=500)) == 100


def test_a_restarted_server_finds_a_running_loop_and_stops_it(tmp_path):
    import subprocess
    import sys

    from fastapi.testclient import TestClient

    from flux_web import create_app
    from flux_web.runs import loop_files
    from flux_web.store import Store

    store = Store(tmp_path / "data")
    store.add_user("bob", "another long secret")
    app = create_app(tmp_path / "data", sandbox=False)
    c = TestClient(app)
    c.post("/api/login", json={"name": "bob", "password": "another long secret"}, headers={"X-Flux": "1"})
    files = [("files", ("x.problem.yaml", b"statement: s\n"))]
    assert c.post("/api/apps", data={"name": "x"}, files=files, headers={"X-Flux": "1"}).status_code == 200
    d = tmp_path / "data" / "users" / "bob" / "apps" / "x"
    lf = loop_files(d)
    lf["log"].parent.mkdir(exist_ok=True)
    lf["log"].write_text("")
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True)
    try:
        rid = app.state.store.add_run(app.state.store.user(name="bob"), "x", str(d / "out" / "x.db"), str(lf["log"]), ["x"], {})
        app.state.store.set_run(rid, pid=proc.pid)
        again = create_app(tmp_path / "data", sandbox=False)             # the server restarted: its session store is the same
        c2 = TestClient(again)
        c2.cookies = c.cookies
        assert c2.get("/api/apps/x/state").json()["running"] is True
        assert c2.post("/api/apps/x/stop", json={"now": True}, headers={"X-Flux": "1"}).json()["ok"] == "stopping now"
        proc.wait(timeout=10)
        assert c2.get("/api/apps/x/state").json()["running"] is False
    finally:
        proc.kill()


