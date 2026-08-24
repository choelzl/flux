"""The proposer Protocol and fence stripping (docs/decisions.md D200)."""

from __future__ import annotations

import os
import re
from typing import Protocol, Sequence, runtime_checkable

# Any language tag, and not anchored to the whole string. Both matter, and both were learned from
# real model output rather than anticipated: this repo's backends tag fences `json`, `JSON`, `yaml`
# and `cpp` depending on what they were asked for, and they routinely write "Here is my proposal:"
# before the fence or "Hope that helps!" after it. An anchored, `json`-only pattern left the fence
# in place, and the caller then failed to parse text that was perfectly good underneath (D191).
_FENCE_RE = re.compile(r"```\w*\s*(.*?)\s*```", re.DOTALL)

# Reasoning models (qwen3 and kin) emit their scratch work in `<think>...</think>` ahead of the
# answer. Removing it BEFORE looking for a fence is the whole point of the ordering: a model
# reasoning about JSON writes candidate JSON, often fenced, inside the trace, so a fence search
# run first happily returns a draft the model went on to reject. An unterminated block — the shape
# a truncated response takes — leaves nothing behind, which fails to parse and reaches the caller's
# retry path, rather than passing reasoning off as an answer.
_THINK_RE = re.compile(r"<think>.*?(?:</think>|\Z)", re.DOTALL | re.IGNORECASE)


@runtime_checkable
class LLMProposer(Protocol):
    """One method, `str -> str`.

    Deliberately narrower than CHIA's own `LLMCallBase.prompt(user_message, tools) -> QueryResult`:
    no strategy or generator in this repo hands the model tools. Autonomous multi-turn tool-calling
    was tried first and does not work reliably with the local models available here (neither
    `qwen2.5-coder:7b` nor `gemma4:e2b` populates a structured `tool_calls` field via Ollama's
    native or OpenAI-compatible endpoints, confirmed against a minimal example outside Flux), so
    every caller drives its own loop and `str -> str` is the honest interface.
    """

    def propose(self, prompt: str) -> str: ...


@runtime_checkable
class StructuredProposer(Protocol):
    """A proposer that can constrain decoding to a JSON schema (D413): Ollama's native
    `format`, or any backend with the same option. Optional -- `propose()` below asks."""

    def propose(self, prompt: str, schema: dict | None = None) -> str: ...


_ACCEPTS_SCHEMA: dict[type, bool] = {}


def accepts_schema(proposer: object) -> bool:
    """Whether `proposer.propose` takes a `schema` argument -- read once from its signature
    per class, never probed by calling it (a `TypeError` raised INSIDE a model call must
    not be mistaken for "no such parameter")."""
    import inspect

    cls = type(proposer)
    if cls not in _ACCEPTS_SCHEMA:
        try:
            params = inspect.signature(proposer.propose).parameters
            _ACCEPTS_SCHEMA[cls] = "schema" in params or any(
                p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())
        except (TypeError, ValueError):
            _ACCEPTS_SCHEMA[cls] = False
    return _ACCEPTS_SCHEMA[cls]


def propose(proposer: object, prompt: str, schema: dict | None = None, *,
            structured: bool = True) -> str:
    """One model call through whatever proposer shape the caller holds (D426): schema-
    constrained when a schema is given, `structured` is on and the proposer supports it;
    plain otherwise. The four shapes this repo grew (`str -> str`, `(prompt, schema)`,
    CHIA-wrapped, test stubs) all go through here, so a loop never has to know which it has."""
    if not hasattr(proposer, "propose") and callable(proposer):
        return proposer(prompt)                      # a bare `prompt -> text` callable
    if schema is not None and structured and accepts_schema(proposer):
        return proposer.propose(prompt, schema)  # type: ignore[call-arg]
    return proposer.propose(prompt)  # type: ignore[attr-defined]


class ScriptedProposer:
    """Replies handed out in order, the last one repeating: a run without a model, for
    tests and for demos that must work with no model installed (D430). Records every
    prompt it was asked, so a test can read what the loop said."""

    def __init__(self, replies: Sequence[str]) -> None:
        self.replies = list(replies)
        self.prompts: list[str] = []
        self.schemas: list[dict | None] = []

    def propose(self, prompt: str, schema: dict | None = None) -> str:
        self.prompts.append(prompt)
        self.schemas.append(schema)
        if not self.replies:
            raise RuntimeError("the scripted proposer has no replies")
        i = min(len(self.prompts), len(self.replies)) - 1
        return self.replies[i]


class InvalidLLMProposal(Exception):
    """Raised when a model's raw text cannot be turned into a valid candidate for the caller —
    malformed JSON, a value outside the valid set, a shape wrong for the search space, or a repeat
    of something already tried. Each caller raises it with its own specific reason.
    """


def strip_markdown_fence(text: str) -> str:
    """Return the contents of the first fenced block in `text`, or `text` unchanged if it has none.

    Tolerant of any language tag and of prose on either side, because real models produce both,
    and of a leading `<think>` reasoning trace, which is dropped first so that a fence inside the
    model's scratch work is never mistaken for its answer.
    """
    text = _THINK_RE.sub("", text).strip()
    match = _FENCE_RE.search(text)
    return match.group(1).strip() if match else text


# The tag of the model installed on a machine outlives any string hard-coded against it, and this
# repo learned that the expensive way: a model store swapped underneath ~15 live tests and a demo,
# each pinning `qwen2.5-coder:7b` separately, and every one of them silently skipped or fell back.
# One default, one override, and a report can always name what it actually ran.
_DEFAULT_LOCAL_MODEL = "qwen3.8:latest"


def default_local_model() -> str:
    """The local Ollama tag to use unless a caller says otherwise (`FLUX_LLM_MODEL` overrides).

    NOT a claim that this model is present, or good: callers that need a model still have to
    handle its absence, and any measurement made with one has to name the tag it used, because
    results from different models are not comparable.
    """
    return os.environ.get("FLUX_LLM_MODEL", _DEFAULT_LOCAL_MODEL)


# Qwen3-family models reason before answering unless told not to, and the trace is charged to the
# SAME budget as the answer. Under a small serving context that is fatal rather than merely
# wasteful: measured here, a 1,800-token prompt under Ollama's default 4,096-token window left so
# little headroom that the model was still thinking when it was truncated, and every proposal in
# a generation round failed with `finish_reason="length"`. `/no_think` is Qwen3's documented soft
# switch. Applied only to models whose tag says qwen3, because for anything else it is a stray
# token in the prompt.
_REASONING_TAGS = ("qwen3",)


def suppress_reasoning(prompt: str, model: str) -> str:
    """Ask a reasoning model to answer directly, for callers that want structured output.

    NOT a general quality setting, and NOT a finding that reasoning hurts: it is a workaround for
    a trace that does not FIT. Whether thinking produces better designs is untested here, and the
    two roles differ — choosing among four enumerated scopes leaves reasoning little to add, while
    inventing a fabric under coupled constraints is the case where suppressing it could plausibly
    cost real quality. `FLUX_LLM_THINK=1` restores reasoning, both as the A/B arm that would
    settle this and as the right default once the serving window has room for it.
    """
    if os.environ.get("FLUX_LLM_THINK", "").lower() in ("1", "true", "yes"):
        return prompt  # let the model reason: an A/B arm, and the right default once it fits
    if any(tag in model.lower() for tag in _REASONING_TAGS):
        return f"{prompt}\n\n/no_think"
    return prompt


# chia's client gives a call 600 seconds and then reports a transport error. That is generous for a
# hosted API and short for CPU inference: a 27B model at ~7 tok/s spends minutes on the prefill of
# a few thousand prompt tokens alone, and this repo hands its proposers whole knowledge blocks.
# Measured here, proposal calls crossed the default and the round died in retries that each paid
# the full cost again. Overridable because the right value depends entirely on the machine.
def local_llm_timeout_s() -> int:
    """Seconds to allow one local-model call before treating it as a transport failure."""
    return int(os.environ.get("FLUX_LLM_TIMEOUT_S", "3600"))
