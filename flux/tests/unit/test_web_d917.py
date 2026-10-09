"""D917: a loop's journal, live state and log over ONE server-sent stream -- each message's id every
part's cursor as delivered, so a reconnect resumes each part where it was; the one-file routes keep
their bare ids."""

from __future__ import annotations

import asyncio
import json
import time

from test_web import H, _client, server  # noqa: F401 -- the fixture


def _loop(app):
    """A loop `s` of bob's with a finished start: its journal, live state and log."""
    bob = _client(app, "bob", "another long secret")
    assert bob.post("/api/apps", data={"name": "s"}, files=[("files", ("problem.yaml", b"statement: s\n"))], headers=H).status_code == 200
    store = app.state.store
    user = store.user(name="bob")
    d = store.data / "users" / "bob" / "apps" / "s"
    (d / "out" / "camp").mkdir(parents=True)
    (d / "runs").mkdir(exist_ok=True)
    db = d / "out" / "s.db"
    (d / "out" / "s.db.runs.json").write_text(json.dumps({"camp": str(d / "out" / "camp")}))
    (d / "runs" / "loop.log").write_text("one\ntwo\n")
    rid = store.add_run(user, "s", str(db), str(d / "runs" / "loop.log"), [], {})
    store.set_run(rid, ended=time.time(), rc=0)
    t = time.time()
    (d / "out" / "camp" / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in (
        {"t": t, "ev": "hello"}, {"t": t, "ev": "start", "id": 1, "parent": None, "name": "generate", "why": "", "params": {}})))
    (d / "out" / "camp" / "live.json").write_text(json.dumps({"t": t, "updates": {}, "publish": {}}))
    return user, d


class _Request:
    """A client that stays for `looks` looks, sending `last` as its Last-Event-ID."""

    def __init__(self, looks: int, last: str | None = None) -> None:
        self.headers = {"last-event-id": last} if last else {}
        self.left = looks

    async def is_disconnected(self) -> bool:
        self.left -= 1
        return self.left < 0


def _read(app, path, user, looks=2, last=None, **kw):
    fn = next(r.endpoint for r in app.routes if getattr(r, "path", "") == path)
    resp = fn(name="s", request=_Request(looks, last), user=user, owner=None, **kw)

    async def drain():
        out = []
        async for chunk in resp.body_iterator:
            out.append(chunk)
        return out

    msgs = []
    for chunk in asyncio.run(drain()):
        m = dict(ln.split(": ", 1) for ln in chunk.strip().splitlines() if ": " in ln and not ln.startswith(":"))
        if "event" in m:
            msgs.append(m)
    return msgs


def test_one_stream_carries_the_three_and_resumes_each_where_it_was(server):  # noqa: F811
    app, _ = server
    user, d = _loop(app)
    msgs = _read(app, "/api/apps/{name}/stream", user)
    kinds = [m["event"] for m in msgs]
    assert {"events", "live", "log", "ready"} <= set(kinds), kinds
    assert kinds.index("ready") > kinds.index("events"), "ready once the journal is caught up"
    last = [m["id"] for m in msgs if "id" in m][-1]
    assert last.startswith("events:") and ",log:" in last, last
    first_events = next(m for m in msgs if m["event"] == "events")
    assert first_events["id"].count(":") == 1, "a part's id names only the parts delivered up to it"
    # more of both; a reconnect with the last id gets only what came after, for each part
    with (d / "runs" / "loop.log").open("a") as fh:
        fh.write("three\n")
    with (d / "out" / "camp" / "events.jsonl").open("a") as fh:
        fh.write(json.dumps({"t": time.time(), "ev": "end", "id": 1, "name": "generate", "seconds": 1, "failed": False, "output": {}}) + "\n")
    again = _read(app, "/api/apps/{name}/stream", user, last=last)
    assert [json.loads(m["data"]) for m in again if m["event"] == "log"] == ["three\n"]
    assert [e["ev"] for m in again if m["event"] == "events" for e in json.loads(m["data"])] == ["end"]
    # only the parts asked for; the cursors from the query when no header
    only = _read(app, "/api/apps/{name}/stream", user, parts="log", log=last.split("log:")[1])
    assert [m["event"] for m in only] == ["log"] and json.loads(only[0]["data"]) == "three\n"
