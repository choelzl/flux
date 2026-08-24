"""D469: a structured proposer over the OpenAI chat protocol, and which backend a demo gets.

What matters here is the WIRE: thinking is a per-request field and nothing else (a server's own
setting is left alone), a schema goes out only when the model is not asked to think, the answer
is read from `content` with D410's salvage from the think channel, and the switch that moves a
demo off the machine is the same `FLUX_LLM_REMOTE` as before, with the same fallback to local.
"""

from __future__ import annotations

import json
import urllib.error

import pytest


def _wire(monkeypatch, reply: dict, *, status: int | None = None):
    """Patch urlopen; return the list of decoded request bodies (with the headers)."""
    import urllib.request

    seen: list[dict] = []

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps(reply).encode()

    def fake(req, timeout):
        if req.full_url.endswith("/api/show"):          # the window probe: unknown here
            import io
            raise urllib.error.HTTPError(req.full_url, 404, "no", {}, io.BytesIO(b""))
        seen.append({"body": json.loads(req.data), "headers": dict(req.header_items()),
                     "url": req.full_url, "timeout": timeout})
        if status is not None:
            import io
            raise urllib.error.HTTPError(req.full_url, status, "nope", {},
                                         io.BytesIO(json.dumps(reply).encode()))
        return _Resp()

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    return seen


def _ok(content: str = "", reasoning: str | None = None, finish: str = "stop") -> dict:
    msg: dict = {"role": "assistant", "content": content}
    if reasoning is not None:
        msg["reasoning"] = reasoning
    return {"choices": [{"finish_reason": finish, "message": msg}],
            "usage": {"prompt_tokens": 31, "completion_tokens": 22}}


@pytest.fixture
def hosted(monkeypatch):
    import flux_llm.openrouter as remote

    monkeypatch.setenv("FLUX_REMOTE_API_KEY", "k-test")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("FLUX_REMOTE_BASE_URL", "https://localai.example")
    monkeypatch.setenv("FLUX_REMOTE_MODEL", "qwen-apex")
    monkeypatch.setenv("FLUX_LLM_STREAM", "0")        # these tests fake ONE message; streaming has its own
    monkeypatch.setattr(remote, "_ANNOUNCED", True)  # the announcement has its own test
    from flux_llm.ollama_native import set_think_override

    set_think_override(None)
    yield
    set_think_override(None)


SCHEMA = {"type": "object", "properties": {"hex": {"type": "string"}}, "required": ["hex"]}


def test_thinking_off_is_a_request_field_and_the_schema_rides_along(monkeypatch, hosted):
    from flux_llm import OpenAIChatProposer

    seen = _wire(monkeypatch, _ok('{"hex": "3C00"}'))
    p = OpenAIChatProposer(num_predict=400, timeout_s=7)
    assert p.propose("bits of 1.0", SCHEMA) == '{"hex": "3C00"}'
    body = seen[0]["body"]
    assert body["reasoning_effort"] == "none"
    assert body["response_format"] == {"type": "json_schema",
                                       "json_schema": {"name": "reply", "schema": SCHEMA}}
    assert body["max_tokens"] == 400 and body["model"] == "qwen-apex"
    assert body["messages"] == [{"role": "user", "content": "bits of 1.0"}]
    assert seen[0]["headers"]["Authorization"] == "Bearer k-test"
    assert seen[0]["url"] == "https://localai.example/v1/chat/completions"
    assert seen[0]["timeout"] == 7
    assert p.last_metadata == {"input_tokens": 31, "output_tokens": 22, "done_reason": "stop",
                               "model": "qwen-apex", "schema": "applied", "max_tokens": 400,
                               "retried": None}


def test_thinking_on_says_nothing_about_reasoning_and_drops_the_schema(monkeypatch, hosted):
    """Measured: a grammar plus an open think block puts the JSON in `reasoning` and lets
    no reasoning happen. So a thinking request leaves the schema out -- and the server's
    own reasoning setting alone, for every other user of it."""
    from flux_llm import OpenAIChatProposer

    seen = _wire(monkeypatch, _ok('{"hex": "3C00"}', reasoning="let me see..."))
    p = OpenAIChatProposer(think=True)
    assert p.propose("bits of 1.0", SCHEMA) == '{"hex": "3C00"}'
    body = seen[0]["body"]
    assert "reasoning_effort" not in body and "response_format" not in body
    assert p.last_metadata["schema"] == "dropped: thinking on"


def test_the_tui_think_toggle_wins_per_call(monkeypatch, hosted):
    from flux_llm import OpenAIChatProposer, set_think_override

    seen = _wire(monkeypatch, _ok("x"))
    p = OpenAIChatProposer(think=False)
    set_think_override(True)
    p.propose("a", SCHEMA)
    set_think_override(False)
    p.propose("b", SCHEMA)
    assert "reasoning_effort" not in seen[0]["body"] and "response_format" not in seen[0]["body"]
    assert seen[1]["body"]["reasoning_effort"] == "none" and "response_format" in seen[1]["body"]


def test_no_output_cap_means_no_max_tokens(monkeypatch, hosted):
    from flux_llm import OpenAIChatProposer

    seen = _wire(monkeypatch, _ok("x"))
    OpenAIChatProposer(num_predict=None).propose("a")
    assert "max_tokens" not in seen[0]["body"]


def test_a_finished_think_only_reply_is_salvaged(monkeypatch, hosted):
    """D410 on this protocol: the model FINISHED (stop) with the answer in the think channel."""
    from flux_llm import OpenAIChatProposer

    _wire(monkeypatch, _ok("", reasoning='thinking... {"hex": "3C00"}'))
    p = OpenAIChatProposer(think=True)
    assert p.propose("a") == 'thinking... {"hex": "3C00"}'
    assert p.last_metadata["salvaged_from_thinking"] is True
    # D503: prose in the think channel and nothing answered -- measured four times in 40
    # hours, never parsable -- is asked again with thinking off instead of handed over
    import urllib.request

    calls: list[dict] = []
    answers = iter([_ok("", reasoning="The user wants a fix. Let me think about the table. No, wait."),
                    _ok('{"hex": "3C00"}')])

    class _Resp:
        def __init__(self, doc):
            self.doc = doc

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps(self.doc).encode()

    def fake(req, timeout):
        if req.full_url.endswith("/api/show"):
            import io
            raise urllib.error.HTTPError(req.full_url, 404, "no", {}, io.BytesIO(b""))
        calls.append(json.loads(req.data))
        return _Resp(next(answers))

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    p = OpenAIChatProposer(think=True)
    assert p.propose("a") == '{"hex": "3C00"}'
    assert len(calls) == 2 and calls[1]["reasoning_effort"] == "none" and "answered nothing" in p.last_metadata["runaway"]


def test_a_reply_cut_off_while_thinking_is_asked_again_without_thinking(monkeypatch, hosted):
    """D483: three live passes ended on a runaway -- 47k tokens of reasoning, no answer. The
    same prompt goes once more with reasoning off; the second answer is the reply."""
    import urllib.request

    from flux_llm import OpenAIChatProposer

    seen: list[dict] = []
    replies = iter([_ok("", reasoning="Here's a thinking process", finish="length"),
                    _ok('{"hex": "3c00"}')])

    class _Resp:
        def __init__(self, reply):
            self.reply = reply

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps(self.reply).encode()

    def fake(req, timeout):
        if req.full_url.endswith("/api/show"):
            import io
            raise urllib.error.HTTPError(req.full_url, 404, "no", {}, io.BytesIO(b""))
        seen.append(json.loads(req.data))
        return _Resp(next(replies))

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    p = OpenAIChatProposer(think=True)
    assert p.propose("a") == '{"hex": "3c00"}'
    assert len(seen) == 2 and "reasoning_effort" not in seen[0] and seen[1]["reasoning_effort"] == "none"
    assert p.last_metadata["runaway"].startswith("empty response")
    # cut off for another reason, or already without thinking: the error stands
    _wire(monkeypatch, _ok("", reasoning="", finish="length"))
    with pytest.raises(RuntimeError, match="finish_reason='length'"):
        OpenAIChatProposer(think=False).propose("a")


def test_the_servers_own_error_message_reaches_the_operator(monkeypatch, hosted):
    from flux_llm import OpenAIChatProposer

    _wire(monkeypatch, {"error": {"code": 404, "message": 'model "nope" not found'}}, status=404)
    with pytest.raises(RuntimeError, match='answered 404: model "nope" not found'):
        OpenAIChatProposer(model="nope").propose("a")


def test_no_key_is_refused_at_construction(monkeypatch):
    monkeypatch.delenv("FLUX_REMOTE_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    from flux_llm import OpenAIChatProposer

    with pytest.raises(RuntimeError, match="FLUX_REMOTE_API_KEY"):
        OpenAIChatProposer()


@pytest.mark.parametrize("given,expected", [
    ("https://localai.example", "https://localai.example/v1"),
    ("https://localai.example/", "https://localai.example/v1"),
    ("https://localai.example/v1", "https://localai.example/v1"),
    ("https://openrouter.ai/api/v1", "https://openrouter.ai/api/v1"),
])
def test_the_base_url_always_names_the_v1_root(monkeypatch, given, expected):
    from flux_llm import remote_base_url

    monkeypatch.setenv("FLUX_REMOTE_BASE_URL", given)
    assert remote_base_url() == expected


def test_going_hosted_is_announced_once_on_the_first_prompt(monkeypatch, hosted):
    import flux_llm.openrouter as remote
    from flux_llm import OpenAIChatProposer

    monkeypatch.setattr(remote, "_ANNOUNCED", False)
    _wire(monkeypatch, _ok("x"))
    said: list[str] = []
    p = OpenAIChatProposer(announce=said.append)
    assert not said, "nothing has been sent yet"
    p.propose("a")
    p.propose("b")
    assert len(said) == 1 and "SENDING PROMPTS OFF THIS MACHINE" in said[0]
    assert "https://localai.example/v1" in said[0] and "qwen-apex" in said[0]


# -- which backend a demo gets ------------------------------------------------------------


def test_the_factory_stays_local_without_the_switch(monkeypatch):
    from flux_llm import NativeOllamaProposer, structured_proposer

    monkeypatch.delenv("FLUX_LLM_REMOTE", raising=False)
    monkeypatch.setenv("FLUX_REMOTE_API_KEY", "k-test")
    p = structured_proposer("qwen3:4b", num_ctx=8192, num_predict=9, think=True)
    assert isinstance(p, NativeOllamaProposer)
    assert (p.model, p.num_ctx, p.num_predict, p.think) == ("qwen3:4b", 8192, 9, True)


def test_the_factory_goes_hosted_on_the_switch_and_says_what_it_cannot_send(monkeypatch, hosted):
    from flux_llm import OpenAIChatProposer, structured_proposer

    monkeypatch.setenv("FLUX_LLM_REMOTE", "1")
    said: list[str] = []
    p = structured_proposer(num_ctx=49152, num_predict=40000, say=said.append)
    assert isinstance(p, OpenAIChatProposer)
    assert p.model == "qwen-apex" and p.num_predict == 40000 and p.think is False
    assert said and "num_ctx 49152 not sent" in said[0]
    assert structured_proposer("their-tag", say=said.append).model == "their-tag"


def test_the_factory_falls_back_to_local_without_a_key_and_says_so(monkeypatch):
    from flux_llm import NativeOllamaProposer, structured_proposer

    monkeypatch.setenv("FLUX_LLM_REMOTE", "1")
    monkeypatch.delenv("FLUX_REMOTE_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    said: list[str] = []
    assert isinstance(structured_proposer(say=said.append), NativeOllamaProposer)
    assert said and "using the local one" in said[0]


def test_remote_proposer_keeps_its_wire_and_wraps_failures(monkeypatch, hosted):
    """The `str -> str` face (D337): no schema, no cap, nothing about reasoning; a refused
    call is `RemoteProposerUnavailable` so `auto_proposer`'s callers can fall back."""
    from flux_llm import RemoteProposerUnavailable, remote_proposer

    seen = _wire(monkeypatch, _ok("fine"))
    propose = remote_proposer(timeout_s=5)
    assert propose("p") == "fine"
    body = seen[0]["body"]
    assert set(body) == {"model", "messages"}
    _wire(monkeypatch, {"error": "rate limited"}, status=429)
    with pytest.raises(RemoteProposerUnavailable, match="429"):
        propose("p")


# -- the window, and one retry ---------------------------------------------------------------


def _server(monkeypatch, *, context: int | None, answers: list):
    """A fake server: `/api/show` states the window (or 404s), `/chat/completions` pops
    the next answer -- a reply dict, or an exception to raise. Returns the bodies sent."""
    import io
    import urllib.request

    sent: list[dict] = []

    class _Resp:
        def __init__(self, doc):
            self.doc = doc

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps(self.doc).encode()

    def fake(req, timeout):
        if req.full_url.endswith("/api/show"):
            if context is None:
                raise urllib.error.HTTPError(req.full_url, 404, "no", {}, io.BytesIO(b""))
            return _Resp({"model_info": {"general.context_length": context}})
        sent.append(json.loads(req.data))
        nxt = answers.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return _Resp(nxt)

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    return sent


def _http(code: int, body: dict):
    import io

    return urllib.error.HTTPError("u", code, "nope", {}, io.BytesIO(json.dumps(body).encode()))


def test_max_tokens_is_capped_to_the_servers_window(monkeypatch, hosted):
    """D473: the live run asked for 80,000 on a 58,112 window and lost two parts to
    "Context size has been exceeded". The prompt is budgeted at two chars per token."""
    from flux_llm import OpenAIChatProposer

    sent = _server(monkeypatch, context=58112, answers=[_ok("x"), _ok("y"), _ok("z")])
    p = OpenAIChatProposer(num_predict=80000)
    p.propose("p" * 20000)                      # ~10k tokens of prompt by the estimate
    assert sent[0]["max_tokens"] == 58112 - int(20000 / 2 * 1.1) - 256     # two chars a token until calibrated
    p.propose("q" * 200000)                     # a prompt that fills the window
    assert sent[1]["max_tokens"] == 1024
    assert p.context_length() == 58112 and p.last_metadata["max_tokens"] == 1024
    OpenAIChatProposer(num_predict=None).propose("r")
    assert "max_tokens" not in sent[2]


def test_an_unknown_window_leaves_the_budget_alone(monkeypatch, hosted):
    from flux_llm import OpenAIChatProposer

    sent = _server(monkeypatch, context=None, answers=[_ok("x")])
    p = OpenAIChatProposer(num_predict=80000)
    p.propose("p" * 20000)
    assert sent[0]["max_tokens"] == 80000 and p.context_length() is None


def test_a_5xx_is_retried_once_and_a_context_overflow_halves_the_budget(monkeypatch, hosted):
    from flux_llm import OpenAIChatProposer

    monkeypatch.setattr(OpenAIChatProposer, "RETRY_AFTER_S", 0.0)
    sent = _server(monkeypatch, context=None, answers=[
        _http(500, {"error": {"message": "rpc error: Context size has been exceeded."}}), _ok("fine")])
    p = OpenAIChatProposer(num_predict=40000)
    assert p.propose("p") == "fine"
    assert [b["max_tokens"] for b in sent] == [40000, 20000]
    assert p.last_metadata["retried"] == "500: rpc error: Context size has been exceeded."


def test_a_second_failure_and_a_4xx_are_not_retried(monkeypatch, hosted):
    from flux_llm import OpenAIChatProposer

    monkeypatch.setattr(OpenAIChatProposer, "RETRY_AFTER_S", 0.0)
    sent = _server(monkeypatch, context=None, answers=[
        _http(503, {"error": "busy"}), _http(503, {"error": "still busy"})])
    with pytest.raises(RuntimeError, match="answered 503: still busy"):
        OpenAIChatProposer().propose("p")
    assert len(sent) == 2
    sent = _server(monkeypatch, context=None, answers=[_http(400, {"error": "bad schema"})])
    with pytest.raises(RuntimeError, match="answered 400: bad schema"):
        OpenAIChatProposer().propose("p")
    assert len(sent) == 1


def test_the_reply_streams_and_the_thinking_shows_while_it_thinks(hosted, monkeypatch):
    """D493 (Cedric: "do we have access to live thinking? so that we can display it while
    the LLM thinks in the respective task"): the reply is read as SSE chunks -- LocalAI
    sends `delta.reasoning` token by token, then `delta.content`, the finish reason and a
    usage chunk -- and the running phase's row is updated with the tail of both through
    `flux_profile.progress`. The assembled payload is the one the non-streaming path returns."""
    import urllib.request

    import flux_profile
    from flux_llm.openai_compat import OpenAIChatProposer

    monkeypatch.setenv("FLUX_LLM_STREAM", "1")
    chunks = [{"choices": [], "usage": None},                  # a chunk without a choice, first (D503)
              {"choices": [{"index": 0, "finish_reason": None, "delta": {"role": "assistant", "content": None}}]}]
    chunks += [{"choices": [{"index": 0, "finish_reason": None, "delta": {"content": None, "reasoning": w}}]}
               for w in ("Let", " me", " think", " about", " 17*23", ".")]
    chunks += [{"choices": [{"index": 0, "finish_reason": None, "delta": {"content": w}}]}
               for w in ('{"answer"', ": 391}")]
    chunks += [{"choices": [{"index": 0, "finish_reason": "stop", "delta": {"content": None}}]},
               {"choices": [], "usage": {"prompt_tokens": 25, "completion_tokens": 9, "total_tokens": 34}}]
    sse = [f"data: {json.dumps(c)}\n".encode() for c in chunks] + [b"\n", b"data: [DONE]\n"]
    seen: list[dict] = []

    class _Stream:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def __iter__(self):
            return iter(sse)

    def fake(req, timeout):
        if req.full_url.endswith("/api/show"):
            import io
            raise urllib.error.HTTPError(req.full_url, 404, "no", {}, io.BytesIO(b""))
        seen.append(json.loads(req.data))
        return _Stream()

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    updates: list[dict] = []

    class Listener:
        def phase_start(self, name, why, params):
            return 7

        def phase_end(self, token, name, secs, failed, output):
            updates.append({"END": output})

        def phase_update(self, token, name, output):
            updates.append(output)

    flux_profile.set_listener(Listener())
    try:
        p = OpenAIChatProposer(think=True)
        p.LIVE_EVERY_S = 0.0                             # every chunk, for the test
        got = p.propose("What is 17*23?")
    finally:
        flux_profile.clear_listener()
    assert got == '{"answer": 391}'
    assert seen[0]["stream"] is True and seen[0]["stream_options"] == {"include_usage": True}
    live = [u for u in updates if "END" not in u]
    assert live and live[0]["thinking (live tail)"] == "Let" and live[-1]["reply (live tail)"] == '{"answer": 391}'
    assert live[-1]["thinking (live tail)"] == "Let me think about 17*23." and "tok/s" in live[-1]["streamed"]
    end = updates[-1]["END"]
    assert end["thinking"] == "Let me think about 17*23." and "9 out" in end["tokens"]
    assert p.last_metadata["output_tokens"] == 9 and p.last_metadata["done_reason"] == "stop"


def test_a_thinking_loop_is_cut_short_and_retried_without_thinking(hosted, monkeypatch):
    """D494 (Cedric: "seems like we have thinking loops"): the live thinking showed four lines
    repeating for minutes. The stream is watched for a cycle -- 200 identical characters four
    times in the last 8,000 -- the request is aborted there, and the runaway path (D488)
    sends the prompt again with thinking off."""
    import urllib.request

    from flux_llm.openai_compat import OpenAIChatProposer, looping

    assert looping("deliberation " * 3) is None
    cycle = "So `v` should be `x * 2^10`, and `idx = (v >> 10) + 32`? No.\n`v = x * 2^10`. `v` is `x` in Q10.\n"
    assert looping("some real thinking first. " + cycle * 12) == (len(cycle), 12)
    assert looping("a" * 8000) is not None                       # a one-character cycle too
    monkeypatch.setenv("FLUX_LLM_STREAM", "1")
    calls: list[dict] = []

    def chunk(**delta):
        return f"data: {json.dumps({'choices': [{'index': 0, 'finish_reason': None, 'delta': delta}]})}\n".encode()

    class _Stream:
        def __init__(self, lines):
            self.lines = lines

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def __iter__(self):
            return iter(self.lines)

    def fake(req, timeout):
        if req.full_url.endswith("/api/show"):
            import io
            raise urllib.error.HTTPError(req.full_url, 404, "no", {}, io.BytesIO(b""))
        body = json.loads(req.data)
        calls.append(body)
        if body.get("reasoning_effort") == "none":              # the retry: a plain answer
            return _Stream([chunk(content='{"ok": 1}'),
                            f"data: {json.dumps({'choices': [{'index': 0, 'finish_reason': 'stop', 'delta': {}}]})}\n".encode(),
                            f"data: {json.dumps({'choices': [], 'usage': {'prompt_tokens': 9, 'completion_tokens': 4}})}\n".encode(),
                            b"data: [DONE]\n"])
        return _Stream([chunk(reasoning="Let me see. ")] + [chunk(reasoning=cycle)] * 400 + [chunk(content="never")])

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    p = OpenAIChatProposer(think=True)
    p.LIVE_EVERY_S = 0.0
    assert p.propose("What?") == '{"ok": 1}'
    assert len(calls) == 2 and "reasoning_effort" not in calls[0] and calls[1]["reasoning_effort"] == "none"
    assert p.last_metadata["runaway"].startswith("thinking looped: a ") and "the request was aborted" in p.last_metadata["runaway"]
