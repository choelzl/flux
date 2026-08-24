"""The one model interface (docs/decisions.md D508): a proposer takes a prompt and answers
with a `Reply`.

    reply = proposer.propose(prompt, schema=..., tools=..., budget=...)
    reply.text        # the answer (or what stands in for it: a salvaged think channel)
    reply.thinking    # the model's reasoning, when the server sends it
    reply.hops        # the tool calls the turn made (D505), in order
    reply.usage       # {"input_tokens", "output_tokens"} as the server counted them
    reply.notes       # what happened on the way: schema applied or dropped, a runaway
                      # re-asked with thinking off, a turn cut short, a salvage

Before D508 this package carried four shapes of proposer (`str -> str`, `(prompt, schema)`,
`(prompt, schema, tools, budget)`, a bare callable) and a dispatcher that read each one's
signature to decide what it could be sent; every loop paid for that with `getattr` on a
`last_metadata` the proposer may or may not have kept. One protocol, one reply object, two
implementations: `OpenAIChatProposer` (any OpenAI-compatible server -- LocalAI, llama.cpp,
Ollama's `/v1`, OpenRouter) and `ScriptedProposer` (replies handed out in order, for tests and
model-free demos). A caller that wants a plain `prompt -> text` function writes
`lambda p: proposer.propose(p).text` at its own site.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, Sequence, runtime_checkable

from .tools import Hop, Tool, ToolBudget

__all__ = ["Proposer", "Reply", "ScriptedProposer"]


@dataclass
class Reply:
    """What one turn came back with. `text` is what the parse gates read."""

    text: str
    thinking: str = ""
    hops: list[Hop] = field(default_factory=list)
    usage: dict[str, int | None] = field(default_factory=dict)
    notes: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:                      # the reply IS its text where text is wanted
        return self.text

    @classmethod
    def of(cls, value: "Reply | str") -> "Reply":
        """A reply from a reply or from bare text (a scripted answer)."""
        return value if isinstance(value, Reply) else cls(str(value))


@runtime_checkable
class Proposer(Protocol):
    """One method. `schema` constrains decoding when the backend can (D413) and is dropped
    where it cannot combine with thinking; `tools` (D505) are what the turn may call, under
    `budget`; a backend that cannot call tools runs the plain turn."""

    def propose(self, prompt: str, *, schema: dict | None = None,
                tools: list[Tool] | None = None, budget: ToolBudget | None = None) -> Reply: ...


class ScriptedProposer:
    """Replies handed out in order, the last one repeating: a run without a model, for
    tests and for demos that must work with no model installed (D430). Records every
    prompt, schema and tool list it was asked, so a test can read what the loop said."""

    def __init__(self, replies: Sequence[str | Reply]) -> None:
        self.replies = list(replies)
        self.prompts: list[str] = []
        self.schemas: list[dict | None] = []
        self.tools: list[list[str]] = []

    def propose(self, prompt: str, *, schema: dict | None = None,
                tools: list[Tool] | None = None, budget: ToolBudget | None = None) -> Reply:
        self.prompts.append(prompt)
        self.schemas.append(schema)
        self.tools.append([t.name for t in tools or []])
        if not self.replies:
            raise RuntimeError("the scripted proposer has no replies")
        i = min(len(self.prompts), len(self.replies)) - 1
        return Reply.of(self.replies[i])
