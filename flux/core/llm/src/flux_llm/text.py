"""Fence stripping, the local model tag and the call timeout (docs/decisions.md D200)."""

from __future__ import annotations

import os
import re

# Any language tag, and not anchored to the whole string: models tag fences `json`, `JSON`,
# `yaml`, `cpp`, ... and write prose before and after the fence (D191).
_FENCE_RE = re.compile(r"```\w*\s*(.*?)\s*```", re.DOTALL)

# Reasoning models emit scratch work in `<think>...</think>` ahead of the answer. It is removed
# before looking for a fence, since the trace often holds fenced drafts the model later rejected.
# An unterminated block (a truncated response) leaves nothing, so the caller's retry path runs.
_THINK_RE = re.compile(r"<think>.*?(?:</think>|\Z)", re.DOTALL | re.IGNORECASE)


def strip_markdown_fence(text: str) -> str:
    """Return the contents of the first fenced block in `text`, or `text` unchanged if it has none.

    Tolerant of any language tag and of prose on either side; a leading `<think>` trace is dropped
    first so a fence inside it is never mistaken for the answer.
    """
    text = _THINK_RE.sub("", text).strip()
    match = _FENCE_RE.search(text)
    return match.group(1).strip() if match else text


# One default model tag and one override, so nothing pins a tag separately and a report can name
# what it actually ran.
_DEFAULT_LOCAL_MODEL = "qwen3.8:latest"


def default_local_model() -> str:
    """The local Ollama tag to use unless a caller says otherwise (`FLUX_LLM_MODEL` overrides).

    Not a claim that the model is present: callers must handle its absence, and a measurement must
    name the tag it used, since results from different models are not comparable.
    """
    return os.environ.get("FLUX_LLM_MODEL", _DEFAULT_LOCAL_MODEL)


# Longer than a typical client's 600 s: CPU inference of a large model spends minutes on the
# prefill of long prompts alone. Overridable because the right value depends on the machine.
def local_llm_timeout_s() -> int:
    """Seconds to allow one local-model call before treating it as a transport failure."""
    return int(os.environ.get("FLUX_LLM_TIMEOUT_S", "3600"))
