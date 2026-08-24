"""D469/D508: THE proposer over the OpenAI chat protocol, and where its requests go.

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
    import flux_llm.openai_compat as remote

    monkeypatch.setenv("FLUX_LLM_REMOTE", "1")
    monkeypatch.setenv("FLUX_REMOTE_API_KEY", "k-test")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("FLUX_REMOTE_BASE_URL", "https://localai.example")
    monkeypatch.setenv("FLUX_REMOTE_MODEL", "qwen-apex")
    monkeypatch.setenv("FLUX_LLM_STREAM", "0")        # these tests fake ONE message; streaming has its own
    monkeypatch.setattr(remote, "_ANNOUNCED", True)  # the announcement has its own test
    from flux_llm import set_think_override

    set_think_override(None)
    yield
    set_think_override(None)


SCHEMA = {"type": "object", "properties": {"hex": {"type": "string"}}, "required": ["hex"]}


def test_thinking_off_is_a_request_field_and_the_schema_rides_along(monkeypatch, hosted):
    from flux_llm import OpenAIChatProposer

    seen = _wire(monkeypatch, _ok('{"hex": "3C00"}'))
    p = OpenAIChatProposer(num_predict=400, timeout_s=7)
    r = p.propose("bits of 1.0", schema=SCHEMA)
    assert r.text == '{"hex": "3C00"}'
    body = seen[0]["body"]
    assert body["reasoning_effort"] == "none"
    assert body["response_format"] == {"type": "json_schema",
                                       "json_schema": {"name": "reply", "schema": SCHEMA}}
    assert body["max_tokens"] == 400 and body["model"] == "qwen-apex"
    assert body["messages"] == [{"role": "user", "content": "bits of 1.0"}]
    assert seen[0]["headers"]["Authorization"] == "Bearer k-test"
    assert seen[0]["url"] == "https://localai.example/v1/chat/completions"
    assert seen[0]["timeout"] == 7
    assert r.usage == {"input_tokens": 31, "output_tokens": 22}
    assert r.notes == {"input_tokens": 31, "output_tokens": 22, "finish": "stop", "model": "qwen-apex",
                       "schema": "applied", "max_tokens": 400, "retried": None}


def test_thinking_on_says_nothing_about_reasoning_and_drops_the_schema(monkeypatch, hosted):
    """Measured: a grammar plus an open think block puts the JSON in `reasoning` and lets
    no reasoning happen. So a thinking request leaves the schema out -- and the server's
    own reasoning setting alone, for every other user of it."""
    from flux_llm import OpenAIChatProposer

    seen = _wire(monkeypatch, _ok('{"hex": "3C00"}', reasoning="let me see..."))
    p = OpenAIChatProposer(think=True)
    r = p.propose("bits of 1.0", schema=SCHEMA)
    assert r.text == '{"hex": "3C00"}'
    body = seen[0]["body"]
    assert "reasoning_effort" not in body and "response_format" not in body
    assert r.notes["schema"] == "dropped: thinking on"


def test_the_tui_think_toggle_wins_per_call(monkeypatch, hosted):
    from flux_llm import OpenAIChatProposer, set_think_override

    seen = _wire(monkeypatch, _ok("x"))
    p = OpenAIChatProposer(think=False)
    set_think_override(True)
    p.propose("a", schema=SCHEMA)
    set_think_override(False)
    p.propose("b", schema=SCHEMA)
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
    r = p.propose("a")
    assert r.text == 'thinking... {"hex": "3C00"}'
    assert r.notes["salvaged_from_thinking"] is True
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
    r = p.propose("a")
    assert r.text == '{"hex": "3C00"}'
    assert len(calls) == 2 and calls[1]["reasoning_effort"] == "none" and "answered nothing" in r.notes["runaway"]


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
    r = p.propose("a")
    assert r.text == '{"hex": "3c00"}'
    assert len(seen) == 2 and "reasoning_effort" not in seen[0] and seen[1]["reasoning_effort"] == "none"
    assert r.notes["runaway"].startswith("empty response")
    # cut off for another reason, or already without thinking: the error stands
    _wire(monkeypatch, _ok("", reasoning="", finish="length"))
    with pytest.raises(RuntimeError, match="finish_reason='length'"):
        OpenAIChatProposer(think=False).propose("a")


def test_the_servers_own_error_message_reaches_the_operator(monkeypatch, hosted):
    from flux_llm import OpenAIChatProposer

    _wire(monkeypatch, {"error": {"code": 404, "message": 'model "nope" not found'}}, status=404)
    with pytest.raises(RuntimeError, match='answered 404: model "nope" not found'):
        OpenAIChatProposer(model="nope").propose("a")


def test_the_switch_without_a_key_is_refused_at_construction(monkeypatch):
    """Asked for the hosted server on purpose and given no key: refused, not quietly
    downgraded to the local model (D508 folds D337's policy into the one proposer)."""
    monkeypatch.setenv("FLUX_LLM_REMOTE", "1")
    monkeypatch.delenv("FLUX_REMOTE_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    from flux_llm import OpenAIChatProposer

    with pytest.raises(RuntimeError, match="FLUX_REMOTE_API_KEY"):
        OpenAIChatProposer()


def test_without_the_switch_the_proposer_is_local_and_sends_no_key(monkeypatch):
    """`FLUX_LLM_REMOTE` is the only thing that sends anything off this machine: a key in
    the environment does not turn it on, and the local Ollama's `/v1` needs none."""
    from flux_llm import OpenAIChatProposer, default_local_model, remote_enabled

    monkeypatch.delenv("FLUX_LLM_REMOTE", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-whatever")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://box:11434")
    monkeypatch.setenv("FLUX_LLM_STREAM", "0")
    assert remote_enabled() is False
    seen = _wire(monkeypatch, _ok("local answer"))
    p = OpenAIChatProposer(num_predict=5)
    assert not p.hosted and p.base_url == "http://box:11434/v1" and p.model == default_local_model()
    assert p.propose("q").text == "local answer"
    assert seen[0]["url"] == "http://box:11434/v1/chat/completions"
    assert "Authorization" not in seen[0]["headers"]


@pytest.mark.parametrize("value,expected",
                         [("1", True), ("true", True), ("YES", True), ("on", True),
                          ("0", False), ("false", False), ("", False), ("maybe", False)],
                         ids=lambda v: str(v))
def test_the_switch_is_read_strictly(monkeypatch, value, expected):
    from flux_llm import remote_enabled

    monkeypatch.setenv("FLUX_LLM_REMOTE", value)
    assert remote_enabled() is expected


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
    import flux_llm.openai_compat as remote
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
    r = p.propose("q" * 200000)                     # a prompt that fills the window
    assert sent[1]["max_tokens"] == 1024
    assert p.context_length() == 58112 and r.notes["max_tokens"] == 1024
    OpenAIChatProposer(num_predict=None).propose("r")
    assert "max_tokens" not in sent[2]


def test_an_unknown_window_leaves_the_budget_alone(monkeypatch, hosted):
    from flux_llm import OpenAIChatProposer

    sent = _server(monkeypatch, context=None, answers=[_ok("x")])
    p = OpenAIChatProposer(num_predict=80000)
    p.propose("p" * 20000)
    assert sent[0]["max_tokens"] == 80000 and p.context_length() is None


def test_a_crowded_tool_turn_compacts_its_older_rounds(monkeypatch, hosted):
    """D549: past `compact_share` of the window, the rounds before the last lose their thinking
    and their results are cut to a head with a note; the last round stays whole, and the
    reply says how much went."""
    from flux_llm import OpenAIChatProposer, Tool, ToolBudget
    from flux_llm.compact import MARK

    tools = [Tool("compute", "run", {"type": "object"}, lambda a: "R" * 3000)]
    calling = {"choices": [{"finish_reason": "tool_calls", "message": {"role": "assistant", "content": "",
                            "reasoning": "T" * 500, "tool_calls": [_tool_call("compute", {"code": "x"})]}}],
               "usage": {}}
    sent = _server(monkeypatch, context=4000, answers=[calling, calling, _ok("done")])   # 4000 tokens, 2 chars each
    r = OpenAIChatProposer(num_predict=1000, think=False).propose(
        "go", tools=tools, budget=ToolBudget(hops=2, compact_share=0.6))
    assert r.text == "done" and r.notes["compacted"] > 3000
    msgs = sent[2]["messages"]                                     # the answering round's conversation
    tool_msgs = [m for m in msgs if m["role"] == "tool"]
    assert MARK in tool_msgs[0]["content"] and len(tool_msgs[0]["content"]) < 400   # the older round: a head
    assert tool_msgs[1]["content"] == "R" * 3000                                     # the last round: whole
    assistants = [m for m in msgs if m["role"] == "assistant"]
    assert "reasoning_content" not in assistants[0] and assistants[1]["reasoning_content"] == "T" * 500
    assert "compacted" not in sent[1] and all(m["content"] == "R" * 3000 for m in sent[1]["messages"] if m["role"] == "tool")


def test_a_result_is_digested_to_its_telling_lines_and_a_model_note_can_replace_the_rounds(monkeypatch, hosted):
    """D550: a digest keeps a result's head, its lines with a number or a verdict, and its
    tail (the answer and the error sit at the end); with `compact: llm` the model writes what
    the older rounds established and that note replaces them at the end of the prompt."""
    from flux_llm import OpenAIChatProposer, Tool, ToolBudget
    from flux_llm.compact import NOTE, digest_result

    text = "table of 40 rows\n" + "\n".join("prose about nothing much" for _ in range(40)) + "\nscore 512 over\nerror: x"
    d = digest_result(text, 200)
    assert d.startswith("table of 40 rows") and "score 512 over" in d and d.count("prose") <= 3 and "lines kept" in d
    tools = [Tool("compute", "run", {"type": "object"}, lambda a: "R" * 3000)]
    calling = {"choices": [{"finish_reason": "tool_calls", "message": {"role": "assistant", "content": "",
                            "tool_calls": [_tool_call("compute", {"code": "x"})]}}], "usage": {}}
    sent = _server(monkeypatch, context=4000, answers=[calling, calling, _ok("round 1 computed R x 3000"), _ok("done")])
    r = OpenAIChatProposer(num_predict=1000, think=False).propose(
        "go", tools=tools, budget=ToolBudget(hops=2, compact_share=0.6, compact="llm"))
    assert r.text == "done" and r.notes["condensed"] == 1 and r.notes["compacted"] > 3000
    assert "tools" not in sent[2] and "ESTABLISHED" in sent[2]["messages"][0]["content"]     # the condensing call
    msgs = sent[3]["messages"]
    assert msgs[0]["content"] == "go" + NOTE + "round 1 computed R x 3000"
    assert [m["role"] for m in msgs] == ["user", "assistant", "tool"] and msgs[2]["content"] == "R" * 3000


def test_a_tool_round_may_write_a_share_of_the_window(monkeypatch, hosted):
    """D544: a round that may call tools is capped at `hop_share` of the model's context
    window (D506 fixed it at 24,000 tokens; Cedric: "like 50% of tokens or so"); the answering
    round keeps the turn's cap, and a window the server does not state leaves every round
    with the turn's cap."""
    from flux_llm import OpenAIChatProposer, Tool, ToolBudget

    tools = [Tool("compute", "run", {"type": "object"}, lambda a: "1")]
    calling = {"choices": [{"finish_reason": "tool_calls", "message": {"role": "assistant", "content": "",
                            "tool_calls": [_tool_call("compute", {"code": "x"})]}}], "usage": {}}
    sent = _server(monkeypatch, context=60000, answers=[calling, _ok("done")])
    r = OpenAIChatProposer(num_predict=80000, think=False).propose(
        "go", tools=tools, budget=ToolBudget(hops=1, hop_share=0.5))      # one tool round, then the answer
    assert r.text == "done" and len(sent) == 2 and "tools" not in sent[1]
    assert sent[0]["max_tokens"] == 30000                      # the tool round: half the window
    assert 30000 < sent[1]["max_tokens"] <= 60000              # the answer: the turn's cap, in the room left
    sent = _server(monkeypatch, context=None, answers=[calling, _ok("done")])
    OpenAIChatProposer(num_predict=80000, think=False).propose(
        "go", tools=tools, budget=ToolBudget(hops=1, hop_share=0.5))
    assert [b["max_tokens"] for b in sent] == [80000, 80000]   # no window stated: the turn's cap


def test_a_5xx_is_retried_once_and_a_context_overflow_halves_the_budget(monkeypatch, hosted):
    from flux_llm import OpenAIChatProposer

    monkeypatch.setattr(OpenAIChatProposer, "RETRY_AFTER_S", 0.0)
    sent = _server(monkeypatch, context=None, answers=[
        _http(500, {"error": {"message": "rpc error: Context size has been exceeded."}}), _ok("fine")])
    p = OpenAIChatProposer(num_predict=40000)
    r = p.propose("p")
    assert r.text == "fine"
    assert [b["max_tokens"] for b in sent] == [40000, 20000]
    assert r.notes["retried"] == "500: rpc error: Context size has been exceeded."


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
        got = r = p.propose("What is 17*23?")
    finally:
        flux_profile.clear_listener()
    assert got.text == '{"answer": 391}'
    assert seen[0]["stream"] is True and seen[0]["stream_options"] == {"include_usage": True}
    live = [u for u in updates if "END" not in u]
    assert live and live[0]["thinking (live tail)"] == "Let" and live[-1]["reply (live tail)"] == '{"answer": 391}'
    assert live[-1]["thinking (live tail)"] == "Let me think about 17*23." and "tok/s" in live[-1]["streamed"]
    end = updates[-1]["END"]
    assert end["thinking"] == "Let me think about 17*23." and "9 out" in end["tokens"]
    assert r.usage["output_tokens"] == 9 and r.notes["finish"] == "stop"


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
    r = p.propose("What?")
    assert r.text == '{"ok": 1}'
    assert len(calls) == 2 and "reasoning_effort" not in calls[0] and calls[1]["reasoning_effort"] == "none"
    assert r.notes["runaway"].startswith("thinking looped: a ") and "the request was aborted" in r.notes["runaway"]


# ----------------------------------------------------------------------- tools (D505)
def _tool_call(name: str, args: dict, cid: str = "c1") -> dict:
    return {"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def _wire_script(monkeypatch, replies: list[dict]):
    """Patch urlopen with a SCRIPT of replies, one per request; return the request bodies."""
    import urllib.request

    seen: list[dict] = []
    answers = iter(replies)

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
        seen.append(json.loads(req.data))
        return _Resp(next(answers))

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    return seen


def test_a_turn_with_tools_runs_the_calls_and_hands_the_results_back(monkeypatch, hosted):
    """D505: the model calls a tool, the proposer performs it and sends the result as a
    `tool` message with the model's own message (thinking carried as `reasoning_content`),
    and the answer comes when the model stops calling. The hops are kept beside the reply."""
    from flux_llm import OpenAIChatProposer, Tool

    ran: list[dict] = []
    compute = Tool("compute", "run python", {"type": "object", "properties": {"code": {"type": "string"}}},
                   lambda a: (ran.append(a), "17536")[1])
    seen = _wire_script(monkeypatch, [
        {"choices": [{"finish_reason": "tool_calls",
                      "message": {"role": "assistant", "content": "", "reasoning": "I should compute it.",
                                  "tool_calls": [_tool_call("compute", {"code": "print(x)"})]}}],
         "usage": {"prompt_tokens": 40, "completion_tokens": 30}},
        _ok('{"hex": "0x4480"}'),
    ])
    p = OpenAIChatProposer(think=True)
    r = p.propose("what is exp(1.5)?", schema=SCHEMA, tools=[compute])
    assert r.text == '{"hex": "0x4480"}'
    assert ran == [{"code": "print(x)"}]
    first, second = seen
    assert first["tools"][0]["function"]["name"] == "compute" and first["tool_choice"] == "auto"
    assert "response_format" not in first                           # never beside the tools
    assert [m["role"] for m in second["messages"]] == ["user", "assistant", "tool"]
    assert second["messages"][1]["reasoning_content"] == "I should compute it."
    assert second["messages"][1]["tool_calls"][0]["function"]["name"] == "compute"
    assert second["messages"][2] == {"role": "tool", "tool_call_id": "c1", "content": "17536"}
    assert second["tools"], "the model may keep calling until it answers"
    assert r.hops[0].tool == "compute" and r.hops[0].result == "17536"


def test_a_bad_call_is_answered_with_an_error_the_model_reads(monkeypatch, hosted):
    """An unknown tool, arguments that are not JSON, a tool that raises: the model reads the
    error as the tool's result and chooses again; the turn does not fail."""
    from flux_llm import OpenAIChatProposer, Tool

    def boom(_a):
        raise ValueError("division by zero")

    tools = [Tool("compute", "run", {"type": "object"}, boom)]
    seen = _wire_script(monkeypatch, [
        {"choices": [{"finish_reason": "tool_calls", "message": {"role": "assistant", "content": "",
                      "tool_calls": [_tool_call("nope", {}, "a"), {"id": "b", "type": "function",
                                                                     "function": {"name": "compute", "arguments": "{not json"}},
                                     _tool_call("compute", {"code": "1/0"}, "c")]}}],
         "usage": {}},
        _ok("done"),
    ])
    p = OpenAIChatProposer(think=False)
    r = p.propose("go", tools=tools)
    assert r.text == "done"
    results = [m["content"] for m in seen[1]["messages"] if m["role"] == "tool"]
    assert results[0].startswith("error: no tool named 'nope'") and "compute" in results[0]
    assert results[1].startswith("error: the arguments were not a JSON object")
    assert results[2] == "error: division by zero"
    assert all(h.error for h in r.hops)


def test_the_hop_budget_ends_the_turn_with_an_answer_under_the_schema(monkeypatch, hosted):
    """When the budget is spent the last round goes out WITHOUT tools and WITH the schema
    (`tool_choice` gone, `response_format` on): the turn ends in an answer, not a call."""
    from flux_llm import OpenAIChatProposer, Tool, ToolBudget

    tools = [Tool("compute", "run", {"type": "object"}, lambda a: "1")]
    calling = {"choices": [{"finish_reason": "tool_calls", "message": {"role": "assistant", "content": "",
                            "tool_calls": [_tool_call("compute", {"code": "x"})]}}], "usage": {}}
    seen = _wire_script(monkeypatch, [calling, calling, _ok('{"hex": "1"}')])
    p = OpenAIChatProposer(think=False)
    r = p.propose("go", schema=SCHEMA, tools=tools, budget=ToolBudget(hops=2))
    assert r.text == '{"hex": "1"}'
    assert len(seen) == 3 and "tools" in seen[0] and "tools" in seen[1]
    assert "tools" not in seen[2] and seen[2]["response_format"]["json_schema"]["schema"] == SCHEMA
    assert len(r.hops) == 2


def test_streaming_assembles_tool_calls_from_their_fragments(monkeypatch, hosted):
    """The server streams a call as `delta.tool_calls` fragments by index -- the id and name
    first, the arguments in pieces (measured on LocalAI, 2026-09-18); they are assembled
    into one call per index."""
    import urllib.request

    from flux_llm import OpenAIChatProposer, Tool

    monkeypatch.setenv("FLUX_LLM_STREAM", "1")
    chunks = [
        {"choices": [{"delta": {"role": "assistant", "reasoning": "hm"}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "z1", "type": "function",
                                                "function": {"name": "compute", "arguments": ""}}]}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": '{"code":'}}]}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": ' "1+1"}'}}]}, "finish_reason": "tool_calls"}]},
        {"choices": [], "usage": {"prompt_tokens": 5, "completion_tokens": 9}},
    ]
    final = [{"choices": [{"delta": {"content": "two"}, "finish_reason": "stop"}]}]
    scripts = iter([chunks, final])
    seen: list[dict] = []

    class _Stream:
        def __init__(self, docs):
            self.docs = docs

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def __iter__(self):
            for d in self.docs:
                yield f"data: {json.dumps(d)}\n".encode()
            yield b"data: [DONE]\n"

    def fake(req, timeout):
        if req.full_url.endswith("/api/show"):
            import io
            raise urllib.error.HTTPError(req.full_url, 404, "no", {}, io.BytesIO(b""))
        seen.append(json.loads(req.data))
        return _Stream(next(scripts))

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    got: list[dict] = []
    p = OpenAIChatProposer(think=True)
    r = p.propose("go", tools=[Tool("compute", "run", {"type": "object"}, lambda a: (got.append(a), "2")[1])])
    assert r.text == "two"
    assert got == [{"code": "1+1"}]
    assert seen[1]["messages"][1]["tool_calls"][0] == {"id": "z1", "type": "function",
                                                       "function": {"name": "compute", "arguments": '{"code": "1+1"}'}}
    assert seen[1]["messages"][2]["content"] == "2"


def test_a_round_that_runs_away_in_a_turn_with_tools_is_asked_again_without_thinking(monkeypatch, hosted):
    """Measured live (2026-09-18): the model called `history`, then thought 40,000 tokens and
    answered nothing. The plain turn's rule (D483) holds inside a turn with tools: the same
    conversation, tool results kept, goes once more with thinking off."""
    from flux_llm import OpenAIChatProposer, Tool

    tools = [Tool("history", "h", {"type": "object"}, lambda a: "(nothing yet)")]
    seen = _wire_script(monkeypatch, [
        {"choices": [{"finish_reason": "tool_calls", "message": {"role": "assistant", "content": "",
                      "tool_calls": [_tool_call("history", {})]}}], "usage": {}},
        _ok("", reasoning="Let me build it from scratch...", finish="length"),
        _ok('{"prototype": "def design(x): return x"}'),
    ])
    p = OpenAIChatProposer(think=True)
    r = p.propose("go", tools=tools)
    assert r.text == '{"prototype": "def design(x): return x"}'
    assert len(seen) == 3 and "reasoning_effort" not in seen[1] and seen[2]["reasoning_effort"] == "none"
    assert [m["role"] for m in seen[2]["messages"]] == ["user", "assistant", "tool"]   # the hop kept
    assert r.notes["runaway"].startswith("empty response") and r.hops[0].tool == "history"


def test_a_stream_that_says_error_or_ends_empty_is_retried(monkeypatch, hosted):
    """D506, live: four gelu passes ended on "empty response (finish_reason=None, None
    tokens)" -- a stream that ended with nothing in it. An `error` chunk in the stream is the
    server's word and goes through the HTTP retry rules; a stream with no chunk at all is a
    cut connection and goes once more after a pause."""
    import urllib.request

    from flux_llm import OpenAIChatProposer

    monkeypatch.setenv("FLUX_LLM_STREAM", "1")
    monkeypatch.setattr(OpenAIChatProposer, "RETRY_AFTER_S", 0.0)
    scripts = iter([
        [],                                                          # nothing at all
        [{"choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]}],
    ])
    seen: list[dict] = []

    class _Stream:
        def __init__(self, docs):
            self.docs = docs

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def __iter__(self):
            for d in self.docs:
                yield f"data: {json.dumps(d)}\n".encode()
            yield b"data: [DONE]\n"

    def fake(req, timeout):
        if req.full_url.endswith("/api/show"):
            import io
            raise urllib.error.HTTPError(req.full_url, 404, "no", {}, io.BytesIO(b""))
        seen.append(json.loads(req.data))
        return _Stream(next(scripts))

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    p = OpenAIChatProposer(think=False)
    r = p.propose("go")
    assert r.text == "ok" and len(seen) == 2 and "unreachable" in r.notes["retried"]
    # an error chunk: the server's message, with the context rule (half the output budget)
    scripts = iter([
        [{"error": {"code": 500, "message": "Context size has been exceeded"}}],
        [{"choices": [{"delta": {"content": "fine"}, "finish_reason": "stop"}]}],
    ])
    seen.clear()
    p2 = OpenAIChatProposer(think=False, num_predict=4000)
    r = p2.propose("go")
    assert r.text == "fine" and seen[1]["max_tokens"] == 2000 and "Context size" in r.notes["retried"]


def test_a_tool_call_written_as_text_is_performed_like_a_structured_one(monkeypatch, hosted):
    """D506, measured live: 83 of 196 answers carried the chat template's own call syntax in
    `content` -- `<tool_call><function=check><parameter=prototype>...</parameter></function>`.
    A round that offered tools performs it like a structured call; the parser keeps an
    unterminated call (the output cap hit inside it)."""
    from flux_llm import OpenAIChatProposer, Tool
    from flux_llm.tools import text_tool_calls

    got: list[dict] = []
    tools = [Tool("check", "c", {"type": "object"}, lambda a: (got.append(a), "refused, score 3")[1])]
    text = ("Let me check it.\n<tool_call>\n<function=check>\n<parameter=prototype>\ndef design(x):\n"
            "    return x\n</parameter>\n</function>\n</tool_call>")
    seen = _wire_script(monkeypatch, [_ok(text), _ok('{"prototype": "def design(x): return 1"}')])
    p = OpenAIChatProposer(think=False)
    r = p.propose("go", tools=tools)
    assert r.text == '{"prototype": "def design(x): return 1"}'
    assert got == [{"prototype": "def design(x):\n    return x"}]
    assert seen[1]["messages"][1]["tool_calls"][0]["function"]["name"] == "check"
    assert seen[1]["messages"][2]["content"] == "refused, score 3"
    cut = "<tool_call>\n<function=check>\n<parameter=prototype>\ndef design(x):\n    return 7"
    (call,) = text_tool_calls(cut)
    assert json.loads(call["function"]["arguments"]) == {"prototype": "def design(x):\n    return 7"}
    assert text_tool_calls("no call here") == []
    # D535 (review §1.3.11): a tag quoted mid-line, in a string literal or a code fence, is text
    quoted = 'the model says "a tag like <function=check><parameter=x>1</parameter></function> is how you call" and stops'
    assert text_tool_calls(quoted) == []
    fenced = "```python\n<function=check><parameter=prototype>def design(x): return x</parameter></function>\n```\n"
    assert text_tool_calls(fenced) == []
    real = "some words\n<tool_call>\n<function=check><parameter=prototype>def design(x): return x</parameter></function>\n</tool_call>"
    (c,) = text_tool_calls(real)
    assert c["function"]["name"] == "check" and "def design" in c["function"]["arguments"]


def test_a_call_the_server_cannot_parse_is_asked_again_without_tools(monkeypatch, hosted):
    """D506, live: LocalAI answered 500 "Failed to parse tool call arguments as JSON: parse
    error at column 67540" -- the model's own call, too long or malformed for the server.
    The round goes again with no tools offered; the call the model then writes as text is
    performed like any other."""
    import urllib.request

    from flux_llm import OpenAIChatProposer, Tool

    monkeypatch.setattr(OpenAIChatProposer, "RETRY_AFTER_S", 0.0)
    got: list[dict] = []
    tools = [Tool("check", "c", {"type": "object"}, lambda a: (got.append(a), "refused, score 1")[1])]
    err = {"error": {"code": 500, "message": "rpc error: code = InvalidArgument desc = Failed to parse tool call arguments as JSON: parse error at line 1, column 67540"}}
    replies = iter([
        ("http", err), ("http", err),                                   # the retry pair, both 500
        ("ok", _ok("<tool_call>\n<function=check>\n<parameter=prototype>\ndef design(x):\n    return 2\n</parameter>\n</function>\n</tool_call>")),
        ("ok", _ok('{"prototype": "def design(x): return 3"}')),
    ])
    seen: list[dict] = []

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
        seen.append(json.loads(req.data))
        kind, doc = next(replies)
        if kind == "http":
            import io
            raise urllib.error.HTTPError(req.full_url, 500, "boom", {}, io.BytesIO(json.dumps(doc).encode()))
        return _Resp(doc)

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    p = OpenAIChatProposer(think=False)
    r = p.propose("go", tools=tools)
    assert r.text == '{"prototype": "def design(x): return 3"}'
    assert "tools" in seen[0] and "tools" in seen[1] and "tools" not in seen[2]      # asked again, no tools offered
    assert got == [{"prototype": "def design(x):\n    return 2"}]                       # the text call, performed


def test_a_parse_failure_on_the_answering_round_ends_the_turn_on_its_checks(monkeypatch, hosted):
    """D506, live again: the 500 came on the ANSWERING round, where no tools were offered
    and there is no round to re-ask -- the turn ends with an empty reply and its hops, and
    the caller takes the best of what the turn checked."""
    import urllib.request

    from flux_llm import OpenAIChatProposer, Tool, ToolBudget

    monkeypatch.setattr(OpenAIChatProposer, "RETRY_AFTER_S", 0.0)
    tools = [Tool("check", "c", {"type": "object"}, lambda a: "refused, score 5")]
    err = {"error": {"code": 500, "message": "rpc error: Failed to parse tool call arguments as JSON: parse error at line 1, column 101"}}
    replies = iter([("ok", {"choices": [{"finish_reason": "tool_calls", "message": {"role": "assistant", "content": "",
                                         "tool_calls": [_tool_call("check", {"prototype": "p"})]}}], "usage": {}}),
                    ("http", err), ("http", err)])

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
        kind, doc = next(replies)
        if kind == "http":
            import io
            raise urllib.error.HTTPError(req.full_url, 500, "boom", {}, io.BytesIO(json.dumps(doc).encode()))
        return _Resp(doc)

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    p = OpenAIChatProposer(think=False)
    r = p.propose("go", tools=tools, budget=ToolBudget(hops=1))
    assert r.text == ""
    assert r.hops[0].result == "refused, score 5" and "parse tool call" in r.notes["cut_short"]
