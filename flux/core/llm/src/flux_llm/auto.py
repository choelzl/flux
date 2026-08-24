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

from typing import Callable

from .openrouter import RemoteProposerUnavailable, remote_enabled, remote_proposer
from .prompting import local_proposer

__all__ = ["auto_proposer"]


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
