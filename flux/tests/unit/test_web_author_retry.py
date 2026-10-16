"""D1156, D1158: a draft its author's check refused is not a dead end on the web. The job ends with the draft kept and
the refusal on the page (`error`), resumable when the agent left a session; Try again and a note start another round
of repairs (`flux ask --repair`, the same session, the note), its work lines after the first round's. A stand-in for
`flux ask --no-run` keeps the CLI's side (flux_loop.author: `outcome.json` and `session.json` beside its journal)."""

from __future__ import annotations

import json
import stat
import time

import pytest
from fastapi.testclient import TestClient

from flux_web import create_app
from flux_web.store import Store

H = {"X-Flux": "1"}
FAKE = r'''#!/usr/bin/env python3
import json, os, sys, time
args = sys.argv[1:]
d = args[args.index("--dir") + 1]
j = os.path.join(d, "runs", "author")
os.makedirs(j, exist_ok=True)
with open(os.path.join(os.environ["FAKE_LOG"]), "a") as fh:
    fh.write(json.dumps(args) + "\n")
def row(k, text, **x):
    with open(os.path.join(j, "activity.jsonl"), "a") as fh:
        fh.write(json.dumps({"t": time.time(), "k": k, "text": text, **x}) + "\n")
def outcome(error):
    open(os.path.join(j, "outcome.json"), "w").write(json.dumps({"error": error, "session": "ses_9", "repairs": 3}))
if "--repair" not in args:
    open(os.path.join(d, "problem.yaml"), "w").write("statement: echo\nlanguage: text\nflow:\n  test: '{python} {home}/check.py {artifact}'\n")
    open(os.path.join(j, "session.json"), "w").write(json.dumps({"agent": "opencode", "session": "ses_9"}))
    row("write", "problem.yaml")
    row("check", "stopped after 3 repairs: the draft stays", tone="bad")
    outcome("`problem.yaml` names 1 path the loop folder does not have:\n  - check.py  (named by flow.test)")
    sys.exit(1)
open(os.path.join(d, "check.py"), "w").write("print('0 failing')\n")
row("write", "check.py")
row("check", "the check passed", tone="ok")
outcome("")
'''


@pytest.fixture()
def server(tmp_path, monkeypatch):
    fake = tmp_path / "flux-fake"
    fake.write_text(FAKE)
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("FAKE_LOG", str(tmp_path / "argv.jsonl"))
    from web_agents import install

    install(monkeypatch, tmp_path)
    import flux_web.authoring as au

    monkeypatch.setattr(au.shutil, "which", lambda name, path=None: str(fake) if name == "flux" else None)
    store = Store(tmp_path / "data")
    store.add_user("bob", "another long secret")
    for agent in ("opencode", "claude", "codex"):
        store.server_set(f"agent-test:bob:{agent}", {"ok": True})
    c = TestClient(create_app(tmp_path / "data", sandbox=False))
    c.post("/api/login", json={"name": "bob", "password": "another long secret"}, headers=H)
    return c, tmp_path


def _ended(c, name, limit_s=20.0):
    for _ in range(int(limit_s * 20)):
        st = c.get(f"/api/apps/{name}/author").json()
        if st.get("ever") and not st["running"] and st.get("ended"):
            return st
        time.sleep(0.05)
    raise AssertionError(f"never ended: {st}")


def test_a_refused_draft_stays_with_its_refusal_and_another_round_repairs_it(server):
    c, tmp = server
    assert c.post("/api/apps/new-by-agent", data={"name": "drafted", "prompt": "echo", "author": "opencode"}, headers=H).status_code == 200
    st = _ended(c, "drafted")
    assert not st["ok"] and st["document"] == "problem.yaml", "the draft stays"
    assert "  - check.py  (named by flow.test)" in st["error"] and st["resumable"] and "session" not in st
    assert (tmp / "data" / "users" / "bob" / "apps" / "drafted" / "problem.yaml").is_file()
    assert c.post("/api/apps/drafted/author/retry", json={"text": "write check.py"}, headers=H).json()["ok"] == "the agent repairs the draft as told"
    st = _ended(c, "drafted")
    argv = [json.loads(x) for x in (tmp / "argv.jsonl").read_text().splitlines()]
    assert argv[1][argv[1].index("--author") + 1] == "opencode", "the same agent"
    assert argv[1][argv[1].index("--repair"):][:5] == ["--repair", "--session", "ses_9", "--note", "write check.py"]
    assert st["ok"] and st["error"] == "" and st["rounds"] == 2
    texts = [a["text"] for a in st["activity"]]
    assert texts[0] == "problem.yaml" and texts[-2:] == ["check.py", "the check passed"], "the first round's lines stay"
    r = c.post("/api/apps/drafted/author/retry", json={}, headers=H)
    assert r.status_code == 409 and "no refused draft" in r.text
