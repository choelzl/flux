"""D705: a question about a loop, answered by an agent that reads it and changes nothing -- the
core (`flux_loop.consult`: a snapshot of the record, the brief, the answer), the web's questions
(a stand-in for `flux consult`), and the agent by default (the admin's, a user's own)."""

from __future__ import annotations

import sqlite3
import stat
import time

import pytest
from fastapi.testclient import TestClient

from flux_llm import ScriptedProposer
from flux_loop.consult import ANSWER, brief, consult, snapshot
from flux_web import create_app
from flux_web.store import Store

H = {"X-Flux": "1"}


def _loop(tmp_path):
    loop = tmp_path / "x"
    (loop / "out").mkdir(parents=True)
    (loop / "runs").mkdir()
    (loop / "x.problem.yaml").write_text("statement: an adder\n")
    (loop / ".flux-app.json").write_text('{"document": "x.problem.yaml", "id": "x"}')
    (loop / "runs" / "loop.log").write_text("line one\nDECISION d1\n")
    db = sqlite3.connect(loop / "out" / "x.db")
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("CREATE TABLE t (a INTEGER)")
    db.execute("INSERT INTO t VALUES (42)")
    db.commit()
    db.close()
    return loop


def test_the_model_answers_from_the_document_the_record_and_the_log(tmp_path):
    loop, out = _loop(tmp_path), tmp_path / "answer"
    out.mkdir()
    rec = snapshot(loop, out)
    assert rec and sqlite3.connect(rec).execute("SELECT a FROM t").fetchone() == (42,), "a whole copy of the record"
    text = brief("why?", loop, out, rec, inline=True)
    assert "x.problem.yaml" in text and "statement: an adder" in text and "DECISION d1" in text and "THE QUESTION:\nwhy?" in text
    agent_text = brief("why?", loop, out, rec, inline=False)
    assert str(out / ANSWER) in agent_text and "do not write in the loop's folder" in agent_text and "statement: an adder" not in agent_text
    got = consult("why?", loop, tmp_path / "a2", author="model", proposer=ScriptedProposer(["## Because\nit was smaller."]))
    assert got["ok"] and (tmp_path / "a2" / ANSWER).read_text().startswith("## Because")


FAKE = r'''#!/usr/bin/env python3
import os, sys, time
a = sys.argv[1:]
out = a[a.index("--out") + 1]
for _ in range(2400):                                 # held until the test says go: a handshake, not a clock
    if os.path.exists(os.path.join(out, "go")):
        break
    time.sleep(0.05)
open(os.path.join(out, "answer.md"), "w").write("**It** answered: " + a[1] + "\n")
open(os.path.join(out, "record.db"), "w").write("snapshot")
print("answered by", a[a.index("--author") + 1])
'''


@pytest.fixture()
def server(tmp_path, monkeypatch):
    fake = tmp_path / "flux-fake"
    fake.write_text(FAKE)
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    import flux_web.asks as asks_mod

    monkeypatch.setattr(asks_mod.shutil, "which", lambda name, path=None: str(fake) if name == "flux" else None)
    from web_agents import install

    install(monkeypatch, tmp_path)                                  # D807: offered where installed
    store = Store(tmp_path / "data")
    store.add_user("ada", "correct horse battery", "admin")
    store.add_user("bob", "another long secret")
    for agent in ("opencode", "claude", "codex"):                  # D751: bob's agents passed their test
        store.server_set(f"agent-test:bob:{agent}", {"ok": True})
    store.add_user("cy", "cy has a long secret")
    return create_app(tmp_path / "data", sandbox=False), store, tmp_path


def _c(app, n, pw):
    c = TestClient(app)
    c.post("/api/login", json={"name": n, "password": pw}, headers=H)
    return c


def test_a_question_about_a_loop_is_answered_and_kept_with_it(server, monkeypatch):
    app, store, tmp = server
    bob, cy = _c(app, "bob", "another long secret"), _c(app, "cy", "cy has a long secret")
    bob.post("/api/apps", data={"name": "x"}, files=[("files", ("x.problem.yaml", b"statement: s\n"))], headers=H)
    r = bob.post("/api/apps/x/asks", json={"question": "what is best?", "author": "opencode"}, headers=H)
    assert r.status_code == 200, r.text
    assert bob.post("/api/apps/x/asks", json={"question": "another", "author": "opencode"}, headers=H).status_code == 409
    (tmp / f"data/users/bob/apps/x/runs/asks/{r.json()['id']}/go").write_text("")
    for _ in range(1200):                               # ends when answered; long only under a loaded machine
        got = bob.get("/api/apps/x/asks").json()
        if not got[0]["running"]:
            break
        time.sleep(0.1)
    a = got[0]
    assert a["ok"] and a["answer"].startswith("**It** answered: what is best?") and a["by"] == "bob" and a["author"] == "opencode"
    assert not (tmp / f"data/users/bob/apps/x/runs/asks/{a['id']}/record.db").exists(), "the record's snapshot is not kept"
    assert bob.post("/api/apps/x/asks", json={"question": "q", "author": "nobody"}, headers=H).status_code == 400
    # a watcher reads the answers and asks nothing
    store.set_share("bob", "x", "cy", "watch")
    assert cy.get("/api/apps/x/asks", params={"owner": "bob"}).json()[0]["id"] == a["id"]
    assert cy.post("/api/apps/x/asks", params={"owner": "bob"}, json={"question": "q"}, headers=H).status_code == 403
    assert bob.delete("/api/apps/x/asks/not-a-question", headers=H).status_code == 404
    assert bob.delete("/api/apps/x/asks/..%2F..%2Fx", headers=H).status_code == 404
    assert bob.delete(f"/api/apps/x/asks/{a['id']}", headers=H).status_code == 200 and bob.get("/api/apps/x/asks").json() == []


def test_the_agent_by_default_is_the_admins_unless_a_user_sets_their_own(server):
    app, store, _tmp = server
    ada, bob = _c(app, "ada", "correct horse battery"), _c(app, "bob", "another long secret")
    assert ada.put("/api/admin/settings", json={"values": {"FLUX_DEFAULT_AGENT": "a robot"}}, headers=H).status_code == 400
    assert ada.put("/api/admin/settings", json={"values": {"FLUX_DEFAULT_AGENT": "model"}}, headers=H).status_code == 200
    assert [a["id"] for a in bob.get("/api/agents").json() if a["default"]] == ["model"]
    assert bob.put("/api/settings", json={"values": {"FLUX_DEFAULT_AGENT": "codex"}}, headers=H).status_code == 200
    assert [a["id"] for a in bob.get("/api/agents").json() if a["default"]] == ["codex"]
    assert [a["id"] for a in ada.get("/api/agents").json() if a["default"]] == ["model"]


def test_an_agents_program_is_the_admins_for_every_run_and_mounted(server, tmp_path):
    from flux_web.runs import run_env

    app, store, _tmp = server
    ada, bob = _c(app, "ada", "correct horse battery"), _c(app, "bob", "another long secret")
    tool = tmp_path / "opt" / "oc-mod" / "bin"
    tool.mkdir(parents=True)
    (tool / "opencode").write_text("#!/bin/sh\necho 9\n")
    (tool / "opencode").chmod(0o755)
    assert ada.put("/api/admin/agents/opencode", json={"bin": str(tool / "opencode")}, headers=H).status_code == 200
    assert ada.put("/api/admin/agents/claude", json={"bin": "rm -rf /"}, headers=H).status_code == 400
    assert bob.put("/api/admin/agents/opencode", json={"bin": "/tmp/mine"}, headers=H).status_code == 403, "the admin's only"
    assert bob.put("/api/settings", json={"values": {"FLUX_OPENCODE_BIN": "/tmp/mine"}}, headers=H).status_code == 400
    env = run_env(store, store.user(name="bob"))
    assert env["FLUX_OPENCODE_BIN"] == str(tool / "opencode") and str(tool) in env["PATH"].split(":"), "its folder on PATH: mounted"
