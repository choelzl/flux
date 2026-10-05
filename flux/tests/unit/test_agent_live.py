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
    assert live.fields() == {"tool calls": "1. Bash", "reply (live tail)": "done\n", "steps total": 2,
                             "steps": [{"k": "tool", "name": "Bash", "call": "Bash", "input": {}}, {"k": "text", "text": "done\n"}]}
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


def test_the_steps_keep_the_conversation_in_order_opencode():
    """D712: words, thinking and each tool call with its input and output, in the order they
    came; words or thinking that go on are one step."""
    live = _Live("opencode")
    for ev in [{"type": "reasoning", "part": {"text": "an adder"}},
               {"type": "reasoning", "part": {"text": "ripple is enough"}},
               {"type": "text", "part": {"text": "Reading the brief."}},
               {"type": "tool_use", "part": {"tool": "bash", "state": {"input": {"command": "ls -la"}, "output": "a.sv\nb.sv", "status": "completed"}}},
               {"type": "tool_use", "part": {"tool": "read", "state": {"input": {"filePath": "/w/x.sv"}, "error": "no such file", "status": "error"}}},
               {"type": "text", "part": {"text": "Done."}}]:
        live.feed(json.dumps(ev))
    st = live.steps
    assert [s["k"] for s in st] == ["think", "text", "tool", "tool", "text"]
    assert st[0]["text"] == "an adder\nripple is enough\n"
    assert st[2]["name"] == "bash" and st[2]["call"] == "bash: ls -la" and st[2]["input"] == {"command": "ls -la"}
    assert st[2]["out"] == "a.sv\nb.sv" and st[2]["error"] is False
    assert st[3]["out"] == "no such file" and st[3]["error"] is True
    f = live.fields()
    assert f["steps total"] == 5 and len(f["steps"]) == 5


def test_the_steps_of_claude_join_a_result_to_its_call_and_stream_the_words():
    live = _Live("claude")
    for ev in [{"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "thinking_delta", "thinking": "hm"}}},
               {"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Let me "}}},
               {"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "look."}}},
               {"type": "assistant", "message": {"content": [{"type": "text", "text": "Let me look."},
                                                             {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "cat a"}}]}},
               {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "content": [{"type": "text", "text": "A"}]}]}},
               {"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "thinking_delta", "estimated_tokens": 30}}}]:
        live.feed(json.dumps(ev))
    st = live.steps
    assert [s["k"] for s in st] == ["think", "text", "tool", "think"]
    assert st[1]["text"] == "Let me look.", "the words once: streamed, not again from the whole message"
    assert st[2]["out"] == "A" and st[2]["error"] is False, "the result joined to its call by id"
    assert st[3]["redacted"] == 30


def test_a_turn_keeps_its_steps_for_the_record(tmp_path):
    fake = tmp_path / "slow_agent.py"
    fake.write_text(SLOW)
    spec = AgentSpec("fake", (sys.executable, str(fake)), None, "opencode", timeout_s=30)
    turn = run_turn(spec, spec.argv, {"prompt": "p", "name": "critique"}, workdir=tmp_path)
    assert [s["k"] for s in turn.steps] == ["tool", "think", "text", "tool", "text"]
    assert turn.steps[0]["out"] == "42"


def test_each_agent_gets_its_own_variables_not_the_others(tmp_path, monkeypatch):
    """D718: OpenCode's providers read ANTHROPIC_API_KEY and OPENAI_API_KEY by themselves; an
    agent gets only its own group's variables, and Flux's."""
    import os

    from flux_loop.agent import agent_spec

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("opencode", "claude"):
        p = bin_dir / name
        p.write_text(f"#!{sys.executable}\nimport json, os, sys\n"
                     f"json.dump(dict(os.environ), open({str(tmp_path / name)!r} + ('.version' if '--version' in sys.argv else '') + '.env', 'w'))\n")
        p.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    for k, v in {"ANTHROPIC_API_KEY": "claude-key", "ANTHROPIC_BASE_URL": "https://a.example", "OPENAI_API_KEY": "codex-key",
                 "CLAUDE_CODE_USE_BEDROCK": "1", "FLUX_OPENCODE_API_KEY": "oc-key", "FLUX_REMOTE_API_KEY": "flux-key"}.items():
        monkeypatch.setenv(k, v)
    for name in ("opencode", "claude"):
        spec = agent_spec(name)
        run_turn(spec, spec.argv, {"prompt": "p", "name": "n", "workdir": str(tmp_path)}, workdir=tmp_path)
    oc = json.loads((tmp_path / "opencode.env").read_text())
    assert not {"ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "OPENAI_API_KEY", "CLAUDE_CODE_USE_BEDROCK"} & set(oc)
    assert oc["FLUX_OPENCODE_API_KEY"] == "oc-key" and oc["FLUX_REMOTE_API_KEY"] == "flux-key" and "OPENCODE_CONFIG_CONTENT" in oc
    assert "ANTHROPIC_API_KEY" not in json.loads((tmp_path / "opencode.version.env").read_text()), "its version asked with its own too"
    cl = json.loads((tmp_path / "claude.env").read_text())
    assert cl["ANTHROPIC_API_KEY"] == "claude-key" and "OPENAI_API_KEY" not in cl and "FLUX_OPENCODE_API_KEY" not in cl



def test_an_added_agent_runs_as_its_kind_with_its_own_set(tmp_path, monkeypatch):
    """D807: a server adds `nga`, an OpenCode of its own -- its kind's arguments, its program, its
    own variables (`FLUX_NGA_ENV`), none of another agent's set; a variable the run was given for
    every agent (`FLUX_SHARED_VARS`) reaches it whatever its name."""
    import os

    from flux_loop.agent import agent_kinds, agent_spec
    from flux_loop.document import TaskError, TaskSpec

    prog = tmp_path / "nga-bin"
    prog.write_text(f"#!{sys.executable}\nimport json, os, sys\n"
                    f"json.dump({{'env': dict(os.environ), 'argv': sys.argv}}, open({str(tmp_path / 'nga.json')!r}, 'w'))\n")
    prog.chmod(0o755)
    monkeypatch.setenv("FLUX_AGENTS", json.dumps({"nga": "opencode", "BAD": "opencode", "x": "cursor"}))
    monkeypatch.setenv("FLUX_NGA_BIN", str(prog))
    monkeypatch.setenv("FLUX_NGA_ENV", json.dumps({"NGA_TOKEN": "nga-own", "ANTHROPIC_API_KEY": "for-nga"}))
    monkeypatch.setenv("FLUX_CLAUDE_ENV", json.dumps({"ANTHROPIC_API_KEY": "claude-own"}))
    monkeypatch.setenv("OPENAI_API_KEY", "everyone")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://codex-only.example")
    monkeypatch.setenv("FLUX_SHARED_VARS", "OPENAI_API_KEY")
    assert agent_kinds()["nga"] == "opencode" and "BAD" not in agent_kinds() and "x" not in agent_kinds()
    spec = agent_spec("nga")
    assert (spec.tool, spec.kind, spec.argv[0]) == ("nga", "opencode", str(prog)) and spec.argv[1:3] == ("run", "--format")
    run_turn(spec, spec.argv, {"prompt": "p", "name": "n", "workdir": str(tmp_path)}, workdir=tmp_path)
    got = json.loads((tmp_path / "nga.json").read_text())["env"]
    assert got["NGA_TOKEN"] == "nga-own" and got["ANTHROPIC_API_KEY"] == "for-nga", "its own set, its own names"
    assert "FLUX_CLAUDE_ENV" not in got and "FLUX_NGA_ENV" not in got, "no agent's set as such"
    assert got["OPENAI_API_KEY"] == "everyone" and "OPENAI_BASE_URL" not in got, "a shared variable, not another kind's"
    assert "permission" in json.loads(got["OPENCODE_CONFIG_CONTENT"]), "its kind's denials"
    doc = {"id": "t", "statement": "s", "language": "text", "flow": {"generate": "nga", "test": "true"}}
    assert TaskSpec.from_dict(doc).generator == {"agent": "nga"}
    monkeypatch.delenv("FLUX_AGENTS")
    try:
        TaskSpec.from_dict(doc)
        raise AssertionError("an agent this machine does not have is refused")
    except TaskError as exc:
        assert "nga" in str(exc)


LEAVES = r'''import subprocess, sys
# a server it starts and leaves running, holding the turn's output
subprocess.Popen([sys.executable, "-c", "import time; open(sys.argv[1], 'w').write('up'); time.sleep(300)", sys.argv[1]])
print('{"type": "text", "part": {"text": "done"}}', flush=True)
'''


def _running(word: bytes) -> list[str]:
    import os

    out = []
    for p in os.listdir("/proc"):
        try:
            if p.isdigit() and word in open(f"/proc/{p}/cmdline", "rb").read():
                out.append(p)
        except OSError:
            pass
    return out


def test_what_an_agent_leaves_running_ends_with_its_turn(tmp_path):
    """D768: an agent's turn is a process group of its own -- a server it left running holding the
    turn's output neither keeps the turn waiting nor outlives it."""
    import time

    fake = tmp_path / "leaves.py"
    fake.write_text(LEAVES.replace("import time;", "import sys, time;"))
    mark = tmp_path / "child-up"
    spec = AgentSpec("fake", (sys.executable, str(fake), str(mark)), None, "opencode", timeout_s=60)
    t0 = time.monotonic()
    turn = run_turn(spec, spec.argv, {"prompt": "p", "name": "x"}, workdir=tmp_path)
    assert turn.ok and turn.text == "done" and time.monotonic() - t0 < 20, "not held open by what it left"
    assert mark.exists(), "the child did run"
    left = _running(str(mark).encode())
    assert not left, f"left running: {left}"


def test_an_agent_past_its_time_is_stopped_with_all_it_started(tmp_path):
    import time

    fake = tmp_path / "slow.py"
    fake.write_text("import subprocess, sys, time\nsubprocess.Popen(['sleep', '301'])\ntime.sleep(300)\n")
    spec = AgentSpec("fake", (sys.executable, str(fake)), None, "opencode", timeout_s=2)
    t0 = time.monotonic()
    turn = run_turn(spec, spec.argv, {"prompt": "p", "name": "x"}, workdir=tmp_path)
    assert not turn.ok and turn.rc == 124 and time.monotonic() - t0 < 20
    left = _running(b"sleep\x00301")
    assert not left, f"left running: {left}"
