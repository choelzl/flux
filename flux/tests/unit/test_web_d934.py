"""D934: an agent an admin added is one a loop document may name from the web -- the server's own
loads (read back, preview, save, preflight) know it as its runs do, without os.environ."""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from flux_web import create_app
from flux_web.store import Store

H = {"X-Flux": "1"}

DOC = ("statement: make x\nlanguage: python\nflow:\n  generate: nga\n  test:\n    test: '{python} {home}/check.py {artifact}'\n"
       "  measure:\n    bench: {command: '{python} {home}/bench.py {artifact}', metrics: [time_ms]}\n"
       "objectives:\n  - {metric: time_ms, direction: minimize}\n")


@pytest.fixture()
def server(tmp_path, monkeypatch):
    monkeypatch.delenv("FLUX_AGENTS", raising=False)
    from web_agents import install

    install(monkeypatch, tmp_path)
    store = Store(tmp_path / "data")
    store.add_user("ada", "correct horse battery", "admin")
    store.add_user("bob", "another long secret")
    return create_app(tmp_path / "data", sandbox=False), store


def _client(app, name, password):
    c = TestClient(app)
    assert c.post("/api/login", json={"name": name, "password": password}, headers=H).status_code == 200
    return c


def test_an_added_agent_named_in_a_document_loads_checks_and_saves_through_the_web(server, tmp_path):
    from flux_loop import load_task
    from flux_loop.agent import added_agents, agent_kinds
    from flux_loop.document import TaskError

    app, _store = server
    ada, bob = _client(app, "ada", "correct horse battery"), _client(app, "bob", "another long secret")
    prog = tmp_path / "corp" / "nga"
    prog.parent.mkdir()
    prog.write_text("#!/bin/sh\necho nga 3\n")
    prog.chmod(0o755)
    assert ada.post("/api/admin/agents", json={"name": "nga", "kind": "opencode", "label": "NGA", "bin": str(prog)},
                    headers=H).status_code == 200
    files = [("files", ("x.problem.yaml", DOC.encode())), ("files", ("check.py", b"print('0 failing')\n")),
             ("files", ("bench.py", b"print('time_ms=1')\n"))]
    assert bob.post("/api/apps", data={"name": "x"}, files=files, headers=H).status_code == 200
    v = bob.get("/api/apps/x/document").json()
    assert not v.get("error"), v.get("error")
    assert "nga" in str(v["normal"]["flow"]["generate"]), v["normal"]["flow"]
    new = DOC.replace("make x", "make x faster")
    p = bob.post("/api/apps/x/document/preview", json={"text": new, "kept": []}, headers=H)
    assert p.status_code == 200 and "faster" in p.json()["after"], p.text
    r = bob.put("/api/apps/x/document", json={"text": new, "kept": []}, headers=H)
    assert r.status_code == 200 and not r.json().get("error"), r.text
    assert bob.get("/api/apps/x/preflight").status_code == 200
    # the server's own process environment is untouched: outside a request, nga is not known
    assert "nga" not in agent_kinds() and "FLUX_AGENTS" not in os.environ
    doc = tmp_path / "doc.yaml"
    doc.write_text(DOC)
    with pytest.raises(TaskError, match="nga"):
        load_task(str(doc))
    with added_agents({"nga": "opencode"}):
        assert agent_kinds()["nga"] == "opencode"
        load_task(str(doc))
        assert "nga" not in agent_kinds(env={}), "a run's own environment says its agents alone"
    # an agent removed is no longer one a document may name
    assert ada.delete("/api/admin/agents/nga", headers=H).status_code == 200
    assert bob.get("/api/apps/x/document").json().get("error"), "nga is gone: the loader says so"
