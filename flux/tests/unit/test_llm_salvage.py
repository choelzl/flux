"""D410: a finished think-only reply is salvaged, not discarded.

qwen habitually writes the entire answer inside the think channel and never exits
it; ollama then returns response="" with the whole trace under `thinking`. When
`done_reason` is "stop" (the model FINISHED), the trace goes to the caller's parse
gates; a "length" cut is genuinely unfinished and still raises."""

from __future__ import annotations

import json


def _proposer(monkeypatch, payload: dict):
    import urllib.request

    from flux_llm.ollama_native import NativeOllamaProposer

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps(payload).encode()

    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout: _Resp())
    return NativeOllamaProposer(model="stub")


def test_finished_think_only_reply_is_handed_to_the_parse_gates(monkeypatch):
    p = _proposer(monkeypatch, {"response": "", "done_reason": "stop",
                                "eval_count": 17920,
                                "thinking": "Let me think...\nDESIGN: {\"x\": 1}"})
    out = p.propose("design something")
    assert "DESIGN:" in out
    assert p.last_metadata["salvaged_from_thinking"] is True


def test_a_length_cut_still_raises_it_is_genuinely_unfinished(monkeypatch):
    import pytest

    p = _proposer(monkeypatch, {"response": "", "done_reason": "length",
                                "eval_count": 24000, "thinking": "Let me think"})
    with pytest.raises(RuntimeError, match="empty response"):
        p.propose("design something")


def test_a_normal_reply_is_untouched(monkeypatch):
    p = _proposer(monkeypatch, {"response": "the answer", "done_reason": "stop",
                                "eval_count": 10, "thinking": "brief"})
    assert p.propose("q") == "the answer"
    assert "salvaged_from_thinking" not in p.last_metadata
