"""D1122-D1125: New loop › Agent from the web -- the author's work as lines, its question answered from the page,
the new loop's files with who made them, its decisions, and the draft made (with one cheap pass or none). A
stand-in for `flux ask --no-run` keeps the CLI's side of the contract (flux_loop.author: its activity and inbox in
`runs/author` of its folder, `decisions.json` beside the document); tests/unit/test_author_activity.py the rest."""

from __future__ import annotations

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
def row(k, text, **x):
    with open(os.path.join(j, "activity.jsonl"), "a") as fh:
        fh.write(json.dumps({"t": time.time(), "k": k, "text": text, **x}) + "\n")
assert os.path.isfile(os.path.join(d, ".claude", "skills", "flux-loop-creator", "SKILL.md")), "the skills are placed first"
import shutil
os.makedirs(os.path.join(d, "library"), exist_ok=True)
for i, a in enumerate(args):
    if a == "--file":
        shutil.copy(args[i + 1], os.path.join(d, "library"))          # as flux ask copies its inputs
row("read", "spec.md")
answer = "none"
if "--questions" in args:
    open(os.path.join(j, "inbox.jsonl"), "a").close()
    row("ask", "Which signedness?", options=["Signed", "Unsigned"], wait_s=30)
    for _ in range(600):
        lines = open(os.path.join(j, "inbox.jsonl")).read().splitlines()
        if lines:
            answer = json.loads(lines[-1])["text"]
            row("answer", answer, by="you")
            break
        time.sleep(0.05)
open(os.path.join(d, "problem.yaml"), "w").write(
    "statement: echo, " + answer + "\nlanguage: text\nflow:\n  test: 'true'\n  measure:\n    quick: {command: 'fluxpreset rtl measure {artifact} --stage synth', metrics: [area_um2]}\n")
open(os.path.join(d, "golden.py"), "w").write("# golden\n")
open(os.path.join(d, "AUTHOR-BRIEF.md"), "w").write("the brief\n")
open(os.path.join(d, "decisions.json"), "w").write(json.dumps([{"step": "prompt", "what": "Echo", "why": "you said " + answer}]))
row("write", "problem.yaml")
row("check", "the check passed", tone="ok")
print("run it:", os.environ.get("FLUX_ASK_RUN_HINT", "flux task run " + os.path.join(d, "problem.yaml")), flush=True)
'''


@pytest.fixture()
def server(tmp_path, monkeypatch):
    fake = tmp_path / "flux-fake"
    fake.write_text(FAKE)
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    from web_agents import install

    install(monkeypatch, tmp_path)
    import flux_web.authoring as au

    monkeypatch.setattr(au.shutil, "which", lambda name, path=None: str(fake) if name == "flux" else None)
    store = Store(tmp_path / "data")
    store.add_user("bob", "another long secret")
    for agent in ("opencode", "claude", "codex"):
        store.server_set(f"agent-test:bob:{agent}", {"ok": True})
    app = create_app(tmp_path / "data", sandbox=False)
    c = TestClient(app)
    c.post("/api/login", json={"name": "bob", "password": "another long secret"}, headers=H)
    return app, c, tmp_path


def _until(c, name, ok, limit_s=20.0):
    st = {}
    for _ in range(int(limit_s * 20)):
        st = c.get(f"/api/apps/{name}/author").json()
        if ok(st):
            return st
        time.sleep(0.05)
    raise AssertionError(f"never: {st}")


def test_the_author_asks_the_page_answers_and_the_draft_is_reviewed_and_made(server, monkeypatch):
    app, c, tmp = server
    r = c.post("/api/apps/new-by-agent", data={"name": "drafted", "prompt": "echo", "author": "opencode", "ask_me": "true"},
               files=[("files", ("spec.md", b"# the spec"))], headers=H)
    assert r.status_code == 200, r.text
    assert c.get("/api/apps/drafted").json()["agent_draft"] is True
    st = _until(c, "drafted", lambda s: s.get("question"))
    assert st["running"] and st["question"]["options"] == ["Signed", "Unsigned"] and st["now"] == "waiting for your answer"
    assert [(a["k"], a["text"]) for a in st["activity"]][:2] == [("read", "spec.md"), ("ask", "Which signedness?")]
    assert {"path": "library/spec.md", "by": "yours"} in st["files"]
    assert c.post("/api/apps/drafted/author/make", json={}, headers=H).status_code == 409, "not while it writes"
    assert c.post("/api/apps/drafted/author/answer", json={"text": "Signed"}, headers=H).json()["ok"]
    st = _until(c, "drafted", lambda s: not s["running"])
    assert st["ok"] and ("answer", "Signed") in [(a["k"], a["text"]) for a in st["activity"]], "the lines outlive the job"
    assert st["question"] is None
    assert c.post("/api/apps/drafted/author/answer", json={"text": "late"}, headers=H).status_code == 409
    loop = tmp / "data/users/bob/apps/drafted"
    assert "statement: echo, Signed" in (loop / "problem.yaml").read_text()
    for gone in (".claude", ".agents", ".opencode", "AUTHOR-BRIEF.md", "decisions.json"):
        assert not (loop / gone).exists(), f"{gone} is the author's, not the loop's"
    by = {f["path"]: f["by"] for f in st["files"]}
    assert by == {"problem.yaml": "written", "golden.py": "written", "library/spec.md": "yours"}, by
    got = c.get("/api/apps/drafted/author/decisions").json()
    assert got["from"] == "agent" and got["decisions"] == [{"step": "prompt", "what": "Echo", "why": "you said Signed"}]
    assert got["first_stage"] == "synth" and got["document"] == "problem.yaml"
    started = []
    monkeypatch.setattr(app.state.runs, "start", lambda *a, **kw: started.append(a[-1]))
    made = c.post("/api/apps/drafted/author/make", json={"first_pass": True}, headers=H)
    assert made.status_code == 200, made.text
    assert started == [{**started[0], "passes": 1, "screen_only": True}], started
    assert c.get("/api/apps/drafted").json()["agent_draft"] is False
    assert c.get("/api/apps/drafted/author").json()["made"], "made: the Overview shows its authoring no more"


def test_a_file_given_hidden_wears_its_lock_and_the_log_names_no_copy(server):
    """The drafting files list says a file given hidden from AI is (D1159), while the author works and once it
    ended; the log's last word is how the loop runs (Start), not the author's copy, removed after."""
    _app, c, _tmp = server
    r = c.post("/api/apps/new-by-agent", data={"name": "hush", "prompt": "echo", "author": "opencode", "ask_me": "true", "hidden": ["held.json"]},
               files=[("files", ("spec.md", b"# the spec")), ("files", ("held.json", b"[1]"))], headers=H)
    assert r.status_code == 200, r.text
    st = _until(c, "hush", lambda s: s.get("question"))
    rows = {f["path"]: f for f in st["files"]}
    assert rows["library/held.json"] == {"path": "library/held.json", "by": "yours", "hidden": True}, rows
    assert "hidden" not in rows["library/spec.md"], rows
    assert c.post("/api/apps/hush/author/answer", json={"text": "Signed"}, headers=H).json()["ok"]
    st = _until(c, "hush", lambda s: not s["running"])
    rows = {f["path"]: f for f in st["files"]}
    assert rows["library/held.json"].get("hidden") is True and "hidden" not in rows["library/spec.md"], rows
    said = [ln for ln in st["log"] if ln.startswith("run it:")]
    assert said == ["run it: Start, on the loop's page"], st["log"]


def test_a_draft_without_decisions_reads_them_from_its_document(server):
    _app, c, tmp = server
    c.post("/api/apps", data={"name": "plain"}, files=[("files", ("problem.yaml",
           b"statement: Add two numbers. Then more.\nlanguage: text\nflow: {test: 'true'}\nobjectives: [{metric: area_um2, direction: minimize}]\n"))], headers=H)
    got = c.get("/api/apps/plain/author/decisions").json()
    assert got["from"] == "document" and got["why"] == "no decisions.json"
    assert [d["step"] for d in got["decisions"]] == ["prompt", "checks", "objective", "graph"]
    assert got["decisions"][2]["what"] == "minimize area_um2"


def test_what_an_author_can_use(server, monkeypatch, tmp_path):
    _app, c, _tmp = server
    lib = tmp_path / "shared-lib"
    (lib / "src").mkdir(parents=True)
    for n in ("a.pdf", "b.md", "src/c.v", ".hidden.pdf"):
        (lib / n).write_text("x")
    monkeypatch.setenv("FLUX_LIBRARY", str(lib))
    got = c.get("/api/new-by-agent/uses").json()
    assert got["papers"] == 2 and got["skills"] == ["flux-loop-creator", "flux-loop-commands"] and "mul8" in got["bundled"]
