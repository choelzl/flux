"""D1036: an agent turn's whole conversation, a page at a time, from the file the turn wrote -- only a
`.flux-steps` file in the loop's own folder or its sandbox cache."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from flux_web import create_app
from flux_web.store import Store

H = {"X-Flux": "1"}


@pytest.fixture()
def bob(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    store = Store(tmp_path / "data")
    store.add_user("bob", "another long secret")
    store.add_user("cy", "cy has a long secret")
    app = create_app(tmp_path / "data", sandbox=False)
    c = TestClient(app)
    c.post("/api/login", json={"name": "bob", "password": "another long secret"}, headers=H)
    c.post("/api/apps", data={"name": "x"}, files=[("files", ("x.problem.yaml", b"statement: s\n"))], headers=H)
    cy = TestClient(app)
    cy.post("/api/login", json={"name": "cy", "password": "cy has a long secret"}, headers=H)
    return c, cy, tmp_path


def _write(path, n):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps({"k": "tool", "name": "Bash", "call": f"echo {i}"}) + "\n" for i in range(n)))
    return str(path)


def test_a_turns_steps_are_read_a_page_at_a_time(bob):
    c, _cy, tmp = bob
    f = _write(tmp / "cache" / "flux" / "apps" / "bob.x" / "tmp" / "agents" / ".flux-steps" / "t.jsonl", 100)
    got = c.get("/api/apps/x/steps", params={"file": f, "start": 50, "count": 30}).json()
    assert got["total"] == 100 and got["start"] == 50 and [s["call"] for s in got["steps"]] == [f"echo {i}" for i in range(50, 80)]
    big = _write(tmp / "cache" / "flux" / "apps" / "bob.x" / ".flux-steps" / "big.jsonl", 300)
    assert len(c.get("/api/apps/x/steps", params={"file": big, "count": 5000}).json()["steps"]) == 200, "a page is at most 200"
    in_loop = _write(tmp / "data" / "users" / "bob" / "apps" / "x" / ".flux-steps" / "u.jsonl", 3)
    assert c.get("/api/apps/x/steps", params={"file": in_loop}).json()["total"] == 3


def test_only_a_steps_file_of_the_loops_own_is_read(bob):
    c, cy, tmp = bob
    outside = _write(tmp / "elsewhere" / ".flux-steps" / "t.jsonl", 3)
    other = _write(tmp / "cache" / "flux" / "apps" / "cy.y" / ".flux-steps" / "t.jsonl", 3)
    plain = tmp / "cache" / "flux" / "apps" / "bob.x" / "notes.jsonl"
    plain.parent.mkdir(parents=True, exist_ok=True)
    plain.write_text("{}\n")
    for f in (outside, other, str(plain), "/etc/passwd"):
        assert c.get("/api/apps/x/steps", params={"file": f}).status_code == 404, f
    mine = _write(tmp / "cache" / "flux" / "apps" / "bob.x" / ".flux-steps" / "t.jsonl", 3)
    assert cy.get("/api/apps/x/steps", params={"file": mine, "owner": "bob"}).status_code in (403, 404), "not shared with cy"


def test_a_turn_without_a_steps_file_reads_its_kept_steps_from_the_transcript(bob):
    """D1039: a turn from before D1036 wrote no steps file; the transcript holds its kept steps --
    the agent turn that ended as the task did."""
    c, _cy, tmp = bob
    from flux_web.store import Store

    store = Store(tmp / "data")
    d = tmp / "data" / "users" / "bob" / "apps" / "x"
    trace = d / "out" / "c1"
    trace.mkdir(parents=True)
    turn = lambda ts, agent, n: json.dumps({"ts": ts, "kind": "agent", "agent": agent,  # noqa: E731
                                            "steps": [{"k": "text", "text": f"{agent} {i}"} for i in range(n)]}) + "\n"
    (trace / "turns.jsonl").write_text(json.dumps({"ts": 100, "kind": "model"}) + "\n" + turn(110, "claude", 3)
                                       + turn(205, "claude", 60) + turn(206, "codex", 2))
    db = d / "out" / "x.db"
    db.with_name("x.db.runs.json").write_text(json.dumps({"c1": str(trace)}))
    (d / "runs").mkdir(exist_ok=True)
    store.add_run(store.user(name="bob"), "x", str(db), str(d / "runs" / "loop.log"), ["flux"], {})
    got = c.get("/api/apps/x/turn-steps", params={"t0": 150, "t1": 204.5, "agent": "claude"}).json()["steps"]
    assert len(got) == 60 and got[0]["text"] == "claude 0"
    assert c.get("/api/apps/x/turn-steps", params={"t0": 300, "t1": 310}).json() == {"steps": []}
