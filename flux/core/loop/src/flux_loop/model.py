"""Talking to the model (D413/D426): one call whatever the proposer's shape, JSON out of a reply, and prompt composition with the static prefix first (D422)."""

from __future__ import annotations

import json
from typing import Any

from .types import LoopState

__all__ = ["_ask", "_compose", "_json"]

def _ask(state: LoopState, prompt: str, schema: dict | None = None) -> str:
    """One model call, schema-constrained when the proposer supports it (D413); the
    proposer's shape is `flux_llm.propose`'s problem, not the loop's (D426)."""
    from flux_llm import propose

    return propose(state.proposer, prompt, schema, structured=state.request.structured)


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
