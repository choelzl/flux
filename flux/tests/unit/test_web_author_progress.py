"""Creation agents stream before a loop exists; status survives finalization and bad files."""

import json
import os
import subprocess
import sys
import time
from types import SimpleNamespace

from flux_web.authoring import Authoring, WORK, _progress


def test_creation_cli_streams_tools_and_thinking_before_finishing(tmp_path):
    work = tmp_path / WORK / "loop"
    work.mkdir(parents=True)
    agent = tmp_path / "fake_agent.py"
    agent.write_text("""import json, pathlib, time
for ev in [
    {"type":"tool_use","part":{"tool":"write","state":{"input":{"filePath":"problem.yaml"}}}},
    {"type":"reasoning","part":{"text":"Choosing a checker and measurement scripts"}},
    {"type":"text","part":{"text":"Preparing the loop files"}}]:
    print(json.dumps(ev), flush=True)
until=time.monotonic()+20
while not pathlib.Path('continue').exists() and time.monotonic()<until:
    time.sleep(.05)
pathlib.Path('problem.yaml').write_text('statement: Test creation progress\\nlanguage: text\\nflow: {test: "true"}\\n')
""")
    spec = {"command": [sys.executable, str(agent)], "name": "test-author", "output": "opencode", "probe": False, "timeout_s": 30}
    authoring = Authoring()
    files = authoring.files(tmp_path)
    files["state"].parent.mkdir()
    with files["log"].open("wb") as log:
        proc = subprocess.Popen([sys.executable, "-m", "flux_cli.main", "ask", "Create a test loop", "--no-run",
                                 "--no-sandbox", "--dir", str(work), "--author", json.dumps(spec)],
                                stdout=log, stderr=subprocess.STDOUT)
    files["state"].write_text(json.dumps({"pid": proc.pid, "started": time.time(), "ended": None, "author": "test-author", "work_directory": "loop"}))
    try:
        deadline = time.monotonic() + 15
        st = {}
        while time.monotonic() < deadline:
            st = authoring.state(tmp_path)
            if (st.get("progress") or {}).get("fields", {}).get("thinking (live tail)"):
                break
            assert proc.poll() is None, files["log"].read_text()
            time.sleep(.1)
        assert st["running"] and st["elapsed_s"] > 0
        assert st["progress"]["phase"] == "agent: test-author"
        fields = st["progress"]["fields"]
        assert "problem.yaml" in fields["tool calls"]
        assert "Choosing a checker" in fields["thinking (live tail)"]
        assert fields["steps"] and "output" in fields
        assert "writing the initial problem" in "\n".join(st["log"])
        (work / "continue").touch()
        assert proc.wait(timeout=15) == 0, files["log"].read_text()
        meta = {}
        authoring._finish(tmp_path, SimpleNamespace(set_meta=lambda name, **fields: meta.update(fields)), "test", 0)
        finished = authoring.state(tmp_path)
        assert finished["ok"] and not finished["running"] and not work.exists()
        assert finished["progress"]["phase"] == "check: problem document"
        assert "Choosing a checker" in finished["progress"]["fields"]["thinking (live tail)"]
        assert meta["document"] == "problem.yaml"
    finally:
        if proc.poll() is None:
            (work / "continue").touch()
            proc.wait(timeout=30)


def test_author_progress_ignores_partial_records_and_does_not_expose_prompt_params(tmp_path):
    root = tmp_path / WORK / "runs" / "author"
    root.mkdir(parents=True)
    events = [{"ev": "start", "id": 1, "t": 100, "name": "author: claude", "params": {"prompt": "PRIVATE PROMPT"}},
              {"ev": "start", "id": 2, "t": 101, "name": "agent: claude"}]
    (root / "events.jsonl").write_text("\n".join(map(json.dumps, events)) + '\n{"ev":')
    (root / "live.json").write_text(json.dumps({"t": 102, "updates": {"2": {"status": "retrying after HTTP 502", "output": "none yet after 10s", "prompt": "PRIVATE PROMPT"}}}))
    progress = _progress(tmp_path)
    assert progress == {"phase": "agent: claude", "updated": 102,
                        "fields": {"status": "retrying after HTTP 502", "output": "none yet after 10s"}}
    (root / "live.json").write_text("not json")
    assert _progress(tmp_path)["phase"] == "agent: claude"
    (root / "events.jsonl").write_text("[]\ninvalid\n")
    assert _progress(tmp_path) is None


def test_author_status_cannot_read_progress_or_logs_outside_the_loop(tmp_path):
    loop = tmp_path / "loop"
    loop.mkdir()
    private = tmp_path / "private"
    private.mkdir()
    (private / "events.jsonl").write_text(json.dumps({"ev": "start", "id": 1, "name": "SECRET"}))
    authoring = Authoring()
    files = authoring.files(loop)
    files["state"].parent.mkdir()
    files["state"].write_text(json.dumps({"pid": os.getpid(), "started": time.time(), "ended": None}))
    files["log"].symlink_to(private / "events.jsonl")
    (loop / WORK).mkdir()
    (loop / WORK / "runs").symlink_to(private)
    (private / "author").mkdir()
    (private / "author" / "events.jsonl").write_text((private / "events.jsonl").read_text())
    state = authoring.state(loop)
    assert state["running"] and state["progress"] is None and state["log"] == []
    assert "SECRET" not in json.dumps(state)
