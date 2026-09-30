"""A running coding agent shows in the TUI as a model turn does (D668): its task row gets the
elapsed time, the tools it called and the tail of its words while it runs, not only at the end."""

from __future__ import annotations

import json
import sys

import flux_profile
from flux_loop.agent import AgentSpec, _Live, _parse, run_turn

SLOW = r'''import json, sys, time
for ev in [{"type": "tool_use", "part": {"tool": "bash"}}, {"type": "text", "part": {"text": "reading the brief"}},
           {"type": "tool_use", "part": {"tool": "edit"}}, {"type": "text", "part": {"text": "wrote the file"}}]:
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
    assert mid and mid[0]["tool calls"].startswith("1: bash"), "the row updated while the agent ran"
    assert any("elapsed" in u for u in lis.updates)
    assert lis.ends and lis.ends[-1]["name"] == "agent: fake" and "2: bash, edit" in lis.ends[-1]["tool calls"]


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
    assert live.fields() == {"tool calls": "1: Bash", "agent (live tail)": "done\n"}
    assert _parse("claude", "".join(json.dumps(e) + "\n" for e in events)) == ("the file is written", "s-1")
