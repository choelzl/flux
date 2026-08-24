"""Talking to a model.

One interface, `Proposer.propose(prompt, *, schema, tools, budget) -> Reply`, with two
implementations: `OpenAIChatProposer` for any OpenAI-compatible server (routing set by
`FLUX_LLM_REMOTE`) and `ScriptedProposer` for tests and model-free demos. Also: turn tools
(`Tool`, `ToolBudget`, `Hop`), the bounded ask-check-repair round (`ask_until`, `refine`) and
the fence stripper. Dependency-free: `urllib` only.
"""

from __future__ import annotations

from .openai_compat import (
    DEFAULT_NUM_PREDICT,
    OpenAIChatProposer,
    default_model,
    describe_model,
    local_base_url,
    remote_api_key,
    remote_base_url,
    remote_enabled,
    remote_model,
    set_think_override,
    think_override,
)
from .proposer import Proposer, Reply, ScriptedProposer
from .repair import Refined, ask_until, refine
from .text import InvalidLLMProposal, default_local_model, local_llm_timeout_s, strip_markdown_fence
from .tools import Hop, Tool, ToolBudget

__all__ = ["DEFAULT_NUM_PREDICT", "describe_model", "Hop", "InvalidLLMProposal", "OpenAIChatProposer", "Proposer",
           "Refined", "Reply", "ScriptedProposer", "Tool", "ToolBudget", "ask_until", "default_local_model", "default_model", "local_base_url",
           "local_llm_timeout_s", "refine", "remote_api_key", "remote_base_url", "remote_enabled",
           "remote_model", "set_think_override", "strip_markdown_fence", "think_override"]
