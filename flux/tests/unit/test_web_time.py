"""D694: where a loop's time goes (the journal's phases by kind of work), what its turns cost
(tokens as the agents and the model reported them), the charts' measurements thinned, and a
restarted server finding a running loop again."""

from __future__ import annotations

import json
import types

from flux_web.results import thin
from flux_web.timeline import kind_of, timeline
from flux_web.usage import usage


def _journal(path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def test_the_timeline_counts_parallel_work_once_and_splits_the_starts(tmp_path):
    p = tmp_path / "events.jsonl"
    _journal(p, [
        {"t": 0, "ev": "hello", "pid": 1},
        {"t": 0, "ev": "start", "id": 1, "parent": None, "name": "propose: decompose", "why": ""},
        {"t": 0, "ev": "end", "id": 1, "name": "propose: decompose", "seconds": 0},
        {"t": 1, "ev": "hello", "pid": 2},                                   # the second start
        {"t": 1, "ev": "start", "id": 1, "parent": None, "name": "propose: decompose", "why": ""},
        {"t": 1, "ev": "end", "id": 1, "name": "propose: decompose", "seconds": 0},
        {"t": 1, "ev": "start", "id": 2, "parent": None, "name": "generation: build x#1", "why": ""},
        {"t": 1, "ev": "start", "id": 3, "parent": 2, "name": "agent: claude", "why": "x"},
        {"t": 11, "ev": "update", "id": 3, "name": "agent: claude", "fields": {"status": "done"}},
        {"t": 11, "ev": "end", "id": 3, "name": "agent: claude", "seconds": 10},
        {"t": 11, "ev": "end", "id": 2, "name": "generation: build x#1", "seconds": 10},
        {"t": 11, "ev": "start", "id": 4, "parent": None, "name": "evaluation", "why": ""},
        {"t": 11, "ev": "start", "id": 5, "parent": 4, "name": "simulation: screen", "why": ""},
        {"t": 11, "ev": "start", "id": 6, "parent": 5, "name": "tool:yosys", "why": "a"},
        {"t": 11, "ev": "start", "id": 7, "parent": 5, "name": "tool:yosys", "why": "b"},    # side by side
        {"t": 15, "ev": "end", "id": 6, "name": "tool:yosys", "seconds": 4},
        {"t": 16, "ev": "end", "id": 7, "name": "tool:yosys", "seconds": 5, "failed": True},
        {"t": 16, "ev": "end", "id": 5, "name": "simulation: screen", "seconds": 5},
        {"t": 16, "ev": "end", "id": 4, "name": "evaluation", "seconds": 5},
        {"t": 16, "ev": "start", "id": 8, "parent": None, "name": "propose: decompose", "why": ""},
        {"t": 16, "ev": "end", "id": 8, "name": "propose: decompose", "seconds": 0},
        {"t": 16, "ev": "start", "id": 9, "parent": None, "name": "test: gate", "why": ""},       # never ended
    ])
    t = timeline(str(p), now=20.0)
    assert t["start"] == 1 and len(t["starts"]) == 2
    kinds = {k["kind"]: k for k in t["kinds"]}
    assert kinds["agent"]["busy"] == 10 and kinds["agent"]["count"] == 1, "the agent under the generation is the agent's"
    scr = kinds["stage screen"]
    assert scr["count"] == 2 and scr["summed"] == 9 and scr["busy"] == 5, "two tools side by side: busy once, summed twice"
    assert scr["mean"] == 4.5 and scr["longest"] == 5 and kinds["agent"]["mean"] == 10, "D772: a call's average and the longest"
    assert t["passes"] == [1, 16]
    gate = next(b for b in t["bars"] if b["kind"] == "gate")
    assert gate["running"] and gate["t1"] == 20.0, "a phase not ended in a live start runs to now"
    assert any(b["failed"] for b in t["bars"] if b["kind"] == "stage screen")
    assert timeline(str(p), 0)["bars"][0]["kind"] == "loop", "the first start, the loop's own work"
    assert kind_of("llm: generating (model)") == "model" and kind_of("records: re-verify *") == "re-verify"


def test_usage_adds_the_agents_and_the_models_tokens(tmp_path):
    p = tmp_path / "turns.jsonl"
    p.write_text("\n".join(json.dumps(t) for t in [
        {"ts": 1, "kind": "agent", "agent": "claude", "ok": True, "seconds": 30, "tokens_in": 1000, "tokens_out": 200,
         "tokens_cached": 800, "cost_usd": 0.25},
        {"ts": 2, "kind": "agent", "agent": "opencode", "ok": False, "seconds": 10},                    # before D694
        {"ts": 3, "kind": "model", "model": "qwen", "seconds": 5, "notes": {"input_tokens": 50, "output_tokens": 7}},
        {"ts": 4, "kind": "model", "model": "qwen", "seconds": 5, "tokens_in": 90, "tokens_out": 9, "notes": {"input_tokens": 40}},
    ]) + "\nnot json\n")
    u = usage(str(p))
    t = u["total"]
    assert t["turns"] == 4 and t["seconds"] == 50 and t["errors"] == 1 and t["counted"] == 3
    assert t["tokens_in"] == 1140 and t["tokens_out"] == 216 and t["cost_usd"] == 0.25
    assert [b["who"] for b in u["by"]] == ["claude", "opencode", "qwen"] and u["by"][2]["turns"] == 2
    assert usage(str(tmp_path / "none.jsonl"))["total"]["turns"] == 0


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


def test_the_timeline_reads_the_journal_as_it_grows(tmp_path):
    """D779: a running loop's next look parses only what was added; a half-written line waits; a
    new file is read afresh; an end's output is not kept."""
    import json as _json

    from flux_web.timeline import _CACHE, starts

    p = tmp_path / "events.jsonl"
    rows = [{"t": 1, "ev": "hello"}, {"t": 2, "ev": "start", "id": 1, "parent": None, "name": "simulation: screen", "why": "x" * 900},
            {"t": 3, "ev": "end", "id": 1, "seconds": 1, "failed": False, "output": {"big": "z" * 10000}}]
    p.write_text("".join(_json.dumps(r) + "\n" for r in rows))
    first = starts(str(p))
    assert len(first) == 1 and len(first[0]) == 3 and "output" not in first[0][2] and len(first[0][1]["why"]) == 200
    read = _CACHE[str(p)][1]
    with p.open("a") as fh:
        fh.write(_json.dumps({"t": 4, "ev": "hello"}) + "\n" + '{"t": 5, "ev": "sta')
    again = starts(str(p))
    assert len(again) == 2 and len(again[1]) == 1 and _CACHE[str(p)][1] > read, "only the new bytes"
    assert first[0] is not again[0] or len(first[0]) == 3, "a returned start is not changed under its reader"
    p.unlink()
    p.write_text(_json.dumps({"t": 9, "ev": "hello"}) + "\n")
    assert len(starts(str(p))) == 1, "a new file: read afresh"


def test_the_timeline_built_as_the_journal_grows_is_the_one_read_whole(tmp_path):
    """D780: the phases of a start are added as its events come; what the page gets equals a
    reading of the whole file afresh, ended or running."""
    import json as _json

    import flux_web.timeline as tl

    p = tmp_path / "events.jsonl"
    rows = [{"t": 0, "ev": "hello"}]
    n = 0

    def grow(k):
        nonlocal n
        with p.open("a") as fh:
            for _ in range(k):
                n += 1
                fh.write(_json.dumps({"t": n, "ev": "start", "id": n, "parent": None if n % 3 else n - 1,
                                      "name": ["simulation: screen", "agent: claude", "generation: x"][n % 3], "why": ""}) + "\n")
                if n % 4:
                    fh.write(_json.dumps({"t": n + 0.5, "ev": "end", "id": n, "seconds": 0.5, "failed": n % 7 == 0}) + "\n")

    import shutil

    p.write_text("".join(_json.dumps(r) + "\n" for r in rows))
    for i, k in enumerate((10, 25, 7)):
        grow(k)
        for running in (True, False):
            built = tl.timeline(str(p), now=n + 10, running=running)          # added to what the last look built
            fresh = tmp_path / f"fresh-{i}-{running}.jsonl"                     # the same file, never looked at
            shutil.copy(p, fresh)
            assert built == tl.timeline(str(fresh), now=n + 10, running=running), (k, running)
