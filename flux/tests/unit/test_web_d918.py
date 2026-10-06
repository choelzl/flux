"""D918: a loop's latest start is one indexed row, a list's latest starts one query; the journal of a
short start appended to a long one opens at that start's hello, not at byte 0."""

from __future__ import annotations

import json
import time

from test_web import H, _client, server  # noqa: F401 -- the fixture
from test_web_d917 import _loop, _read


def test_the_latest_start_is_one_row_and_a_list_one_query(server):  # noqa: F811
    app, _ = server
    store = app.state.store
    bob = _client(app, "bob", "another long secret")
    user = store.user(name="bob")
    for name in ("a", "b", "c"):
        assert bob.post("/api/apps", data={"name": name}, files=[("files", ("problem.yaml", b"statement: s\n"))], headers=H).status_code == 200
    for i in range(30):
        rid = store.add_run(user, "ab"[i % 2], "/nowhere.db", "/nowhere.log", [], {"i": i})
        store.set_run(rid, ended=time.time(), rc=0)
    for name in ("a", "b", "c"):
        whole = store.runs(user, name)
        assert store.latest_run(user, name) == (whole[0] if whole else None)
    assert {k: v["id"] for k, v in store.latest_runs(user).items()} == {n: store.runs(user, n)[0]["id"] for n in ("a", "b")}
    with store._db() as db:
        plan = " ".join(r[-1] for r in db.execute("EXPLAIN QUERY PLAN SELECT * FROM runs WHERE user_id = 1 AND app = 'a' ORDER BY id DESC LIMIT 1"))
    assert "runs_latest" in plan, plan
    calls = []
    real = store.runs
    store.runs = lambda *a, **k: calls.append(a) or real(*a, **k)
    listed = bob.get("/api/apps").json()
    assert {a["name"]: a["options"] for a in listed} == {"a": {"i": 28}, "b": {"i": 29}, "c": {}}
    bob.get("/api/loops")
    assert not calls, "a list reads no start but each loop's latest"


def test_a_short_start_after_a_long_journal_opens_at_its_hello(server):  # noqa: F811
    from flux_loop.journal import start_offset

    app, _ = server
    user, d = _loop(app)
    ev = d / "out" / "camp" / "events.jsonl"
    t = time.time()
    old = "".join(json.dumps({"t": t - 100, "ev": "start", "id": 100 + i, "parent": None, "name": "old", "why": "x" * 200, "params": {}}) + "\n"
                  for i in range(2000))
    new = ev.read_text()                                    # the latest start: its hello and one task
    ev.write_text(json.dumps({"t": t - 200, "ev": "hello"}) + "\n" + old + new)
    at = start_offset(str(ev))
    assert ev.read_bytes()[at:].decode() == new, "from the latest hello"
    (d / "out" / "camp" / "marks.jsonl").write_text(json.dumps({"at": 0, "ev": "hello", "n": 0}) + "\n"
                                                    + json.dumps({"at": at, "ev": "hello", "n": 0}) + "\n")
    assert start_offset(str(ev)) == at, "the same from the marks"
    msgs = _read(app, "/api/apps/{name}/stream", user, parts="events", window=30)
    first = next(m for m in msgs if m["event"] == "events")
    assert [e["ev"] for e in json.loads(first["data"])] == ["hello", "start"]
    assert int(first["id"].split("-")[-1]) == ev.stat().st_size and int(first["id"].split(":")[1].split("-")[1]) > at, \
        "read from the hello: one slice, not the old journal's"
