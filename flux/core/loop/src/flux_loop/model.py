"""Talking to the model (D413/D426): one call whatever the proposer's shape, JSON out of a reply, and prompt composition with the static prefix first (D422)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from .types import LoopState

if TYPE_CHECKING:  # pragma: no cover
    from flux_llm import Reply

__all__ = ["NO_MODEL", "_ask", "_compose", "_json"]

NO_MODEL = "no model was attached: nothing can be designed; the record and the evaluators still act"

def _ask(state: LoopState, prompt: str, schema: dict | None = None,
         tools: list | None = None) -> "Reply":
    """One model turn (D508): the proposer is a `flux_llm.Proposer`, the answer a `Reply`
    whose `text` the parse gates read. The schema goes out when the request is structured
    (D413); `tools` (D505) are what the turn may call, under the request's hop budget."""
    import hashlib

    # D535 (review §1.3.12): the prompt's digest travels onto every row this turn makes, so a
    # prompt edit and a rule edit in one relaunch can be told apart on the record
    state.last_prompt_sha = hashlib.sha256((prompt or "").encode()).hexdigest()[:16]
    import threading

    state.prompt_sha_by_thread[threading.get_ident()] = state.last_prompt_sha     # D569: per drafting thread
    if state.proposer is None:
        # D507: said once, plainly, where the old monolithic NLU flow used to say it; every
        # turn that would have needed the model refuses with the same line.
        if NO_MODEL not in state.not_established:
            state.not_established.append(NO_MODEL)
        raise RuntimeError(NO_MODEL)
    from .tools import budget_for

    return state.proposer.propose(prompt, schema=schema if state.request.structured else None,
                                  tools=list(tools) if tools else None,
                                  budget=budget_for(state) if tools else None)


def _json(reply: str) -> Any:
    try:
        from flux_llm import strip_markdown_fence
    except Exception:  # noqa: BLE001
        def strip_markdown_fence(t: str) -> str:  # type: ignore[misc]
            return t
    for text in (reply, strip_markdown_fence(reply)):
        try:
            return json.loads(text)
        except Exception:  # noqa: BLE001
            continue
    return None


def _compose(prefix: str, *parts: str) -> str:
    """Static prefix first (cache-friendly), then the changing parts, in order."""
    body = "\n\n".join(p for p in parts if p)
    return f"{prefix}\n\n{body}" if prefix else body
