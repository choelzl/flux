"""Fence stripping, the local model tag and the call timeout (docs/decisions.md D200)."""

from __future__ import annotations

import os
import re

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


# chia's client gives a call 600 seconds and then reports a transport error. That is generous for a
# hosted API and short for CPU inference: a 27B model at ~7 tok/s spends minutes on the prefill of
# a few thousand prompt tokens alone, and this repo hands its proposers whole knowledge blocks.
# Measured here, proposal calls crossed the default and the round died in retries that each paid
# the full cost again. Overridable because the right value depends entirely on the machine.
def local_llm_timeout_s() -> int:
    """Seconds to allow one local-model call before treating it as a transport failure."""
    return int(os.environ.get("FLUX_LLM_TIMEOUT_S", "3600"))
