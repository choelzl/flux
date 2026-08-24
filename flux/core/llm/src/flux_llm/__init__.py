"""Shared handling of raw LLM output (docs/decisions.md D200).

Four packages here send a prompt to a model and parse what comes back, and each had grown its own
copy of the same two things: a one-method proposer Protocol, and a markdown-fence stripper. The
copies drifted, which is the whole reason this package exists rather than a comment in each file
asking the next author to keep them in step — D191 fixed a prose-intolerance bug in two of the four
and did not know the other two existed.

Deliberately dependency-free and deliberately narrow: `str -> str` for the proposer, no tools, no
CHIA. Real backends (`chia.models.ollama.OllamaLLM`) are adapted onto it by the caller, so no
package here has to know about CHIA to use a model.
"""

from __future__ import annotations

from .auto import auto_proposer
from .ollama_native import NativeOllamaProposer, set_think_override, think_override
from .openrouter import (
    RemoteProposerUnavailable,
    remote_enabled,
    remote_model,
    remote_proposer,
)
from .prompting import budget_chars, fit_to_budget, local_proposer
from .text import (InvalidLLMProposal, LLMProposer, default_local_model, local_llm_timeout_s,
                   strip_markdown_fence, suppress_reasoning)

__all__ = ["InvalidLLMProposal", "RemoteProposerUnavailable", "auto_proposer", "remote_enabled",
           "remote_model", "remote_proposer", "LLMProposer", "NativeOllamaProposer", "set_think_override", "think_override", "budget_chars", "fit_to_budget", "local_proposer", "default_local_model", "local_llm_timeout_s",
           "strip_markdown_fence",
           "suppress_reasoning"]
