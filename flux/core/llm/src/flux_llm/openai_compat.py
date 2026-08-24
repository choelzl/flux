"""A structured proposer over the OpenAI chat protocol, for hosted servers that are not
Ollama (docs/decisions.md D469).

WHY A THIRD WAY TO CALL A MODEL. `NativeOllamaProposer` exists because Ollama's
OpenAI-compatible `/v1` could not turn reasoning off (D293); `remote_proposer` exists for a
hosted endpoint at `str -> str` (D337). A LocalAI server (llama.cpp behind the OpenAI
protocol) is neither: it answers `/api/generate` too, but that compatibility layer drops a
reasoning model's answer on the floor (measured: `response: ""`, `eval_count` = the whole
budget, no `thinking` field to salvage from), while its `/v1/chat/completions` honours the two
things the loop needs -- `reasoning_effort: "none"` per request, and `response_format:
json_schema` for constrained decoding (D413).

Two server facts shape this class, both measured on qwen3.6-35b-a3b-apex (2026-09-15):

- Thinking is a PER-REQUEST choice here, never a server setting. `reasoning_effort: "none"`
  goes out when the caller wants the answer straight; NOTHING goes out when it wants the model
  to think, so the server's own default (on) stands for that call and for every other user of
  the server.
- A schema and thinking do not combine. The grammar starts at the first generated token and
  the chat template has already opened the think block, so the constrained JSON lands in the
  `reasoning` field, `content` stays empty, and no reasoning happened (23 tokens). A thinking
  request therefore goes out WITHOUT its schema and the callers' parse gates do the work, as
  they did before D413; `last_metadata["schema"]` says so.

Deliberately the same shape as `NativeOllamaProposer`: `propose(prompt, schema)`,
`last_metadata`, the TUI's think override, `urllib` only -- so a demo can hold either without
knowing which. What it does NOT have is `num_ctx`: the context window is the server's per-model
setting, not a request field, and `last_metadata["input_tokens"]` is how a caller sees what the
prompt cost against it.
"""

from __future__ import annotations

import os
import json
import time
import urllib.error
import urllib.request

from .ollama_native import DEFAULT_NUM_PREDICT, think_override
from .openrouter import announce_remote, remote_api_key, remote_base_url, remote_model
from .text import local_llm_timeout_s

__all__ = ["OpenAIChatProposer"]


class OpenAIChatProposer:
    """`propose(prompt, schema=None) -> str` against `<base>/chat/completions`.

    `think=False` (the default, like every structured proposer here) sends
    `reasoning_effort: "none"` with that request. `think=True` sends nothing about reasoning:
    the server's own setting decides, which on a reasoning model with its default config means
    the model thinks first and answers in `content`. `num_predict=None` leaves the output
    budget to the server as well.
    """

    def __init__(self, model: str | None = None, *, num_predict: int | None = DEFAULT_NUM_PREDICT,
                 timeout_s: float | None = None, think: bool = False,
                 base_url: str | None = None, api_key: str | None = None,
                 announce=print) -> None:
        self.model = model or remote_model()
        self.num_predict = num_predict
        self.timeout_s = timeout_s if timeout_s is not None else local_llm_timeout_s()
        self.think = think
        self.base_url = (base_url or remote_base_url()).rstrip("/")
        key = api_key if api_key is not None else remote_api_key()
        if not key:
            raise RuntimeError("no OPENROUTER_API_KEY / FLUX_REMOTE_API_KEY in the environment; "
                               "the hosted proposer needs one")
        self._key = key
        self._announce = announce
        self.last_metadata: dict = {}
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
                headers={"Content-Type": "application/json",
                         "Authorization": f"Bearer {self._key}"})
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

    def _cap(self, prompt: str) -> int | None:
        """`max_tokens` that fits the window beside this prompt (D473). The live run asked
        for 80,000 on a 58,112 window and lost two parts to "Context size has been
        exceeded"; the prompt is estimated from the calibrated characters-per-token ratio
        (two until the first reply says otherwise), with a tenth of margin."""
        if self.num_predict is None:
            return None
        ctx = self.context_length()
        if ctx is None:
            return self.num_predict
        est = int(len(prompt) / max(1.0, self.chars_per_token) * 1.1)
        room = ctx - est - 256
        return max(1024, min(self.num_predict, room))

    def _body(self, prompt: str, schema: dict | None, think: bool) -> dict:
        body: dict = {"model": self.model,
                      "messages": [{"role": "user", "content": prompt}]}
        cap = self._cap(prompt)
        if cap is not None:
            body["max_tokens"] = cap
        if not think:
            body["reasoning_effort"] = "none"
        if schema is not None and not think:
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
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {self._key}"})
        with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
            return json.loads(resp.read())

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
            headers={"Content-Type": "application/json", "Accept": "text/event-stream",
                     "Authorization": f"Bearer {self._key}"})
        reasoning: list[str] = []
        content: list[str] = []
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
        message = {"role": role, "content": "".join(content)}
        if reasoning:
            message["reasoning"] = "".join(reasoning)
        return {"choices": [{"index": 0, "message": message, "finish_reason": finish}],
                "usage": usage}

    def propose(self, prompt: str, schema: dict | None = None) -> str:
        think = self.think if think_override() is None else think_override()
        try:
            return self._propose(prompt, schema, think)
        except _RanAway as exc:
            # THE RUNAWAY (D483): a reasoning model that spends its whole budget thinking
            # returns nothing -- three live passes ended that way at ~47k tokens. Thinking is
            # per request here (D469): the same prompt goes again with reasoning off, and an
            # answer without deliberation beats no answer and a pass lost.
            text = self._propose(prompt, schema, False, after=f"runaway: {exc}")
            self.last_metadata["runaway"] = str(exc)[:200]
            return text

    def _propose(self, prompt: str, schema: dict | None, think: bool, after: str = "") -> str:
        body = self._body(prompt, schema, think)
        announce_remote(self.base_url, self.model, self._announce)
        from flux_profile import phase

        with phase("llm: generating (model)" + (" -- retry, thinking off" if after else ""),
                   why=f"~{len(prompt) // 4} tok prompt",
                   model=self.model, num_predict=body.get("max_tokens"), think=think,
                   prompt=prompt, **({"after": after} if after else {})) as out:
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
            # The same fields as the native proposer's, under the same names, so the TUI
            # and the record read either without caring which server answered.
            self.last_metadata = {
                "input_tokens": usage.get("prompt_tokens"),
                "output_tokens": usage.get("completion_tokens"),
                "done_reason": finish,
                "model": self.model,
                "schema": ("applied" if "response_format" in body
                           else "dropped: thinking on" if schema is not None else None),
                "max_tokens": body.get("max_tokens"),
                "retried": retried,
            }
            text = message.get("content") or ""
            reasoning = message.get("reasoning") or message.get("reasoning_content") or ""
            if usage.get("prompt_tokens") and len(prompt) > 2000:
                # calibrate: this prompt's real chars-per-token, smoothed, when plausible
                measured = len(prompt) / max(1, int(usage["prompt_tokens"]))
                if 1.5 <= measured <= 8.0:
                    self.chars_per_token = (0.5 * self.chars_per_token + 0.5 * measured
                                            if self.chars_per_token != 2.0 else measured)
            # The task pane shows what came back beside what was asked (D470).
            out["tokens"] = (f"{usage.get('prompt_tokens')} in, "
                             f"{usage.get('completion_tokens')} out, finish_reason={finish}")
            if self.last_metadata["schema"]:
                out["schema"] = self.last_metadata["schema"]
            out["reply"] = text
            if reasoning.strip():
                out["thinking"] = reasoning
            if not text.strip():
                if reasoning.strip() and finish == "stop":
                    # The model FINISHED thinking and answered nothing. D410 handed the think
                    # channel to the parse gates; measured over 40 hours (D503): four times,
                    # nothing parsable in any of them, a turn lost each time. If the answer
                    # was in the think channel the gates get it; otherwise the same prompt
                    # goes again with thinking off, like the runaway (D488).
                    if think and "{" in reasoning and '"' in reasoning:
                        self.last_metadata["salvaged_from_thinking"] = True
                        out["reply"] = "(empty; the think channel below is handed to the parse gates)"
                        return reasoning
                    if think:
                        out["error"] = f"finished thinking ({usage.get('completion_tokens')} tokens) and answered nothing"
                        raise _RanAway(f"thought {usage.get('completion_tokens')} tokens and answered nothing")
                    self.last_metadata["salvaged_from_thinking"] = True
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
