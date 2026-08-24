"""A proposer that talks to Ollama's NATIVE endpoint, so reasoning can actually be turned off.

WHY THIS EXISTS, since a second way to call the same server needs justifying. Reasoning models of
the qwen3 family do not put their scratch work in `<think>` tags in the response; they put it in a
separate `thinking` field, and `response` stays EMPTY until the reasoning finishes. Ollama's
`/api/generate` accepts `think: false` to switch that off. Its OpenAI-compatible `/v1` endpoint —
which is what `chia.models.ollama.OllamaLLM` speaks — has no equivalent field, and the prompt-level
`/no_think` switch is not honoured by every build.

The failure this caused is not subtle. Measured on qwen3.8 (27.3B, CPU) with a 1,435-token prompt:

    /no_think via /v1   600 tokens, done_reason=length, response=''      160s and climbing
    think:false native  214 tokens, done_reason=stop,   response=JSON     53s

Generation runs about 5.5 tok/s, so the 16,000-token default output budget is roughly 48 minutes
of reasoning before anything is returned at all. Every generation-round symptom in this repo's
interconnect demo — truncation at `finish_reason="length"`, calls hanging to the client timeout,
whole rounds recording nothing — is that one fact.

Deliberately narrow: one method, `str -> str`, `urllib` only, no CHIA and no third-party HTTP
client. It is a proposer for structured output, NOT a general LLM client — no tools, no streaming,
no chat history.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from .text import default_local_model, local_llm_timeout_s

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


# A structured proposal is a few hundred tokens. The cap matters more than it looks: it is the
# difference between a bad answer costing seconds and costing the whole client timeout, because a
# model that will not stop is bounded by nothing else.
DEFAULT_NUM_PREDICT = 1200


def ollama_base_url() -> str:
    """Native API root (not the `/v1` OpenAI-compatible one)."""
    return os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/").removesuffix(
        "/v1")


class NativeOllamaProposer:
    """`propose(prompt) -> str` against `/api/generate`, with reasoning disabled.

    `think=False` is sent unconditionally: every caller of this class in this repo wants JSON or
    YAML conforming to a schema, and for those the reasoning trace is not the product. A caller
    that genuinely wants a model to reason should not use this class.
    """

    def __init__(self, model: str | None = None, *, num_predict: int = DEFAULT_NUM_PREDICT,
                 timeout_s: int | None = None, think: bool = False,
                 num_ctx: int | None = None) -> None:
        self.model = model or default_local_model()
        self.num_predict = num_predict
        self.timeout_s = timeout_s if timeout_s is not None else local_llm_timeout_s()
        self.think = think
        # None = the server's default window. A caller whose prompts outgrow that default
        # (omni's catalog rounds, D377) must SAY so: a prompt silently truncated to the
        # window is the D354 failure shape again, just on the input side.
        self.num_ctx = num_ctx
        self.last_metadata: dict = {}

    def propose(self, prompt: str) -> str:
        think = self.think if _THINK_OVERRIDE is None else _THINK_OVERRIDE
        options: dict = {"num_predict": self.num_predict}
        if self.num_ctx is not None:
            options["num_ctx"] = self.num_ctx
        body = json.dumps({
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "think": think,
            "options": options,
        }).encode()
        req = urllib.request.Request(
            f"{ollama_base_url()}/api/generate", data=body,
            headers={"Content-Type": "application/json"})
        from flux_profile import phase

        with phase("llm: generating (model)", why=f"~{len(prompt) // 4} tok prompt",
                   model=self.model, num_predict=self.num_predict,
                   think=think, prompt=prompt):
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                payload = json.loads(resp.read())

        # Kept for the caller's provenance, and because `done_reason` is the field that would have
        # named this bug on day one: "length" means the model was cut off mid-answer, which is a
        # different failure from a model that answered badly.
        self.last_metadata = {
            "input_tokens": payload.get("prompt_eval_count"),
            "output_tokens": payload.get("eval_count"),
            "done_reason": payload.get("done_reason"),
            "model": self.model,
        }
        text = payload.get("response", "")
        if not text.strip():
            thinking = (payload.get("thinking") or "")[:120]
            raise RuntimeError(
                f"empty response from {self.model} (done_reason="
                f"{payload.get('done_reason')!r}, {payload.get('eval_count')} tokens"
                + (f", reasoning began {thinking!r}" if thinking else "")
                + "). A reasoning model that cannot finish thinking inside its output budget "
                  "returns nothing; raise num_predict or disable thinking.")
        return text
