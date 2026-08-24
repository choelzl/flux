"""What earlier work already settled, in a form that can change the NEXT decision.

Without this an orchestrator rediscovers the shape of a space every run: why candidates failed
lives in the store, not the prompt (D297). The refusals, grouped by message, ARE measurements
and are the cheapest possible guidance: they say where not to spend the next round.

Not the measured points: those already reach the prompt as a frontier digest.
"""

from __future__ import annotations

from collections import Counter
from typing import Any


def lessons_digest(facts: list[dict[str, Any]], *, max_chars: int = 1400,
                   max_refusals: int = 3) -> str:
    """A short brief for an orchestrator, capped because it is sent on every decision.

    `facts` are mined facts (from `mine_knowledge`).
    """
    lines = [f"- {count} group(s) of candidates were refused: {message}"
             for message, count in _refusal_groups(facts, max_refusals)]
    if not lines:
        return "(nothing yet: this is the first run against this store)"
    text = "\n".join(lines)
    return text if len(text) <= max_chars else text[:max_chars] + "\n  (truncated)"


def _refusal_groups(facts: list[dict[str, Any]], limit: int) -> list[tuple[str, int]]:
    counts: Counter = Counter()
    for fact in facts:
        if fact.get("kind") == "refusal_pattern":
            message = str((fact.get("evidence") or {}).get("message", ""))[:70]
            if message:
                counts[message] += 1
    return counts.most_common(limit)
