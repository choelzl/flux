"""What earlier work already settled, in a form that can change the NEXT decision.

An orchestrator that sees only the current frontier rediscovers the shape of a space every run.
It has no way to know that a whole family was already ruled out, because the reason a candidate
failed lives in the store and never reaches the prompt (docs/decisions.md D297).

Two sources, and the difference between them is stated wherever they are shown:

  CONCLUSIONS  drawn by a model FROM measurements, and marked as inference
  REFUSALS     grouped constraint failures, which ARE measurements and are the cheapest possible
               guidance: they say where not to spend the next round

Deliberately NOT the measured points. Those usually already reach the prompt as a frontier
digest, and repeating them spends a budget that is charged on every decision.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from .conclusions import CONCLUSION_KIND
from .store import FactStore


def fact_store_path(campaign_db: str | Path) -> str:
    """Where a study's lessons live: beside the campaign store they were drawn from, so moving or
    deleting a study takes its conclusions with it rather than leaving orphaned claims behind."""
    return str(Path(campaign_db).with_suffix(".facts.db"))


def lessons_digest(campaign_db: str | Path, facts: list[dict[str, Any]] | None = None, *,
                   max_chars: int = 1400, max_conclusions: int = 4,
                   max_refusals: int = 3) -> str:
    """A short brief for an orchestrator. Capped because this is sent on EVERY decision, and on
    local inference the prompt is the whole cost of a call.

    `facts` are mined facts (from `mine_knowledge`); pass None to include conclusions only.
    """
    lines: list[str] = []
    store_path = Path(fact_store_path(campaign_db))
    if store_path.exists():
        try:
            with FactStore(store_path) as store:
                for stored in store.facts(kind=CONCLUSION_KIND)[:max_conclusions]:
                    fact = stored.fact
                    lines.append(f"- {fact['statement']}")
                    actionable = (fact.get("evidence") or {}).get("actionable")
                    if actionable:
                        lines.append(f"  so: {actionable}")
        except Exception:  # noqa: BLE001 — no lessons yet is the normal first-run case
            pass
    # What the screen got WRONG, where a real placement has settled it (D305). This is the one
    # thing that tells an orchestrator which estimates to distrust, and therefore which fabric is
    # worth spending a real placement on next: without it, choosing what to measure is a guess.
    try:
        with FactStore(store_path) as store:
            for stored in store.facts(kind="estimator_bias")[:max_conclusions]:
                lines.append(f"- {stored.fact['statement']}")
    except Exception:  # noqa: BLE001
        pass
    for message, count in _refusal_groups(facts or [], max_refusals):
        lines.append(f"- {count} group(s) of candidates were refused: {message}")
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
