"""THE proposer (docs/decisions.md D469, D508): one request shape over the OpenAI chat
protocol, against whichever server answers it -- LocalAI, llama.cpp, Ollama's `/v1`, OpenRouter.

Two server facts shape this class, both measured on qwen3.6-35b-a3b-apex (2026-09-15):

- Thinking is a PER-REQUEST choice here, never a server setting. `reasoning_effort: "none"`
  goes out when the caller wants the answer straight; NOTHING goes out when it wants the model
  to think, so the server's own default (on) stands for that call and for every other user of
  the server.
- A schema and thinking do not combine. The grammar starts at the first generated token and
  the chat template has already opened the think block, so the constrained JSON lands in the
  `reasoning` field, `content` stays empty, and no reasoning happened (23 tokens). A thinking
  request therefore goes out WITHOUT its schema and the callers' parse gates do the work, as
  they did before D413; `reply.notes["schema"]` says so.

WHERE THE REQUEST GOES is one policy (D337, D403, folded here in D508): `FLUX_LLM_REMOTE`
is the only thing that sends anything off this machine. Set, the request goes to
`FLUX_REMOTE_BASE_URL` (OpenRouter by default) with `OPENROUTER_API_KEY` /
`FLUX_REMOTE_API_KEY`, and the first prompt that leaves is ANNOUNCED; unset, it goes to the
local Ollama's `/v1` (`OLLAMA_BASE_URL`), no key. Asking for the hosted server without a key is
refused at construction -- it is not silently downgraded, because the operator asked for the
remote one on purpose. What this class does NOT have is `num_ctx`: the context window is the
server's per-model setting, not a request field, and `reply.usage["input_tokens"]` is how a
caller sees what the prompt cost against it (Ollama's native endpoint, which took `num_ctx` and
could turn thinking off, left with D508: the floor is an OpenAI-compatible server, review §6.5).
"""

from __future__ import annotations

import io
import os
import json
import time
import urllib.error
import urllib.request

from .proposer import Reply
from .text import default_local_model, local_llm_timeout_s
from .tools import ToolBudget

__all__ = ["DEFAULT_NUM_PREDICT", "OpenAIChatProposer", "announce_remote", "default_model",
           "local_base_url", "remote_api_key", "remote_base_url", "remote_enabled", "remote_model",
           "set_think_override", "think_override"]

# A structured proposal is a few hundred tokens. The cap matters more than it looks: it is the
# difference between a bad answer costing seconds and costing the whole client timeout, because a
# model that will not stop is bounded by nothing else.
DEFAULT_NUM_PREDICT = 1200

_REMOTE_BASE = "https://openrouter.ai/api/v1"
_REMOTE_MODEL = "deepseek/deepseek-chat-v3-0324:free"
_ANNOUNCED = False

# Runtime override for `think`, settable from the TUI's t key (D392): None defers to
# each proposer's own setting; True/False wins over it for every FUTURE call. A live
# toggle beats an env var here because the operator decides mid-run, watching the
# task tab's durations.
_THINK_OVERRIDE: bool | None = None


def set_think_override(value: bool | None) -> None:
    global _THINK_OVERRIDE
    _THINK_OVERRIDE = value


def think_override() -> bool | None:
    return _THINK_OVERRIDE


def remote_enabled() -> bool:
    """OPT-IN, and by an explicit switch rather than by a key happening to be in the
    environment. A key can be present for unrelated reasons; sending a study's measurements
    off the machine is not something to start doing because of that."""
    return os.environ.get("FLUX_LLM_REMOTE", "").strip().lower() in {"1", "true", "yes", "on"}


def remote_base_url() -> str:
    """`FLUX_REMOTE_BASE_URL`, always ending in `/v1`: a LocalAI or llama.cpp server is
    usually named by its root, OpenRouter by its `/api/v1`, and the chat path hangs off
    the same place on both."""
    base = os.environ.get("FLUX_REMOTE_BASE_URL", _REMOTE_BASE).rstrip("/")
    return base if base.endswith("/v1") else f"{base}/v1"


def remote_api_key() -> str | None:
    return os.environ.get("OPENROUTER_API_KEY") or os.environ.get("FLUX_REMOTE_API_KEY") or None


def remote_model() -> str:
    return os.environ.get("FLUX_REMOTE_MODEL", _REMOTE_MODEL)


def local_base_url() -> str:
    """The local Ollama's OpenAI-compatible root: `OLLAMA_BASE_URL` (its native root) + `/v1`."""
    root = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/").removesuffix("/v1")
    return f"{root}/v1"


def default_model() -> str:
    """The model a proposer built without a name talks to: the hosted one when
    `FLUX_LLM_REMOTE` is set, the local tag otherwise."""
    return remote_model() if remote_enabled() else default_local_model()


def announce_remote(base: str, model: str, announce=print) -> None:
    """ANNOUNCED once per process, on the first prompt that actually goes out -- not at
    construction, because building a proposer and never using it sends nothing."""
    global _ANNOUNCED
    if _ANNOUNCED:
        return
    _ANNOUNCED = True
    announce(f"\n!! SENDING PROMPTS OFF THIS MACHINE to {base} ({model}).\n"
             "   The prompt carries this study's problem and its measured results.\n"
             "   Unset FLUX_LLM_REMOTE to keep everything local.\n")




class OpenAIChatProposer:
    """`propose(prompt, *, schema, tools, budget) -> Reply` against `<base>/chat/completions`.

    `think=False` (the default) sends `reasoning_effort: "none"` with that request.
    `think=True` sends nothing about reasoning: the server's own setting decides, which on a
    reasoning model with its default config means the model thinks first and answers in
    `content`. `num_predict=None` leaves the output budget to the server as well. `base_url`
    and `api_key` name a server outright; left out, `FLUX_LLM_REMOTE` decides (module doc).
    """

    def __init__(self, model: str | None = None, *, num_predict: int | None = DEFAULT_NUM_PREDICT,
                 timeout_s: float | None = None, think: bool = False,
                 base_url: str | None = None, api_key: str | None = None,
                 announce=print) -> None:
        self.hosted = remote_enabled() or base_url is not None
        self.model = model or (remote_model() if self.hosted else default_local_model())
        self.num_predict = num_predict
        self.timeout_s = timeout_s if timeout_s is not None else local_llm_timeout_s()
        self.think = think
        self.base_url = (base_url or (remote_base_url() if self.hosted else local_base_url())).rstrip("/")
        key = api_key if api_key is not None else (remote_api_key() if self.hosted else None)
        if self.hosted and not key:
            raise RuntimeError("no OPENROUTER_API_KEY / FLUX_REMOTE_API_KEY in the environment; "
                               "FLUX_LLM_REMOTE asks for the hosted server and it needs one")
        self._key = key
        self._announce = announce
        self._context: int | None | bool = False      # False = not asked yet
        # D493: the reply STREAMS so the thinking shows while the model thinks (the task row
        # is updated through flux_profile.progress); FLUX_LLM_STREAM=0 asks for one message
        self.stream = os.environ.get("FLUX_LLM_STREAM", "1") not in ("0", "false", "no")

    def context_length(self) -> int | None:
        """The model's context window as the server states it, or None. Asked once, on
        LocalAI's Ollama-compatible `/api/show` (`general.context_length`); a server
        without it (OpenRouter) answers 404 and the window stays unknown."""
        if self._context is False:
            self._context = None
            root = self.base_url[:-3] if self.base_url.endswith("/v1") else self.base_url
            req = urllib.request.Request(
                f"{root}/api/show", data=json.dumps({"model": self.model}).encode(),
                headers=self._headers())
            try:
                with urllib.request.urlopen(req, timeout=min(self.timeout_s, 20)) as resp:
                    info = json.loads(resp.read()).get("model_info") or {}
                n = info.get("general.context_length")
                self._context = int(n) if isinstance(n, (int, float)) and n > 0 else None
            except Exception:  # noqa: BLE001 - the probe is a courtesy
                self._context = None
        return self._context

    #: characters per prompt token, calibrated from each reply's `prompt_tokens` (D487:
    #: the fixed estimate of two was half the truth for these prompts and cost the
    #: thinking model ~5k tokens of reply budget a call -- and it hit the limit)
    chars_per_token: float = 2.0

    def _cap(self, prompt: "str | int") -> int | None:
        """`max_tokens` that fits the window beside this prompt (D473). The live run asked
        for 80,000 on a 58,112 window and lost two parts to "Context size has been
        exceeded"; the prompt is estimated from the calibrated characters-per-token ratio
        (two until the first reply says otherwise), with a tenth of margin. `prompt` is
        the text or its length in characters (a conversation with tool rounds, D505)."""
        if self.num_predict is None:
            return None
        ctx = self.context_length()
        if ctx is None:
            return self.num_predict
        chars = prompt if isinstance(prompt, int) else len(prompt)
        est = int(chars / max(1.0, self.chars_per_token) * 1.1)
        room = ctx - est - 256
        return max(1024, min(self.num_predict, room))

    def _body(self, messages: list[dict], schema: dict | None, think: bool,
              tools: list | None = None, tool_choice: str | None = None,
              hop_share: float | None = None) -> dict:
        body: dict = {"model": self.model, "messages": messages}
        cap = self._cap(_chars(messages))
        if hop_share and tools:
            # a tool round thinks less than the answering round: a SHARE of the model's window
            # (D544; D506 fixed it at 24,000 tokens), and no cap of its own on a window the
            # server does not state
            ctx = self.context_length()
            if ctx:
                share = max(1024, int(ctx * float(hop_share)))
                cap = min(cap, share) if cap is not None else share
        if cap is not None:
            body["max_tokens"] = cap
        if not think:
            body["reasoning_effort"] = "none"
        if tools:
            # D505: the tools ride along and the schema does not -- measured, a schema beside
            # the tools makes the model answer instead of calling (see flux_llm.tools)
            from .tools import as_openai

            body["tools"] = as_openai(tools)
            body["tool_choice"] = tool_choice or "auto"
        elif schema is not None and not think:
            body["response_format"] = {"type": "json_schema",
                                       "json_schema": {"name": "reply", "schema": schema}}
        return body

    # One retry, after a pause, for what a second attempt can fix: a 5xx (the server's
    # context pool was full for a moment), an unreachable host, a cut connection. A 4xx
    # is the request's fault and is not retried. "Context size" answers retry with half
    # the output budget -- the cap above is an estimate, the server's count is the fact.
    RETRY_AFTER_S = 3.0

    def _call(self, body: dict) -> dict:
        if self.stream:
            return self._call_streaming(body)
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions", data=json.dumps(body).encode(),
            headers=self._headers())
        with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
            return json.loads(resp.read())

    def _headers(self, **more: str) -> dict[str, str]:
        h = {"Content-Type": "application/json", **more}
        if self._key:
            h["Authorization"] = f"Bearer {self._key}"
        return h

    # How much of the growing text the live row carries (its tail), and how often
    LIVE_TAIL_CHARS = 4000
    LIVE_EVERY_S = 0.5

    def _call_streaming(self, body: dict) -> dict:
        """The same reply as `_call`, assembled from the server's SSE chunks (D493): each
        `delta.reasoning` / `delta.content` token is appended and the tail of both is pushed
        to the running phase's row every half second, so the operator watches the model
        think instead of a blank "still running". The final chunk carries `usage` when
        `stream_options.include_usage` is asked (llama.cpp / LocalAI do)."""
        from flux_profile import progress

        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps({**body, "stream": True, "stream_options": {"include_usage": True}}).encode(),
            headers=self._headers(Accept="text/event-stream"))
        reasoning: list[str] = []
        content: list[str] = []
        calls: dict[int, dict] = {}                   # D505: tool calls, assembled by index
        finish = None
        usage: dict = {}
        role = "assistant"
        t_last, n_last = time.monotonic(), 0
        t_first: float | None = None
        with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
            for raw in resp:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if isinstance(chunk, dict) and chunk.get("error") and not chunk.get("choices"):
                    # D506: the server says WHY in the stream (a context overflow mid-request,
                    # a model reload) -- as an HTTP error, so the retry rules apply
                    err = chunk["error"]
                    text = err.get("message") if isinstance(err, dict) else str(err)
                    code = int(err.get("code", 500)) if isinstance(err, dict) and str(err.get("code", "")).isdigit() else 500
                    raise urllib.error.HTTPError(req.full_url, code, str(text)[:300], {},
                                                 io.BytesIO(json.dumps(chunk).encode()))
                if chunk.get("usage"):
                    usage = chunk["usage"]
                r = c = None                                  # a chunk may carry no choice at all
                for ch in chunk.get("choices") or []:
                    delta = ch.get("delta") or {}
                    r = delta.get("reasoning") or delta.get("reasoning_content")
                    c = delta.get("content")
                    if r:
                        reasoning.append(r)
                    if c:
                        content.append(c)
                    if delta.get("role"):
                        role = delta["role"]
                    for tc in delta.get("tool_calls") or []:
                        i = int(tc.get("index", len(calls)))
                        slot = calls.setdefault(i, {"id": None, "type": "function",
                                                    "function": {"name": "", "arguments": ""}})
                        if tc.get("id"):
                            slot["id"] = tc["id"]
                        fn = tc.get("function") or {}
                        if fn.get("name"):
                            slot["function"]["name"] = fn["name"]
                        if fn.get("arguments"):
                            slot["function"]["arguments"] += fn["arguments"]
                    if ch.get("finish_reason"):
                        finish = ch["finish_reason"]
                if (r or c) and t_first is None:
                    t_first = time.monotonic()
                n = len(reasoning) + len(content)
                now = time.monotonic()
                if n != n_last and now - t_last >= self.LIVE_EVERY_S:
                    t_last, n_last = now, n
                    think_txt, out_txt = "".join(reasoning), "".join(content)
                    live: dict = {"streamed": f"~{n} tokens so far"
                                  + (f", {n / max(1e-6, now - t_first):.0f} tok/s" if t_first else "")}
                    if think_txt:
                        live["thinking (live tail)"] = think_txt[-self.LIVE_TAIL_CHARS:]
                    if out_txt:
                        live["reply (live tail)"] = out_txt[-self.LIVE_TAIL_CHARS:]
                    progress(**live)
                    cyc = looping(think_txt) if think_txt else None
                    if cyc is not None:
                        # D494: a cycle in the thinking never ends on its own -- the budget
                        # does, minutes later. Closing the stream stops the generation.
                        raise _Looped(f"thinking looped: a {cyc[0]}-character cycle repeated "
                                      f"{cyc[1]} times after ~{n} tokens; the request was aborted",
                                      think_txt)
        if not content and not reasoning and not calls and finish is None:
            # the stream ended before anything came (a cut connection, a server restart):
            # said as unreachable, so the request goes once more after a pause
            raise urllib.error.URLError("the stream ended with nothing in it (no chunk, no finish_reason)")
        message = {"role": role, "content": "".join(content)}
        if reasoning:
            message["reasoning"] = "".join(reasoning)
        if calls:
            message["tool_calls"] = [calls[i] for i in sorted(calls)]
        return {"choices": [{"index": 0, "message": message, "finish_reason": finish}],
                "usage": usage}

    def propose(self, prompt: str, *, schema: dict | None = None, tools: list | None = None,
                budget: "ToolBudget | None" = None) -> Reply:
        think = self.think if think_override() is None else think_override()
        notes: dict = {}
        if tools:
            return self._propose_with_tools(prompt, schema, think, list(tools), budget, notes)
        try:
            return self._propose(prompt, schema, think, notes)
        except _RanAway as exc:
            # THE RUNAWAY (D483): a reasoning model that spends its whole budget thinking
            # returns nothing -- three live passes ended that way at ~47k tokens. Thinking is
            # per request here (D469): the same prompt goes again with reasoning off, and an
            # answer without deliberation beats no answer and a pass lost.
            notes["runaway"] = str(exc)[:200]
            return self._propose(prompt, schema, False, notes, after=f"runaway: {exc}")

    def _propose(self, prompt: str, schema: dict | None, think: bool, notes: dict,
                 after: str = "") -> Reply:
        messages = [{"role": "user", "content": prompt}]
        with self._turn("llm: generating (model)" + (" -- retry, thinking off" if after else ""),
                        messages, think, after=after) as out:
            message = self._exchange(messages, schema, think, out, notes=notes)
            return self._reply(message, self._answer(message, think, out, notes), notes)

    @staticmethod
    def _reply(message: dict, text: str, notes: dict, hops: list | None = None) -> Reply:
        reasoning = message.get("reasoning") or message.get("reasoning_content") or ""
        return Reply(text, thinking=reasoning if text != reasoning else "", hops=list(hops or []),
                     usage={"input_tokens": notes.get("input_tokens"),
                            "output_tokens": notes.get("output_tokens")}, notes=notes)

    # ------------------------------------------------------------------ tools (D505)
    def _propose_with_tools(self, prompt: str, schema: dict | None, think: bool, tools: list,
                            budget: "ToolBudget | None", notes: dict) -> Reply:
        """A TURN WITH TOOLS: the model may call any of `tools` as often as the budget allows;
        each call is performed here and its result handed back as a `tool` message, the
        thinking of every round carried along; the last round goes out without tools (and
        with the schema, when there is one) so the turn ends in an answer. Every hop is kept
        on `reply.hops` and shown in the pane. A round that runs away is asked
        again with thinking off, like a plain turn (D483)."""
        from .tools import Hop, ToolBudget, run_call, text_tool_calls

        budget = budget or ToolBudget()
        messages: list[dict] = [{"role": "user", "content": prompt}]
        hops: list[Hop] = []
        from flux_profile import phase

        with self._turn("llm: turn with tools", messages, think,
                        tools=", ".join(t.name for t in tools), hops_allowed=budget.hops) as out:
            for round_ in range(budget.hops + 1):
                last = round_ >= budget.hops
                if round_ and self._crowded(messages, budget.compact_share):
                    self._compact_rounds(messages, budget, out, notes)
                # ONE name for every round (D547): the timing tab sums a turn's rounds into
                # one row instead of a row per hop; the task tab still shows each round
                with phase("llm: round", why=f"hop {round_ + 1} of {budget.hops + 1}"
                           + (", the answer, no tools" if last else "") + f", {len(messages)} message(s)") as inner:
                    try:
                        message = self._exchange(messages, schema if last else None, think, inner,
                                                 tools=None if last else tools, hop_share=budget.hop_share,
                                                 notes=notes)
                    except _RanAway as exc:
                        inner["runaway"] = str(exc)[:200]
                        notes["runaway"] = str(exc)[:200]
                        message = self._exchange(messages, schema if last else None, False, inner,
                                                 tools=None if last else tools, hop_share=budget.hop_share,
                                                 notes=notes)
                    except RuntimeError as exc:
                        if "parse tool call" not in str(exc):
                            raise
                        # D506, live: "500: rpc error: ... Failed to parse tool call arguments as
                        # JSON: parse error at column 67540" -- the SERVER could not read the
                        # model's own call (67k characters of arguments). The round goes again
                        # without tools offered; a call the model then writes as text is
                        # performed all the same (`text_tool_calls`)
                        if last and hops:
                            # the answering round itself: the turn ends with what it measured --
                            # an empty reply, and the caller takes the best of the turn's checks
                            inner["error"] = f"the server could not read the answer ({str(exc)[:120]}); the turn ends on its checks"
                            notes["cut_short"] = str(exc)[:200]
                            out["hops"] = "\n".join(h.line() for h in hops)
                            return Reply("", hops=list(hops), notes=notes)
                        inner["retried"] = f"without tools offered: {str(exc)[:160]}"
                        message = self._exchange(messages, None, think, inner, tools=None,
                                                 hop_share=budget.hop_share, notes=notes)
                calls = message.get("tool_calls") or []
                if not calls and not last:
                    # the call written as text (the template's own syntax) is a call all the same
                    calls = text_tool_calls(message.get("content") or "")
                    if calls:
                        message = {**message, "tool_calls": calls, "content": ""}
                if not calls or last:
                    out["hops"] = "\n".join(h.line() for h in hops) or "(no tool was called)"
                    try:
                        return self._reply(message, self._answer(message, think, out, notes), notes, hops)
                    except _RanAway as exc:
                        # the round ran away in its thinking (D483): the same conversation
                        # goes once more with thinking off, the tool results kept
                        out["runaway"] = str(exc)[:200]
                        notes["runaway"] = str(exc)[:200]
                        with phase("llm: the answer, thinking off", why="after a runaway") as inner:
                            message = self._exchange(messages, schema, False, inner, notes=notes)
                            return self._reply(message, self._answer(message, False, inner, notes), notes, hops)
                messages.append(_assistant_turn(message))
                for n, call in enumerate(calls):
                    hop = run_call(tools, call, max_result_chars=budget.result_chars)
                    hops.append(hop)
                    out[f"hop {len(hops)}"] = hop.line(600)
                    messages.append({"role": "tool", "tool_call_id": call.get("id") or f"call_{len(hops)}",
                                     "content": hop.result})
        raise RuntimeError("unreachable: the tool loop returns from its last round")

    def _compact_rounds(self, messages: list[dict], budget: "ToolBudget", out: dict, notes: dict) -> None:
        """A crowded turn's older rounds (D549, D550): the model's own note of what they
        established replaces them when the budget says `compact: llm` (one call, thinking off,
        no tools); otherwise, or when that call fails, their results are digested by rule."""
        from flux_profile import phase

        from .compact import compact_conversation, older_rounds_prompt, replace_older_rounds

        gone = 0
        if budget.compact == "llm":
            ask = older_rounds_prompt(messages)
            if ask:
                with phase("llm: condense", why="the older rounds of this turn, as facts") as inner:
                    try:
                        reply = self._exchange([{"role": "user", "content": ask}], None, False, inner, notes={})
                        digest = str(reply.get("content") or "").strip()
                    except Exception as exc:  # noqa: BLE001 -- the rules half is always there
                        inner["error"] = str(exc)[:200]
                        digest = ""
                    inner["digest"] = digest
                gone = replace_older_rounds(messages, digest)
                if gone:
                    notes["condensed"] = int(notes.get("condensed", 0)) + 1
        if not gone:
            gone = compact_conversation(messages)
        if gone:
            notes["compacted"] = int(notes.get("compacted", 0)) + gone
            out["compacted"] = f"{notes['compacted']} chars of older rounds" + (" (the model's note)" if notes.get("condensed") else "")

    def _crowded(self, messages: list[dict], share: float) -> bool:
        """Does the conversation pass `share` of the window the server states? Unknown
        window: never (nothing to measure against)."""
        ctx = self.context_length()
        return bool(ctx and share) and _chars(messages) / max(1.0, self.chars_per_token) > share * ctx

    def _turn(self, title: str, messages: list[dict], think: bool, **extra):
        """The pane's row for a turn: the prompt, the model, the budget."""
        from flux_profile import phase

        prompt = messages[0].get("content", "") if messages else ""
        if self.hosted:
            announce_remote(self.base_url, self.model, self._announce)
        return phase(title, why=f"~{len(prompt) // 4} tok prompt", model=self.model,
                     num_predict=self._cap(prompt), think=think, prompt=prompt,
                     **{k: v for k, v in extra.items() if v})

    def _exchange(self, messages: list[dict], schema: dict | None, think: bool, out: dict,
                  tools: list | None = None, hop_share: float | None = None,
                  notes: dict | None = None) -> dict:
        """ONE request and its message back, with the retry and the metadata: a 5xx or a cut
        connection is tried once more after a pause, a context overflow with half the output
        budget; a 4xx is the request's fault and is not retried."""
        body = self._body(messages, schema, think, tools=tools, hop_share=hop_share)
        retried = None
        for attempt in (1, 2):
            try:
                payload = self._call(body)
                break
            except _Looped as exc:
                out["error"] = str(exc)
                out["thinking"] = exc.reasoning
                raise _RanAway(str(exc)) from exc
            except urllib.error.HTTPError as exc:
                # The server says WHY in the body ("model not found", a schema it
                # cannot compile); a bare status code would send the operator to the
                # server logs.
                text = _error_text(exc)
                out["error"] = f"{exc.code}: {text}"
                if attempt == 2 or exc.code < 500:
                    raise RuntimeError(f"{self.base_url} answered {out['error']}") from exc
                if "context" in text.lower() and body.get("max_tokens"):
                    body["max_tokens"] = max(512, body["max_tokens"] // 2)
                retried = out["error"]
            except urllib.error.URLError as exc:
                out["error"] = f"unreachable: {exc.reason}"
                if attempt == 2:
                    raise RuntimeError(f"{self.base_url} unreachable: {exc.reason}") from exc
                retried = out["error"]
            time.sleep(self.RETRY_AFTER_S)
        if retried:
            out["retried"] = f"after: {retried}" + (
                f"; max_tokens now {body['max_tokens']}" if "max_tokens" in body else "")
            out.pop("error", None)

        choices = payload.get("choices") or []
        message = choices[0].get("message", {}) if choices else {}
        finish = choices[0].get("finish_reason") if choices else None
        usage = payload.get("usage") or {}
        # The reply's notes: the LAST exchange of a turn is what they describe
        notes = notes if notes is not None else {}
        notes.update({
            "input_tokens": usage.get("prompt_tokens"),
            "output_tokens": usage.get("completion_tokens"),
            "finish": finish,
            "model": self.model,
            "schema": ("applied" if "response_format" in body
                       else "dropped: thinking on" if schema is not None and not tools else None),
            "max_tokens": body.get("max_tokens"),
            "retried": retried,
        })
        prompt_chars = _chars(messages)
        if usage.get("prompt_tokens") and prompt_chars > 2000:
            # calibrate: this prompt's real chars-per-token, smoothed, when plausible
            measured = prompt_chars / max(1, int(usage["prompt_tokens"]))
            if 1.5 <= measured <= 8.0:
                self.chars_per_token = (0.5 * self.chars_per_token + 0.5 * measured
                                        if self.chars_per_token != 2.0 else measured)
        # The task pane shows what came back beside what was asked (D470).
        out["tokens"] = (f"{usage.get('prompt_tokens')} in, "
                         f"{usage.get('completion_tokens')} out, finish_reason={finish}")
        if notes["schema"]:
            out["schema"] = notes["schema"]
        text = message.get("content") or ""
        reasoning = message.get("reasoning") or message.get("reasoning_content") or ""
        if message.get("tool_calls"):
            out["reply"] = "(tool call" + ("s" if len(message["tool_calls"]) > 1 else "") + ": " + ", ".join(
                str((c.get("function") or {}).get("name")) for c in message["tool_calls"]) + ")"
        else:
            out["reply"] = text
        if reasoning.strip():
            out["thinking"] = reasoning
        message = dict(message)
        message["_finish"] = finish
        message["_usage"] = usage
        return message

    def _answer(self, message: dict, think: bool, out: dict, notes: dict) -> str:
        """The reply's text, or what stands in for it: the salvage from the think channel
        and the re-ask rules of D410/D483/D488/D503."""
        text = message.get("content") or ""
        reasoning = message.get("reasoning") or message.get("reasoning_content") or ""
        finish, usage = message.get("_finish"), message.get("_usage") or {}
        if not text.strip():
            if reasoning.strip() and finish == "stop":
                # The model FINISHED thinking and answered nothing. D410 handed the think
                # channel to the parse gates; measured over 40 hours (D503): four times,
                # nothing parsable in any of them, a turn lost each time. If the answer
                # was in the think channel the gates get it; otherwise the same prompt
                # goes again with thinking off, like the runaway (D488).
                if think and "{" in reasoning and '"' in reasoning:
                    notes["salvaged_from_thinking"] = True
                    out["reply"] = "(empty; the think channel below is handed to the parse gates)"
                    return reasoning
                if think:
                    out["error"] = f"finished thinking ({usage.get('completion_tokens')} tokens) and answered nothing"
                    raise _RanAway(f"thought {usage.get('completion_tokens')} tokens and answered nothing")
                notes["salvaged_from_thinking"] = True
                out["reply"] = "(empty; the think channel below is handed to the parse gates)"
                return reasoning
            head = reasoning[:120]
            msg = (f"empty response from {self.model} (finish_reason={finish!r}, "
                   f"{usage.get('completion_tokens')} tokens"
                   + (f", reasoning began {head!r}" if head else "")
                   + "). A reasoning model that cannot finish thinking inside its output "
                     "budget returns nothing; raise num_predict or disable thinking.")
            out["error"] = msg
            if think and finish == "length":
                raise _RanAway(msg)
            raise RuntimeError(msg)
        return text


def _chars(messages: list[dict]) -> int:
    """How long the conversation is, for the output cap: every message's text and the tool
    calls' arguments count against the window."""
    n = 0
    for m in messages:
        n += len(str(m.get("content") or ""))
        for c in m.get("tool_calls") or []:
            n += len(str((c.get("function") or {}).get("arguments") or ""))
        n += len(str(m.get("reasoning_content") or ""))
    return n


def _assistant_turn(message: dict) -> dict:
    """The model's own message, handed back for the next round: its content, its tool calls
    and its thinking (`reasoning_content`, the field llama.cpp's chat templates read), and
    nothing of the bookkeeping this class added."""
    turn: dict = {"role": "assistant", "content": message.get("content") or ""}
    if message.get("tool_calls"):
        turn["tool_calls"] = [{"id": c.get("id") or f"call_{i}", "type": "function",
                               "function": {"name": (c.get("function") or {}).get("name", ""),
                                            "arguments": (c.get("function") or {}).get("arguments") or "{}"}}
                              for i, c in enumerate(message["tool_calls"])]
    reasoning = message.get("reasoning") or message.get("reasoning_content")
    if reasoning:
        turn["reasoning_content"] = reasoning
    return turn


class _RanAway(RuntimeError):
    """Thinking consumed the whole reply budget and no answer came (finish_reason length)."""


class _Looped(RuntimeError):
    """The streamed thinking fell into a cycle (D494) and the request was aborted."""

    def __init__(self, msg: str, reasoning: str) -> None:
        super().__init__(msg)
        self.reasoning = reasoning


def looping(text: str, *, needle: int = 200, window: int = 8000, times: int = 4) -> tuple[int, int] | None:
    """(cycle length, repetitions) when the tail of `text` is a CYCLE -- the last `needle`
    characters occur `times` or more times in the last `window` -- else None. D494: the
    live thinking showed the same four lines ("So `v` should be `x * 2^10` ... No.")
    repeated for minutes until the budget ran out; 200 identical characters four times in
    8,000 is not deliberation."""
    tail = text[-window:]
    if len(tail) < needle * times:
        return None
    seed = tail[-needle:]
    if tail.count(seed) < times:
        return None
    period = next((q for q in range(1, needle + 1) if tail[-needle - q:-q] == seed), needle)
    cycle, reps = tail[-period:], 0
    while reps * period < len(tail) and tail.endswith(cycle * (reps + 1)):
        reps += 1
    return period, reps


def _error_text(exc: urllib.error.HTTPError) -> str:
    try:
        raw = exc.read().decode(errors="replace")
    except Exception:  # noqa: BLE001 - the body is a courtesy, the status is the fact
        return exc.reason or ""
    try:
        err = json.loads(raw).get("error")
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])[:200]
        if isinstance(err, str):
            return err[:200]
    except Exception:  # noqa: BLE001
        pass
    return raw[:200]
