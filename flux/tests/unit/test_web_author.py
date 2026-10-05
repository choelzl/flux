"""D704: a loop's problem written, or revised, by an agent from the web -- `flux ask --no-run` in
the loop's folder; here a stand-in for it writes the document, so the job, its state, the
loop's document name kept on a revision, the attachments and the guards are what is tested."""

from __future__ import annotations

import os
import stat
import time

import pytest
from fastapi.testclient import TestClient

from flux_web import create_app
from flux_web.runs import sandbox_env
from flux_web.store import Store

H = {"X-Flux": "1"}
FAKE = r'''#!/usr/bin/env python3
import os, sys, time
args = sys.argv[1:]
prompt, d = args[1], args[args.index("--dir") + 1]
files = [args[i + 1] for i, a in enumerate(args) if a == "--file"]
time.sleep(float(os.environ.get("FAKE_SLEEP", "0")))
print("author:", args[args.index("--author") + 1], "files:", ",".join(os.path.basename(f) for f in files))
text = "statement: from the agent\n" if not prompt.startswith("REVISE") else "statement: revised by the agent\n"
open(os.path.join(d, "problem.yaml"), "w").write(text)
'''


@pytest.fixture()
def server(tmp_path, monkeypatch):
    fake = tmp_path / "flux-fake"
    fake.write_text(FAKE)
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    from web_agents import install

    install(monkeypatch, tmp_path)                                  # D807: offered where installed
    import flux_web.authoring as au

    monkeypatch.setattr(au.shutil, "which", lambda name, path=None: str(fake) if name == "flux" else None)
    store = Store(tmp_path / "data")
    store.add_user("bob", "another long secret")
    for agent in ("opencode", "claude", "codex"):                  # D751: bob's agents passed their test
        store.server_set(f"agent-test:bob:{agent}", {"ok": True})
    app = create_app(tmp_path / "data", sandbox=False)
    c = TestClient(app)
    c.post("/api/login", json={"name": "bob", "password": "another long secret"}, headers=H)
    return app, c, tmp_path


def _wait(c, name):
    for _ in range(100):
        st = c.get(f"/api/apps/{name}/author").json()
        if not st.get("running"):
            return st
        time.sleep(0.1)
    raise AssertionError("the agent never ended")


def test_an_agent_writes_a_new_loops_problem_from_a_description_and_files(server):
    _app, c, tmp = server
    agents = c.get("/api/agents").json()
    assert [a["id"] for a in agents] == ["opencode", "claude", "codex", "model"] and all("available" in a for a in agents)
    r = c.post("/api/apps/new-by-agent", data={"name": "made", "prompt": "an 8-bit adder", "author": "opencode"},
               files=[("files", ("spec.md", b"# the spec"))], headers=H)
    assert r.status_code == 200, r.text
    st = _wait(c, "made")
    assert st["ok"] and st["document"] == "problem.yaml" and "files: spec.md" in "\n".join(st["log"])
    info = c.get("/api/apps/made").json()
    assert info["document"] == "problem.yaml" and info["id"] == "made"
    assert not (tmp / "data/users/bob/apps/made/.attachments").exists(), "the attachments go once read"
    assert c.post("/api/apps/new-by-agent", data={"name": "made", "prompt": "again"}, headers=H).status_code == 400
    assert c.post("/api/apps/new-by-agent", data={"name": "z", "prompt": "x", "author": "nobody"}, headers=H).status_code == 400
    assert not (tmp / "data/users/bob/apps/z").exists(), "a refused start leaves no loop behind"


def test_an_agent_revises_a_problem_keeping_its_name_and_says_what_changed(server, monkeypatch):
    _app, c, tmp = server
    c.post("/api/apps", data={"name": "x"}, files=[("files", ("x.problem.yaml", b"statement: as it was\n"))], headers=H)
    monkeypatch.setenv("FAKE_SLEEP", "1.0")
    assert c.post("/api/apps/x/author", data={"prompt": "say it better", "author": "opencode"}, headers=H).status_code == 200
    assert c.post("/api/apps/x/start", json={"passes": 1}, headers=H).status_code == 409, "not while an agent writes"
    assert c.post("/api/apps/x/author", data={"prompt": "again", "author": "opencode"}, headers=H).status_code == 409
    st = _wait(c, "x")
    loop = tmp / "data/users/bob/apps/x"
    assert st["ok"] and st["document"] == "x.problem.yaml" and not (loop / "problem.yaml").exists(), "the loop keeps its document's name"
    assert "revised by the agent" in (loop / "x.problem.yaml").read_text()
    assert "as it was" in st["before"] and "revised" in st["after"]
    assert c.get("/api/apps/x").json()["document"] == "x.problem.yaml"


def test_a_server_without_the_sandbox_says_so_to_its_runs():
    env = {"FLUX_SANDBOX": "1"}
    sandbox_env(env, False, {})
    assert env["FLUX_SANDBOX"] == "0", "a run's own default is the sandbox: a --no-sandbox server must say no"
    assert os.environ.get("FLUX_SANDBOX") == "0"
