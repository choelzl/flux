"""The proposer (D508): one request shape over the OpenAI chat protocol, against any server
that answers it -- LocalAI, llama.cpp, Ollama's `/v1`, OpenRouter.

- Thinking is a per-request choice: `reasoning_effort: "none"` goes out for a straight
  answer; nothing goes out to let the model think, so the server default stands.
- A schema and thinking do not combine: the grammar would constrain the think block and
  `content` would stay empty. A thinking request goes out without its schema and the callers'
  parse gates do the work; `reply.notes["schema"]` says so.

Where the request goes: only `FLUX_LLM_REMOTE` sends anything off this machine. Set, requests
go to `FLUX_REMOTE_BASE_URL` (OpenRouter by default) with `OPENROUTER_API_KEY` /
`FLUX_REMOTE_API_KEY`; unset, to the local
Ollama's `/v1` (`OLLAMA_BASE_URL`). The hosted default without a key is refused at
construction, never downgraded. The context window is the server's setting, not a request
field; `reply.usage["input_tokens"]` shows what a prompt cost against it.
"""

from __future__ import annotations

import io
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

from .proposer import Reply
from .text import default_local_model, local_llm_timeout_s
from .tools import ToolBudget

__all__ = ["DEFAULT_NUM_PREDICT", "OpenAIChatProposer", "default_model",
           "local_base_url", "remote_api_key", "remote_base_url", "remote_enabled", "remote_model",
           "set_think_override", "think_override"]

# A structured proposal is a few hundred tokens; the cap bounds a model that will not stop,
# which otherwise costs the whole client timeout.
DEFAULT_NUM_PREDICT = 1200

_REMOTE_BASE = "https://openrouter.ai/api/v1"
_REMOTE_MODEL = "deepseek/deepseek-chat-v3-0324:free"

# Runtime override for `think`, set from the TUI's t key: None defers to each proposer's
# setting; True/False wins for every future call.
_THINK_OVERRIDE: bool | None = None


def set_think_override(value: bool | None) -> None:
    global _THINK_OVERRIDE
    _THINK_OVERRIDE = value


def think_override() -> bool | None:
    return _THINK_OVERRIDE


def remote_enabled() -> bool:
    """Opt-in by an explicit act: `FLUX_LLM_REMOTE=1`, or naming the server with
    `FLUX_REMOTE_BASE_URL` (D590). A key alone never does it; `FLUX_LLM_REMOTE=0` forces local."""
    switch = os.environ.get("FLUX_LLM_REMOTE", "").strip().lower()
    if switch in {"0", "false", "no", "off"}:
        return False
    if switch in {"1", "true", "yes", "on"}:
        return True
    return bool(os.environ.get("FLUX_REMOTE_BASE_URL", "").strip())


def describe_model(proposer: object) -> str:
    """One line for a run's start: which model, where, local or not."""
    model = getattr(proposer, "model", None)
    base = getattr(proposer, "base_url", None)
    if model is None:
        return f"model: {type(proposer).__name__}"
    where = "remote" if getattr(proposer, "hosted", False) else "local"
    return f"model: {model} at {base} ({where})"


def remote_base_url() -> str:
    """`FLUX_REMOTE_BASE_URL`, always ending in `/v1`: a LocalAI or llama.cpp server is
    usually named by its root, OpenRouter by its `/api/v1`, and the chat path hangs off
    the same place on both."""
    base = os.environ.get("FLUX_REMOTE_BASE_URL", _REMOTE_BASE).rstrip("/")
    return base if base.endswith("/v1") else f"{base}/v1"


def remote_api_key() -> str | None:
    """The key: `OPENROUTER_API_KEY`, `FLUX_REMOTE_API_KEY`, or the first line of the file
    `FLUX_REMOTE_API_KEY_FILE` names (so the key never sits in an env file or a shell) (D651)."""
    key = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("FLUX_REMOTE_API_KEY")
    path = os.environ.get("FLUX_REMOTE_API_KEY_FILE")
    if not key and path:
        try:
            key = Path(path).expanduser().read_text().strip().splitlines()[0].strip()
        except (OSError, IndexError):
            key = None
    return key or None


def user_config_path() -> Path:
    """`FLUX_CONFIG`, else `$XDG_CONFIG_HOME/flux/flux.env` (`~/.config/flux/flux.env`)."""
    if os.environ.get("FLUX_CONFIG"):
        return Path(os.environ["FLUX_CONFIG"]).expanduser()
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "flux" / "flux.env"


def load_user_config(path: Path | None = None) -> list[str]:
    """The user's `FLUX_*=value` lines into the environment, where the shell did not set them
    already (the shell wins). Returns the names it set. Blank lines and `#` comments are skipped;
    anything that is not a `FLUX_` or `OLLAMA_` variable is ignored (D651)."""
    path = path or user_config_path()
    if not path.is_file():
        return []
    set_now = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = (t.strip() for t in line.split("=", 1))
        if name.startswith(("FLUX_", "OLLAMA_")) and name not in os.environ:
            os.environ[name] = value.strip("'\"")
            set_now.append(name)
    # coding agents (OpenCode's config reads {env:FLUX_REMOTE_API_KEY}) inherit the key from the
    # environment, in memory only, never written anywhere (D669)
    if "FLUX_REMOTE_API_KEY" not in os.environ and os.environ.get("FLUX_REMOTE_API_KEY_FILE"):
        key = remote_api_key()
        if key:
            os.environ["FLUX_REMOTE_API_KEY"] = key
    return set_now


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
                 base_url: str | None = None, api_key: str | None = None) -> None:
        self.hosted = remote_enabled() or base_url is not None
        self.model = model or (remote_model() if self.hosted else default_local_model())
        self.num_predict = num_predict
        self.timeout_s = timeout_s if timeout_s is not None else local_llm_timeout_s()
        self.think = think
        self.base_url = (base_url or (remote_base_url() if self.hosted else local_base_url())).rstrip("/")
        key = api_key if api_key is not None else (remote_api_key() if self.hosted else None)
        named_server = base_url is not None or bool(os.environ.get("FLUX_REMOTE_BASE_URL", "").strip())
        if self.hosted and not key and not named_server:
            # the default hosted endpoint (OpenRouter) needs a key; a server you name yourself
            # may have none
            raise RuntimeError("no OPENROUTER_API_KEY / FLUX_REMOTE_API_KEY(_FILE) in the environment: the default "
                               "hosted server needs one (or name your own with FLUX_REMOTE_BASE_URL)")
        self._key = key
        self._context: int | None | bool = False      # False = not asked yet
        # the reply streams so the thinking shows live (via flux_profile.progress);
        # FLUX_LLM_STREAM=0 asks for one message
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

    def preflight(self, timeout_s: float = 10.0) -> str:
        """Why a run with this model cannot start, or "": the server answers `/models`, accepts
        the key, and lists the model when it lists any. Asked once, before the first pass."""
        where = "FLUX_REMOTE_BASE_URL and FLUX_REMOTE_API_KEY" if self.hosted else \
            "`ollama serve` running, or OLLAMA_BASE_URL / FLUX_REMOTE_BASE_URL"
        req = urllib.request.Request(f"{self.base_url}/models", headers=self._headers())
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                listed = json.loads(resp.read() or b"{}").get("data") or []
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                return f"the model server at {self.base_url} refused the key (HTTP {exc.code}): check FLUX_REMOTE_API_KEY"
            return ""                                  # a server without /models: let the first call say
        except Exception as exc:  # noqa: BLE001
            return (f"no model server answers at {self.base_url} ({exc}); check {where} "
                    "(docs/models.md)")
        ids = {str(m.get("id", "")) for m in listed if isinstance(m, dict)}
        wanted = {self.model, self.model if ":" in self.model else f"{self.model}:latest"}
        if ids and not ids & wanted:
            fix = f"`ollama pull {self.model}`, or " if not self.hosted else ""
            return (f"the model server at {self.base_url} has no model {self.model!r} (it has: "
                    f"{', '.join(sorted(ids)[:8])}{', ...' if len(ids) > 8 else ''}); {fix}name one it has "
                    "with --model or FLUX_LLM_MODEL / FLUX_REMOTE_MODEL")
        return ""

    #: characters per prompt token, calibrated from each reply's `prompt_tokens`
    chars_per_token: float = 2.0

    def _cap(self, prompt: "str | int") -> int | None:
        """`max_tokens` that fits the window beside this prompt, estimated from the calibrated
        characters-per-token ratio with a tenth of margin. `prompt` is the text or its length
        in characters."""
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
            # a tool round gets a share of the window (D544); no cap when the server states none
            ctx = self.context_length()
            if ctx:
                share = max(1024, int(ctx * float(hop_share)))
                cap = min(cap, share) if cap is not None else share
        if cap is not None:
            body["max_tokens"] = cap
        if not think:
            body["reasoning_effort"] = "none"
        if tools:
            # tools go out without the schema: with both, the model answers instead of calling
            from .tools import as_openai

            body["tools"] = as_openai(tools)
            body["tool_choice"] = tool_choice or "auto"
        elif schema is not None and not think:
            body["response_format"] = {"type": "json_schema",
                                       "json_schema": {"name": "reply", "schema": schema}}
        return body

    # One retry after a pause for a 5xx, an unreachable host or a cut connection; a 4xx is not
    # retried. A context-size error retries with half the output budget (the cap is an estimate).
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
        """The same reply as `_call`, assembled from SSE chunks; the tail of reasoning and
        content is pushed to the running phase's row every half second. The final chunk
        carries `usage` when `stream_options.include_usage` is asked."""
        from flux_profile import progress

        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps({**body, "stream": True, "stream_options": {"include_usage": True}}).encode(),
            headers=self._headers(Accept="text/event-stream"))
        reasoning: list[str] = []
        content: list[str] = []
        calls: dict[int, dict] = {}                   # tool calls, assembled by index
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
                    # an error in the stream (context overflow, model reload) is raised as an
                    # HTTP error, so the retry rules apply
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
                        # a cycle in the thinking never ends on its own; closing the stream
                        # stops the generation (D494)
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
        """One turn, recorded in the run's transcript whatever it ends as."""
        from . import transcript

        t0 = time.monotonic()
        base = {"model": self.model, "server": self.base_url, "prompt": prompt, "structured": schema is not None,
                "tools": [getattr(t, "name", str(t)) for t in tools or []]}
        try:
            reply = self._propose_turn(prompt, schema=schema, tools=tools, budget=budget)
        except Exception as exc:
            transcript.record("model", **base, error=f"{type(exc).__name__}: {exc}", seconds=round(time.monotonic() - t0, 2))
            raise
        notes = getattr(reply, "notes", None) or {}
        transcript.record("model", **base, reply=reply.text, hops=[h.line(400) for h in getattr(reply, "hops", None) or []],
                          notes=notes, seconds=round(time.monotonic() - t0, 2),
                          **{k: notes[n] for k, n in (("tokens_in", "turn_tokens_in"), ("tokens_out", "turn_tokens_out")) if notes.get(n)})
        return reply

    def _propose_turn(self, prompt: str, *, schema: dict | None = None, tools: list | None = None,
                      budget: "ToolBudget | None" = None) -> Reply:
        think = self.think if think_override() is None else think_override()
        notes: dict = {}
        if tools:
            return self._propose_with_tools(prompt, schema, think, list(tools), budget, notes)
        try:
            return self._propose(prompt, schema, think, notes)
        except _RanAway as exc:
            # Runaway (D483): a model that spends its whole budget thinking returns nothing, so
            # the same prompt goes again with reasoning off.
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

    # ------------------------------------------------------------------ tools
    def _propose_with_tools(self, prompt: str, schema: dict | None, think: bool, tools: list,
                            budget: "ToolBudget | None", notes: dict) -> Reply:
        """A turn with tools: the model calls `tools` as the budget allows, each result goes
        back as a `tool` message, and the last round goes out without tools (and with the
        schema, if any) so the turn ends in an answer. Hops are kept on `reply.hops`. A round
        that runs away is asked again with thinking off."""
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
                # one name for every round, so the timing tab sums a turn into one row
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
                        # the server could not parse the model's tool call: the round goes again
                        # without tools; a call written as text is still performed
                        # (`text_tool_calls`)
                        if last and hops:
                            # the answering round itself: the turn ends with what it measured --
                            # an empty reply, and the caller takes the best of the turn's checks
                            inner["error"] = f"the server could not read the answer ({str(exc)[:120]}); the turn ends on its checks"
                            notes["cut_short"] = str(exc)[:200]
                            out["hops"] = "\n".join(h.line() for h in hops)
                            return Reply("", hops=list(hops), notes=notes)
                        inner["retried"] = f"without tools offered: {str(exc)[:160]}"
                        try:
                            message = self._exchange(messages, None, think, inner, tools=None,
                                                     hop_share=budget.hop_share, notes=notes)
                        except RuntimeError as again:
                            if "parse tool call" not in str(again):
                                raise
                            # the retry failed the same way (the server parses text calls too):
                            # end the turn on what it measured (D596)
                            inner["error"] = f"the server could not read the model's call twice ({str(again)[:120]}); the turn ends"
                            notes["cut_short"] = str(again)[:200]
                            out["hops"] = "\n".join(h.line() for h in hops) or "(no tool was called)"
                            return Reply("", hops=list(hops), notes=notes)
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
        """Compact a crowded turn's older rounds: the model's own note replaces them when the
        budget says `compact: llm` (one call, thinking off, no tools); otherwise, or when that
        call fails, their results are digested by rule."""
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
        return phase(title, why=f"~{len(prompt) // 4} tok prompt", model=self.model,
                     num_predict=self._cap(prompt), think=think, prompt=prompt,
                     **{k: v for k, v in extra.items() if v})

    def _exchange(self, messages: list[dict], schema: dict | None, think: bool, out: dict,
                  tools: list | None = None, hop_share: float | None = None,
                  notes: dict | None = None) -> dict:
        """One request and its message back, with the retry rules above and the metadata."""
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
                # include the body: the server says why ("model not found", a bad schema)
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
        # The reply's notes describe the last exchange of a turn
        notes = notes if notes is not None else {}
        # the whole turn's tokens, every exchange of its tool hops (D694)
        notes["turn_tokens_in"] = notes.get("turn_tokens_in", 0) + int(usage.get("prompt_tokens") or 0)
        notes["turn_tokens_out"] = notes.get("turn_tokens_out", 0) + int(usage.get("completion_tokens") or 0)
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
        # for the task pane, beside what was asked
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
        """The reply's text, or what stands in for it: salvage from the think channel, or a
        re-ask with thinking off."""
        text = message.get("content") or ""
        reasoning = message.get("reasoning") or message.get("reasoning_content") or ""
        finish, usage = message.get("_finish"), message.get("_usage") or {}
        if not text.strip():
            if reasoning.strip() and finish == "stop":
                # The model finished thinking and answered nothing. If the answer is in the
                # think channel the gates get it; otherwise re-ask with thinking off (D503).
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
    """(cycle length, repetitions) when the tail of `text` is a cycle -- the last `needle`
    characters occur `times` or more times in the last `window` -- else None (D494)."""
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
