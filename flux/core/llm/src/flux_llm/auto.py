"""The hosted-or-local choice, as one policy (D337, moved here in D403).

LOCAL IS THE FALLBACK, never the other way round. A hosted endpoint that refuses -- no
key, a rate limit, a network that is not there -- must not end a study that a local
model can still run, and it must not silently become the default either.
`FLUX_LLM_REMOTE` is the only thing that sends anything off this machine.

Born as the interconnect study's `a_proposer`; moved here because the policy was never
about interconnects, and a second loop writing its own copy is how copies drift (the
D200 lesson, again).
"""

from __future__ import annotations

from typing import Any, Callable

from .ollama_native import DEFAULT_NUM_PREDICT, NativeOllamaProposer
from .openrouter import RemoteProposerUnavailable, remote_api_key, remote_enabled, remote_proposer
from .prompting import local_proposer

__all__ = ["auto_proposer", "structured_proposer"]


def auto_proposer(model: str | None = None, *, timeout_s: float | None = None,
                  say: Callable[[str], None] = print) -> Callable[[str], str]:
    """A `prompt -> text` callable: hosted when `FLUX_LLM_REMOTE` asks for it and the
    endpoint answers, the local `model` otherwise -- with the downgrade announced."""
    if remote_enabled():
        try:
            return remote_proposer(timeout_s=timeout_s or 120.0)
        except RemoteProposerUnavailable as exc:
            say(f"    remote model unavailable ({str(exc)[:70]}); using the local one")
    if timeout_s is None:
        return local_proposer(model)
    return local_proposer(model, timeout_s=timeout_s)


def structured_proposer(model: str | None = None, *, num_predict: int = DEFAULT_NUM_PREDICT,
                        timeout_s: int | None = None, think: bool = False,
                        num_ctx: int | None = None,
                        say: Callable[[str], None] = print) -> Any:
    """The proposer OBJECT a demo holds (`propose(prompt, schema)`, `last_metadata`): the
    same policy as `auto_proposer`, for the structured shape (D469). Hosted
    (`OpenAIChatProposer`) when `FLUX_LLM_REMOTE` asks for it and a key is there, the native
    local Ollama otherwise -- with the downgrade announced, never silent.

    `model` is the caller's explicit choice for whichever backend answers; None means each
    backend's own default (`FLUX_LLM_MODEL` locally, `FLUX_REMOTE_MODEL` hosted). `num_ctx`
    is an Ollama request field; a hosted server's window is its own per-model setting, so
    it is not sent there and the caller is told."""
    if remote_enabled():
        if remote_api_key():
            from .openai_compat import OpenAIChatProposer

            if num_ctx is not None:
                say(f"    hosted endpoint: the context window is the server's; "
                    f"num_ctx {num_ctx} not sent")
            return OpenAIChatProposer(model, num_predict=num_predict, timeout_s=timeout_s,
                                      think=think, announce=say)
        say("    remote model unavailable (no OPENROUTER_API_KEY / FLUX_REMOTE_API_KEY "
            "in the environment); using the local one")
    return NativeOllamaProposer(model, num_predict=num_predict, timeout_s=timeout_s,
                                think=think, num_ctx=num_ctx)
