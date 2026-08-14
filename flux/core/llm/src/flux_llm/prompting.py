"""Getting a local proposer, and fitting what you hand it into the context it has.

Both were written inside one application's demo and are not about that application at all: any
study that hands a model curated guidance plus its own measurements needs the same two things,
and the second one needs it for a reason that is easy to miss (docs/decisions.md D292): mined
facts GROW with a study, so an unbounded knowledge block works early and fails late, exactly
when the campaign has learned something worth telling the model.
"""

from __future__ import annotations

import os
from typing import Any, Callable

from .ollama_native import NativeOllamaProposer
from .text import default_local_model


def local_proposer(model: str | None = None, **kwargs: Any) -> Callable[[str], str]:
    """A `prompt -> text` callable against the local model. Nothing leaves the machine.

    The NATIVE endpoint, so reasoning can actually be disabled (D293). Every caller in this repo
    wants JSON or YAML back, and on a reasoning model the trace is charged to the same budget as
    the answer: measured on qwen3.8, the OpenAI-compatible path spent its entire output budget
    thinking and returned an empty response, while this one answered in 17 seconds.
    """
    proposer = NativeOllamaProposer(model or default_local_model(), **kwargs)
    return proposer.propose


def budget_chars(env_var: str = "FLUX_PROMPT_BUDGET_CHARS", default: int = 9000) -> int:
    return int(os.environ.get(env_var, str(default)))


def fit_to_budget(guidance: str, facts: list, render, *, budget: int,
                  guidance_share: float = 0.6) -> tuple[str, list]:
    """Trim a knowledge block to `budget` characters, and SAY what was dropped.

    Guidance is capped at `guidance_share` and no more. Letting it take the budget in order is
    the obvious implementation and it is wrong: measured, guidance consumed the whole allowance
    and left ZERO facts, producing a model that knows every family exists and nothing about the
    run it is advising, which is the precise failure two knowledge surfaces exist to avoid.

    Cost is measured on the RENDERED block the caller will actually send, via `render`, because
    rendering adds framing and each fact carries the limits that must travel with it.
    """
    cap = int(budget * guidance_share)
    if len(guidance) > cap:
        guidance = guidance[:cap].rsplit("\n", 1)[0] + "\n- (guidance truncated to fit)"
    room = budget - len(guidance)
    kept: list = []
    for fact in facts:
        if len(render([*kept, fact])) > room:
            break
        kept.append(fact)
    if len(kept) < len(facts):
        # Told, not hidden: a model reasoning from a narrowed view should know it is narrowed,
        # the same posture the coverage line takes for search scopes.
        guidance += (f"\n\n(Showing {len(kept)} of {len(facts)} measured facts: the rest did not "
                     "fit this model\'s context. The omitted ones are not evidence against "
                     "anything.)")
    return guidance, kept
