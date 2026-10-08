"""Endpoint reloads resume the same task/session, including an agent stuck after an API error."""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from flux_llm import recovery
from flux_loop import agent


@pytest.fixture
def endpoint(monkeypatch):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append((self.path, {k.lower(): v for k, v in self.headers.items()}))
            self.send_response(503 if len(requests) < 3 else 200)
            self.end_headers()
            self.wfile.write(b'{"data": [{"id": "test-model"}]}')

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(recovery, "POLL_S", 0.01)
    monkeypatch.setattr(agent, "RECOVERY_IDLE_S", 0.05)
    monkeypatch.setattr(agent, "_about", lambda *_: "test agent")
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


FAKE = r'''import json, os, sys, time
from pathlib import Path
mode, kind, freeze, artifact = sys.argv[1:5]
artifact = Path(artifact)
sid = "kept-session"
def emit(doc): print(json.dumps(doc), flush=True)
text = sys.stdin.read()
with open(artifact.parent / "calls.jsonl", "a") as fh:
    fh.write(json.dumps({"mode": mode, "session": sys.argv[5] if mode == "resume" else sid,
                         "prompt": text}) + "\n")
if mode == "first":
    (artifact.parent / "old.pid").write_text(str(os.getpid()))
    artifact.write_text("partial work")
    if kind == "opencode": emit({"type": "error", "sessionID": sid, "error": {"data": {"message": "502 Bad Gateway"}}})
    elif kind == "claude":
        emit({"type": "system", "subtype": "init", "session_id": sid})
        emit({"type": "assistant", "error": "server_error", "session_id": sid,
              "message": {"content": [{"type": "text", "text": "API Error: 502 Bad Gateway"}]}})
    else:
        emit({"type": "thread.started", "thread_id": sid})
        emit({"type": "turn.failed", "error": {"message": "HTTP 502 Bad Gateway"}})
    if freeze == "yes":
        while True: time.sleep(0.1)
    sys.exit(0)  # some agents report an API error yet exit cleanly
assert sys.argv[5] == sid and "Continue the interrupted task" in text
assert artifact.read_text() == "partial work"
artifact.write_text("finished work")
if kind == "opencode": emit({"type": "text", "sessionID": sid, "part": {"text": "written"}})
elif kind == "claude": emit({"type": "result", "session_id": sid, "result": "written"})
else:
    emit({"type": "thread.started", "thread_id": sid})
    emit({"type": "item.completed", "item": {"type": "agent_message", "text": "written"}})
    emit({"type": "turn.completed", "usage": {}})
'''


@pytest.mark.parametrize("kind", ["opencode", "claude", "codex"])
@pytest.mark.parametrize("freeze", ["yes", "no"])
def test_reload_continues_same_session_after_endpoint_returns(endpoint, monkeypatch, tmp_path, kind, freeze):
    base, requests = endpoint
    monkeypatch.setenv("FLUX_RESTART_TEST_BASE_URL", base)
    monkeypatch.setenv("FLUX_RESTART_TEST_MODEL", "test-model")
    monkeypatch.setenv("FLUX_RESTART_TEST_API_KEY", "only-this-agent")
    monkeypatch.setenv("FLUX_REMOTE_API_KEY", "unrelated-model-key")
    fake = tmp_path / "agent.py"
    fake.write_text(FAKE)
    artifact = tmp_path / "design.py"
    argv = (sys.executable, str(fake), "first", kind, freeze, "{artifact}")
    resume = (sys.executable, str(fake), "resume", kind, freeze, "{artifact}", "{session}")
    spec = agent.AgentSpec("restart_test", argv, resume, kind, timeout_s=10, kind=kind)
    turn = agent.run_turn(spec, spec.argv, {"prompt": "original task", "artifact": str(artifact)}, workdir=tmp_path)
    assert turn.ok and turn.session == "kept-session" and turn.resumed and turn.began == "fresh"
    assert artifact.read_text() == "finished work"
    calls = [json.loads(line) for line in (tmp_path / "calls.jsonl").read_text().splitlines()]
    assert [call["mode"] for call in calls] == ["first", "resume"]
    assert len(requests) == 3 and all(path == "/v1/models" for path, _headers in requests)
    for _path, headers in requests:
        credential = headers.get("x-api-key") if kind == "claude" else headers.get("authorization")
        assert "only-this-agent" in credential
        assert "unrelated-model-key" not in str(headers)
    with pytest.raises(ProcessLookupError):
        os.kill(int((tmp_path / "old.pid").read_text()), 0)


def test_real_progress_clears_an_api_error_and_tool_failures_are_not_endpoint_errors():
    live = agent._Live("opencode")
    live.feed(json.dumps({"type": "error", "error": {"data": {"message": "502 Bad Gateway"}}}))
    assert live.transport_error
    live.feed(json.dumps({"type": "reasoning", "part": {"text": "the retry worked"}}))
    assert not live.transport_error
    for event in [{"type": "tool_use", "part": {"tool": "bash", "state": {"error": "curl got HTTP 502"}}},
                  {"type": "text", "part": {"text": "The log says API Error: 502"}},
                  {"type": "error", "error": {"data": {"message": "403 not on the network allowlist"}}}]:
        live.feed(json.dumps(event))
        assert not live.transport_error
    live.feed_err("debug: counter=502\n")
    assert not live.transport_error
    live.feed_err("API Error: connection reset by peer\n")
    assert live.transport_error
    live.feed(json.dumps({"type": "error", "error": {"data": {"message": "403 not on the network allowlist"}}}))
    assert not live.transport_error, "a permanent refusal supersedes a previous connection failure"
    live.feed(json.dumps({"type": "step_finish", "part": {"reason": "stop"}}))
    live.feed_err("API Error: 503\n")
    assert not live.transport_error, "delayed stderr must not revive an already recovered error"


def test_recovery_is_bounded_and_does_not_reset_the_turn_deadline(monkeypatch, tmp_path):
    calls, waits = [], []

    def failed(spec, argv, subs, **_kw):
        calls.append((spec.timeout_s, argv, subs))
        return agent.Turn(False, 1, "", "same", transport_error="HTTP 502 Bad Gateway")

    monkeypatch.setattr(agent, "_run_turn", failed)
    monkeypatch.setattr(agent, "_recovery_endpoint", lambda _: (None, {}))
    monkeypatch.setattr(recovery, "wait_ready", lambda _base, _headers, deadline, _report: waits.append(deadline) or True)
    spec = agent.AgentSpec("test", ("first",), ("resume",), timeout_s=10)
    turn = agent.run_turn(spec, spec.argv, {"prompt": "task"}, workdir=tmp_path)
    assert not turn.ok and len(calls) == agent.RECOVERIES + 1
    assert calls[-1][0] <= calls[0][0]
    assert len({call[2].get("_recovery_deadline") for call in calls[1:]}) == 1
    calls.clear()
    turn = agent.run_turn(replace(spec, resume=None), spec.argv, {"prompt": "task"}, workdir=tmp_path)
    assert not turn.ok and len(calls) == 1, "non-resumable agents must not replay the job"


def test_readiness_wait_respects_deadline_and_manual_interrupt(monkeypatch):
    monkeypatch.setattr(recovery, "POLL_S", 0.01)
    reports = []
    assert not recovery.wait_ready(None, {}, time.monotonic(), reports.append)
    assert "time limit" in reports[-1]
    monkeypatch.setattr(recovery.time, "sleep", lambda _: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        recovery.wait_ready(None, {}, time.monotonic() + 10, reports.append)
