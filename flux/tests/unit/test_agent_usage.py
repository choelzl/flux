"""Interrupted agents retain reported usage without counting cumulative events twice."""

from __future__ import annotations

import json
import sys

import pytest

from flux_loop.agent import AgentSpec, _Live, run_turn, usage
from flux_loop.agent_usage import from_output


def _stream(kind, **fields):
    return {"type": "stream_event", "event": {"type": kind, **fields}}


def _output(events):
    return "\n".join(json.dumps(event) for event in events)


def _partial():
    return [
        _stream("message_start", message={"id": "m1", "usage": {"input_tokens": 10, "cache_read_input_tokens": 100,
                                                                 "cache_creation_input_tokens": 5, "output_tokens": 1}}),
        {"type": "assistant", "message": {"id": "m1", "usage": {"input_tokens": 10, "output_tokens": 5}}},
        {"type": "assistant", "message": {"id": "m1", "usage": {"input_tokens": 10, "output_tokens": 5}}},
        _stream("message_delta", usage={"output_tokens": 10, "input_tokens": 12, "cache_creation_input_tokens": 6}),
        _stream("message_stop"),
        _stream("message_start", message={"id": "m2", "usage": {"input_tokens": 30, "cache_read_input_tokens": 20,
                                                                 "output_tokens": 0}}),
        _stream("message_delta", usage={"output_tokens": 4}),
    ]


def test_claude_counts_unique_messages_and_cumulative_stream_updates_without_result():
    text = _output(_partial())
    assert usage("claude", text) == {"tokens_in": 168, "tokens_out": 14, "tokens_cached": 120}
    assert from_output("claude", text).record(finished=False)["tokens_complete"] is False
    live = _Live("claude")
    for event in _partial():
        live.feed(json.dumps(event))
    assert live.fields()["tokens (reported)"] == "168 in, 14 out"


def test_claude_assistant_blocks_without_partial_stream_are_counted_once():
    events = [{"type": "assistant", "message": {"id": ident, "usage": {"input_tokens": 100, "output_tokens": out}}}
              for ident, out in (("m1", 20), ("m1", 30), ("m2", 10))]
    assert usage("claude", _output(events)) == {"tokens_in": 200, "tokens_out": 40}


def test_claude_final_summary_replaces_partial_counts_instead_of_adding_them():
    events = _partial() + [{"type": "result", "usage": {"input_tokens": 200, "output_tokens": 100,
                                                        "cache_read_input_tokens": 500}, "total_cost_usd": 0.25}]
    tracker = from_output("claude", _output(events))
    assert usage("claude", _output(events)) == {"tokens_in": 700, "tokens_out": 100, "tokens_cached": 500, "cost_usd": 0.25}
    assert tracker.record(finished=True)["tokens_complete"] is True


@pytest.mark.parametrize("result", [{"type": "result", "is_error": True},
    {"type": "result", "is_error": True, "usage": {"input_tokens": 0, "output_tokens": 0}, "total_cost_usd": 0}])
def test_crash_results_with_missing_or_zeroed_usage_keep_reported_messages(result):
    tracker = from_output("claude", _output(_partial() + [result]))
    assert tracker.values() == {"tokens_in": 168, "tokens_out": 14, "tokens_cached": 120}
    assert tracker.record(finished=False)["tokens_complete"] is False


def test_interleaved_message_streams_do_not_mix_their_usage():
    events = [_stream("message_start", message={"id": "main", "usage": {"input_tokens": 100}}),
              {**_stream("message_start", message={"id": "child", "usage": {"input_tokens": 200}}), "parent_tool_use_id": "tool"},
              {**_stream("message_delta", usage={"output_tokens": 20}), "parent_tool_use_id": "tool"},
              _stream("message_delta", usage={"output_tokens": 10}),
              {"type": "assistant", "parent_tool_use_id": "tool", "message": {"id": "child", "usage": {"output_tokens": 20}}}]
    assert usage("claude", _output(events)) == {"tokens_in": 300, "tokens_out": 30}


def test_bad_events_and_truncated_json_cannot_erase_valid_usage():
    bad = [None, [], {"type": "stream_event", "event": []}, {"type": "assistant", "message": []},
           {"type": "assistant", "message": {"id": "bad", "usage": {"input_tokens": -1, "output_tokens": True}}},
           {"type": "assistant", "message": {"id": "bad2", "usage": {"input_tokens": float("nan"), "output_tokens": "20"}}}]
    text = "debug line\n" + _output(_partial() + bad) + '\n{"type":"result"'
    assert usage("claude", text) == {"tokens_in": 168, "tokens_out": 14, "tokens_cached": 120}
    assert usage("text", text) == {}


@pytest.mark.parametrize("output,events,expected", [
    ("opencode", [{"type": "step_finish", "part": {"id": "step", "reason": "tool-calls", "tokens": {"input": 100, "output": 20}}},
                  {"type": "step_finish", "part": {"id": "step", "reason": "tool-calls", "tokens": {"input": 100, "output": 20}}},
                  {"type": "step_start"}], {"tokens_in": 100, "tokens_out": 20}),
    ("codex", [{"type": "turn.completed", "usage": {"input_tokens": 100, "output_tokens": 20}},
               {"type": "turn.started"}], {"tokens_in": 100, "tokens_out": 20}),
    ("codex", [{"type": "turn.started"}], {}),
])
def test_other_agents_keep_reported_steps_but_mark_an_interrupted_request_incomplete(output, events, expected):
    text = _output(events)
    assert usage(output, text) == expected
    assert from_output(output, text).record(finished=False)["tokens_complete"] is False


@pytest.mark.parametrize("output,event", [("opencode", {"type": "step_finish", "part": {"reason": "stop"}}),
                                         ("codex", {"type": "turn.completed", "usage": {}})])
def test_missing_usage_does_not_become_a_reported_zero(output, event):
    record = from_output(output, _output([event])).record(finished=True)
    assert record == {"tokens_complete": False}


@pytest.mark.parametrize("output,event", [
    ("opencode", {"type": "step_finish", "part": {"reason": "stop", "tokens": {"input": 0, "output": 0}}}),
    ("codex", {"type": "turn.completed", "usage": {"input_tokens": 0, "output_tokens": 0}}),
    ("claude", {"type": "result", "usage": {"input_tokens": 0, "output_tokens": 0}}),
])
def test_an_explicit_zero_is_known_usage(output, event):
    record = from_output(output, _output([event])).record(finished=True)
    assert record == {"tokens_in": 0, "tokens_out": 0, "tokens_complete": True}


def test_a_reported_claude_error_result_can_have_complete_usage():
    event = {"type": "result", "is_error": True, "usage": {"input_tokens": 100, "output_tokens": 10}}
    assert from_output("claude", _output([event])).record(finished=False)["tokens_complete"] is True


def test_a_result_that_reports_only_cost_does_not_lose_it_or_invent_tokens():
    event = {"type": "result", "total_cost_usd": 0.2}
    assert usage("claude", _output([event])) == {"cost_usd": 0.2}
    assert from_output("claude", _output([event])).record(finished=True) == {"cost_usd": 0.2, "tokens_complete": False}


def test_real_timeout_records_tokens_including_updates_during_shutdown(tmp_path, monkeypatch):
    from flux_llm import transcript
    from flux_web import insights
    from flux_web.usage import usage as web_usage

    events = _partial()
    script = tmp_path / "partial_agent.py"
    script.write_text("import json, signal, sys, time\n"
                      f"events = {events!r}\n"
                      "def stop(*_):\n"
                      "    print(json.dumps({'type':'stream_event','event':{'type':'message_delta','usage':{'output_tokens':9}}}), flush=True)\n"
                      "    sys.exit(0)\n"
                      "signal.signal(signal.SIGTERM, stop)\n"
                      "for event in events: print(json.dumps(event), flush=True)\n"
                      "time.sleep(60)\n")
    path = tmp_path / "turns.jsonl"
    monkeypatch.setattr(transcript, "_STATE", {"path": str(path)})
    monkeypatch.setenv("FLUX_PARTIALTEST_PRICE_IN", "1")
    monkeypatch.setenv("FLUX_PARTIALTEST_PRICE_OUT", "2")
    monkeypatch.setattr("flux_loop.agent._about", lambda *_: "test")
    spec = AgentSpec("partialtest", (sys.executable, str(script)), None, "claude", timeout_s=0.2)
    turn = run_turn(spec, spec.argv, {"prompt": "p"}, workdir=tmp_path)
    assert turn.rc == 124 and not turn.ok
    record = json.loads(path.read_text())
    assert record["tokens_in"] == 168 and record["tokens_out"] == 19 and record["tokens_cached"] == 120
    assert record["tokens_complete"] is False
    assert record["cost_usd"] == pytest.approx(0.000206)
    total = web_usage(str(path))["total"]
    assert total["tokens_in"] == 168 and total["tokens_out"] == 19 and total["partial"] == 1 and total["counted"] == 1
    rows = [("bob", "test", *row) for row in insights._rows(str(path))]
    end = record["ts"] + 1
    rates = insights.token_rate(rows, hours=1, now=end)
    assert sum(p["in_agent"] for p in rates) * 20 == pytest.approx(168)
    assert sum(p["out_agent"] for p in rates) * 20 == pytest.approx(19)
    endpoint, = insights.endpoints(rows, since=end - 3600)
    assert endpoint["failed"] == 0, "an agent time budget remains neutral for endpoint health"
