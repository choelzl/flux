"""A running coding agent shows in the TUI as a model turn does (D668, D675): its task row gets
the elapsed time, each tool call with its command or file, the last tool output, the tail of its
thinking and of its words, while it runs, not only at the end."""

from __future__ import annotations

import json
import sys

import flux_profile
from flux_loop.agent import AgentSpec, _Live, _parse, run_turn

SLOW = r'''import json, sys, time
for ev in [{"type": "tool_use", "part": {"tool": "bash", "state": {"input": {"command": "python3 -c 'print(42)'"}, "output": "42"}}},
           {"type": "reasoning", "part": {"text": "a ripple adder is enough"}},
           {"type": "text", "part": {"text": "reading the brief"}},
           {"type": "tool_use", "part": {"tool": "edit", "state": {"input": {"filePath": "/w/draft.sv"}}}},
           {"type": "text", "part": {"text": "wrote the file"}}]:
    print(json.dumps(ev), flush=True)
    time.sleep(0.8)
'''


class _Listener:
    def __init__(self):
        self.updates: list[dict] = []
        self.ends: list[dict] = []

    def phase_start(self, name, why, params):
        return name

    def phase_update(self, token, name, fields):
        self.updates.append(dict(fields))

    def phase_end(self, token, name, seconds, failed, output):
        self.ends.append({"name": name, **dict(output or {})})


def test_the_running_agent_streams_its_tools_and_words(tmp_path):
    fake = tmp_path / "slow_agent.py"
    fake.write_text(SLOW)
    spec = AgentSpec("fake", (sys.executable, str(fake)), None, "opencode", timeout_s=30)
    lis = _Listener()
    flux_profile.set_listener(lis)
    try:
        turn = run_turn(spec, spec.argv, {"prompt": "p", "name": "critique"}, workdir=tmp_path)
    finally:
        flux_profile.clear_listener()
    assert turn.ok and "wrote the file" in turn.text
    mid = [u for u in lis.updates if "tool calls" in u]
    # an update at most once a second: under load the first may already count both tools
    assert mid and mid[0]["tool calls"].startswith("1. bash: python3 -c 'print(42)'"), "the row updated while the agent ran"
    assert any("elapsed" in u for u in lis.updates)
    assert lis.ends and lis.ends[-1]["name"] == "agent: fake" and lis.ends[-1]["tool calls"].endswith("2. edit: draft.sv")
    end = lis.ends[-1]
    assert end["last tool output"] == "42" and "ripple" in end["thinking (live tail)"] and "wrote the file" in end["reply (live tail)"]


def test_claude_stream_json_is_read_live_and_its_result_is_the_answer():
    live = _Live("claude")
    events = [{"type": "system", "subtype": "init"},
              {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash"}]}},
              {"type": "user", "message": {"content": "tool output as a plain string"}},
              {"type": "assistant", "message": {"content": [{"type": "text", "text": "done"}]}},
              {"type": "result", "result": "the file is written", "session_id": "s-1"},
              {"type": "rate_limit_event"}]
    for ev in events:
        live.feed(json.dumps(ev) + "\n")
    assert live.fields() == {"tool calls": "1. Bash", "reply (live tail)": "done\n"}
    assert _parse("claude", "".join(json.dumps(e) + "\n" for e in events)) == ("the file is written", "s-1")


def test_claude_partial_messages_stream_the_words_and_count_redacted_thinking():
    """--include-partial-messages: the words token by token (the whole message is not added
    again), the tool's command, and a thinking the model redacts shown by its size."""
    live = _Live("claude")
    delta = lambda d: {"type": "stream_event", "event": {"type": "content_block_delta", "index": 0, "delta": d}}
    events = [delta({"type": "thinking_delta", "thinking": "", "estimated_tokens": 50}),
              delta({"type": "thinking_delta", "thinking": "", "estimated_tokens": 70}),
              delta({"type": "text_delta", "text": "The smallest "}), delta({"type": "text_delta", "text": "adder"}),
              {"type": "stream_event", "event": {"type": "content_block_stop", "index": 1}},
              {"type": "assistant", "message": {"content": [{"type": "text", "text": "The smallest adder"}]}},
              {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash",
                                                             "input": {"command": "python3 -c 'print(255+255)'"}}]}},
              {"type": "user", "message": {"content": [{"type": "tool_result", "content": [{"type": "text", "text": "510"}]}]}},
              {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Write",
                                                             "input": {"file_path": "/w/note.txt", "content": "x"}}]}}]
    for ev in events:
        live.feed(json.dumps(ev) + "\n")
    f = live.fields()
    assert f["reply (live tail)"] == "The smallest adder\n"
    assert f["tool calls"] == "1. Bash: python3 -c 'print(255+255)'\n2. Write: note.txt"
    assert f["last tool output"] == "510" and f["thinking"].startswith("about 120 tokens")


def test_the_presets_stream_what_they_can_show():
    from flux_loop.agent import agent_spec

    assert "--thinking" in agent_spec("opencode").argv and "--thinking" in agent_spec("opencode").resume
    assert "--include-partial-messages" in agent_spec("claude").argv and "--include-partial-messages" in agent_spec("claude").resume


def test_a_silent_agent_says_why(tmp_path):
    """D676: the model and version it started with, its status, a rate limit that holds it, its
    stderr, and how long since its last output line -- so 300 s of nothing has a reason."""
    live = _Live("claude")
    assert live.fields(310.0, 10.0) == {"output": "none yet after 300s"}
    live.feed(json.dumps({"type": "system", "subtype": "init", "model": "claude-x", "claude_code_version": "2.1"}) + "\n", 20.0)
    live.feed(json.dumps({"type": "system", "subtype": "status", "status": "requesting"}) + "\n", 21.0)
    live.feed(json.dumps({"type": "rate_limit_event", "rate_limit_info": {"status": "rejected", "rateLimitType": "five_hour",
                                                                          "resetsAt": 1790773200}}) + "\n", 22.0)
    live.feed_err("API Error: 529 overloaded, retrying\n")
    f = live.fields(82.0, 10.0)
    assert f["agent"] == "claude-x, Claude Code 2.1" and f["status"] == "requesting"
    assert f["rate limit"].startswith("rejected (five_hour, resets ") and "529" in f["stderr"]
    assert f["output"] == "3 lines, the last 60s ago"
    live.feed(json.dumps({"type": "system", "subtype": "api_retry", "attempt": 2, "error_status": 529, "uuid": "u"}) + "\n", 83.0)
    assert live.fields()["status"] == "api_retry: attempt 2, error_status 529"
    live.feed(json.dumps({"type": "rate_limit_event", "rate_limit_info": {"status": "allowed"}}) + "\n", 84.0)
    assert "rate limit" not in live.fields()
    oc = _Live("opencode")
    oc.feed(json.dumps({"type": "error", "error": {"name": "UnknownError", "data": {"message": "exceeds the available context size"}}}) + "\n", 1.0)
    assert oc.fields()["status"] == "error: exceeds the available context size"


def test_a_claude_turn_stopped_before_its_result_keeps_its_session():
    """D677: a timeout leaves no `result` event; every event names the session."""
    events = [{"type": "system", "subtype": "init", "session_id": "s-9"},
              {"type": "assistant", "session_id": "s-9", "message": {"content": [{"type": "text", "text": "working"}]}}]
    text, session = _parse("claude", "".join(json.dumps(e) + "\n" for e in events))
    assert session == "s-9"


def test_claude_is_given_the_loops_folder_when_it_works_outside_it(tmp_path):
    """D710: an Ask works in `runs/asks/<id>/` and reads the loop around it; Claude Code reads
    outside its working folder only through --add-dir."""
    from dataclasses import replace

    argv_file = tmp_path / "argv.json"
    fake = tmp_path / "fake_claude.py"
    fake.write_text(f"import json, sys\njson.dump(sys.argv[1:], open({str(argv_file)!r}, 'w'))\n")
    loop = tmp_path / "loop"
    work = loop / "runs" / "asks" / "1"
    work.mkdir(parents=True)
    spec = AgentSpec("fake", (sys.executable, str(fake)), None, "text", timeout_s=30)
    spec = replace(spec, add_dir=("--add-dir",))
    run_turn(spec, spec.argv, {"prompt": "p", "name": "answer", "home": str(loop)}, workdir=work)
    got = json.loads(argv_file.read_text())
    assert got[-2:] == ["--add-dir", str(loop.resolve())]
    run_turn(spec, spec.argv, {"prompt": "p", "name": "answer", "home": str(work / "inner")}, workdir=work)
    assert "--add-dir" not in json.loads(argv_file.read_text()), "a home inside the workdir needs nothing"
