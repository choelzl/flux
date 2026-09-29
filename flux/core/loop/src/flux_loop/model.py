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

    # the prompt's digest goes onto every row this turn makes, so prompt and rule edits can be
    # told apart on the record (D535)
    state.last_prompt_sha = hashlib.sha256((prompt or "").encode()).hexdigest()[:16]
    import threading

    state.prompt_sha_by_thread[threading.get_ident()] = state.last_prompt_sha     # per drafting thread
    if state.last_prompt_sha not in state.cited:          # which papers this prompt carried (D648)
        from flux_knowledge import cited_files

        state.cited[state.last_prompt_sha] = cited_files(prompt or "")
    if state.proposer is None:
        # said once in not_established; every model turn refuses with the same line
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
            pass
        try:
            # a raw newline or tab inside a string: strict JSON refuses it, models emit it (D603)
            return json.loads(text, strict=False)
        except Exception:  # noqa: BLE001
            continue
    # the object inside prose: the first `{` from which a whole object decodes
    decoder = json.JSONDecoder(strict=False)
    at = reply.find("{")
    while at >= 0:
        try:
            doc, _end = decoder.raw_decode(reply, at)
            if isinstance(doc, dict):
                return doc
        except ValueError:
            pass
        at = reply.find("{", at + 1)
    return None


def _compose(prefix: str, *parts: str) -> str:
    """Static prefix first (cache-friendly), then the changing parts, in order."""
    body = "\n\n".join(p for p in parts if p)
    return f"{prefix}\n\n{body}" if prefix else body
