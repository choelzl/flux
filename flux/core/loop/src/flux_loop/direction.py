"""One pass, one design (D845): a loop of one design (no parts, no search over knobs) builds one
design a pass. With nothing left to draft -- a design stands -- the pass asks which direction the
next one takes: REFINE the standing design (its numbers in hand, its ladder's next step) or EXPLORE
(a new design that beats it, D839). The orchestrator answers when it can (a model or a coding
agent, `orchestrate:`); else the rules: refine while the standing design moves (D900: decided or not), explore once it has stood
for `budget.explore_after` passes, then alternate, so a stalled campaign tries both.
"""

from __future__ import annotations

from typing import Any

__all__ = ["choose", "stood"]

CHOICES = ("refine", "explore")


def stood(state: Any) -> int:
    """How many passes in a row, the latest first, kept the same standing design: 0 without a record.
    D900: the standing design is the shortfall ranking's pick, decided or not -- a campaign with no
    feasible design yet still stalls or moves; a conclusion from before it names its decision."""
    rows = []
    try:
        rows = state.records.conclusions(limit=50) if state.records is not None else []
    except Exception:  # noqa: BLE001 -- no record: nothing has stood
        rows = []
    ids = [x for r in rows if (x := r.get("standing_key") or r.get("standing") or r.get("decision_key") or r.get("decision"))]
    n = 0
    for x in ids[1:]:
        if x != ids[0]:
            break
        n += 1
    return n


def choose(problem: Any, state: Any, standing: Any, said: str) -> tuple[str, str]:
    """("refine" | "explore", why) for this pass."""
    n = stood(state)
    after = max(1, int(getattr(state.request, "explore_after", 2) or 2))
    orch = problem.roles().orchestrator
    ask = getattr(orch, "direction", None)
    if callable(ask):
        lines = [f"The standing design: {standing.name}. {said}",
                 f"The standing design has stood for {n} pass(es)." if n else "The last pass moved the standing design.",
                 "refine: hand it back with its numbers to be improved -- its next step, a small change that measures.",
                 "explore: ask for a NEW design that beats it -- another structure or algorithm, a risk.",
                 "Pick the direction of the next design."]
        try:
            got = ask(problem, state, lines)
        except Exception:  # noqa: BLE001 -- an unanswered choice falls back to the rules
            got = None
        if got and got[0] in CHOICES:
            return got[0], f"the orchestrator: {got[1]}" if got[1] else "the orchestrator"
    if n < after:
        return "refine", ("the decision moved on the last pass" if n == 0 else f"the decision has stood {n} pass(es), fewer than {after}")
    pick = "explore" if (n - after) % 2 == 0 else "refine"
    return pick, f"the decision has stood {n} pass(es): {'a new design' if pick == 'explore' else 'its ladder again'}, in turn"
