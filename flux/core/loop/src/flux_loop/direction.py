"""DSE preferences guide reasoned local or structural experiments throughout a campaign.

The orchestrator can explore before stagnation. Rules provide a fallback, and an optional
budget quota reserves exploration attempts across passes while keeping verified candidates.
"""

from __future__ import annotations

from typing import Any
import threading

__all__ = ["choose", "stood"]

CHOICES = ("improve", "explore", "tune", "finetune", "variations", "refine")
PRESETS = ("adaptive", "explore", "improve", "tune", "finetune", "variations")
_CHOOSING = threading.Lock()
PROMPTS = {
    "adaptive": "Choose local changes or a different algorithm or architecture according to the evidence. Take a reasoned risk whenever it could teach us something; there is no need to wait for stagnation.",
    "explore": "Try a materially different algorithm, architecture or representation. The incumbent is a measured baseline to learn from, not a structure you must preserve.",
    "improve": "Improve the objectives using local or structural changes. Do not preserve the current approach merely because it works.",
    "tune": "Favor tuning parameters and implementation choices around promising designs. Broader changes remain welcome when supported by a useful hypothesis.",
    "finetune": "Favor small, attributable edits near a promising candidate. Explain why a broader change is worth trying if you choose one.",
    "variations": "Try distinct variations of promising approaches, including alternatives that need more than one attempt to develop. Use intent and measured trade-offs as evidence, without copying or patching earlier designs. Avoid repeating evaluated candidates.",
    "sweep": "Cover distinct combinations systematically and compare them; do not repeatedly polish only the incumbent.",
    "montecarlo": "Sample diverse alternatives, including unfamiliar structures and regions of the space.",
    "anneal": "Balance local moves with larger jumps. A temporarily worse candidate can be a useful stepping stone; keep the best verified result separately.",
    "gradient": "Use measured trends to choose nearby improvements, but change direction or restart elsewhere when the trend is weak.",
    "genetic": "Develop multiple alternatives, combine promising ideas and mutate structures. Preserve diversity as well as the best verified result.",
    "pareto": "Explore different trade-offs across all objectives. Keep alternatives that improve a trade-off even when they do not beat the incumbent on every metric.",
}


def policy_name(problem: Any) -> str:
    orch = problem.roles().orchestrator
    return str(getattr(orch, "dse", "") or getattr(orch, "name", "") or "adaptive")


def guidance(problem: Any, state: Any, subgoal: str | None = None, *, active: bool = True) -> str:
    selected = getattr(state.part(subgoal), "dse", "") if hasattr(state, "part") else ""
    name = policy_name(problem)
    return (f"SEARCH POLICY ({name if name in PROMPTS else 'adaptive'}): " + PROMPTS.get(name, PROMPTS["adaptive"])
            + (f" Current experiment ({selected}): {PROMPTS.get(selected, PROMPTS['adaptive'])}" if active and selected and selected != name else "")
            + " State a plausible hypothesis and what measuring this candidate will establish. Failed or temporarily worse experiments are useful evidence and remain on record. "
            "Passing correctness checks and respecting the task's contract are required; the best verified candidate remains available.")


def _quota_due(state: Any) -> bool:
    from .ledger import Kind

    quota = float(getattr(state.request, "exploration_quota", 0) or 0)
    if quota <= 0:
        return False
    rows = state.ledger.entries(Kind.SEARCH_CHOICE) if hasattr(state, "ledger") else []
    picks = [r.detail.get("pick") for r in rows] or getattr(state, "search_choices", [])
    return picks.count("explore") < quota * (len(picks) + 1)


def record_choice(state: Any, pick: str, why: str, subgoal: str | None = None) -> None:
    from .ledger import Kind

    if hasattr(state, "part"):
        state.part(subgoal).dse = pick
    if hasattr(state, "search_choices"):
        state.search_choices.append(pick)
    if hasattr(state, "ledger"):
        state.ledger.note(Kind.SEARCH_CHOICE, subgoal or "*", pick=pick, why=why)


def reserve_choice(state: Any, pick: str, why: str, subgoal: str | None = None) -> tuple[str, str]:
    # Parallel passes reserve against the same ledger before launching their work.
    with _CHOOSING:
        if _quota_due(state):
            pick, why = "explore", "the budget's exploration quota reserves this attempt for a new approach"
        record_choice(state, pick, why, subgoal)
    return pick, why


def begin(problem: Any, state: Any, subgoal: str | None) -> None:
    """Reserve one search move for an initial draft, before prototype and target prompts."""
    if state.part(subgoal).dse:
        return
    policy = policy_name(problem)
    pick = "explore" if _quota_due(state) else (policy if policy in PRESETS else "adaptive")
    pick, why = reserve_choice(state, pick, "initial draft guided by the DSE policy", subgoal)
    state.say(f"  search [{subgoal or '*'}]: {pick} -- {why}")


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


def choose(problem: Any, state: Any, standing: Any, said: str, *, consult: bool = True) -> tuple[str, str]:
    """Choose a search move; DSE names guide it, and an optional quota reserves exploration."""
    if _quota_due(state):
        return "explore", "the budget's exploration quota reserves this attempt for a new approach"
    n = stood(state)
    after = max(1, int(getattr(state.request, "explore_after", 2) or 2))
    orch = problem.roles().orchestrator
    ask = getattr(orch, "direction", None)
    if consult and callable(ask):
        lines = [f"The standing design: {standing.name}. {said}",
                 f"The standing design has stood for {n} pass(es)." if n else "The last pass moved the standing design.",
                 guidance(problem, state, active=False),
                 "improve: any reasoned improvement, local or structural; explore: a new approach, with no need to win immediately; "
                 "tune: parameters or implementation choices; finetune: small edits; variations: develop distinct alternatives; refine: use the existing ladder.",
                 "Pick the direction of the next design."]
        try:
            got = ask(problem, state, lines)
        except Exception:  # noqa: BLE001 -- an unanswered choice falls back to the rules
            got = None
        if got and got[0] in CHOICES:
            return got[0], f"the orchestrator: {got[1]}" if got[1] else "the orchestrator"
    policy = policy_name(problem)
    if policy in PRESETS and policy != "adaptive":
        return policy, f"the DSE preference is {policy}; reasoned risks remain allowed"
    mapped = {"gradient": "tune", "sweep": "variations", "montecarlo": "explore", "genetic": "variations", "pareto": "variations"}
    if policy in mapped:
        return mapped[policy], f"the {policy} DSE policy guides this attempt"
    if n < after:
        return "improve", "choose a reasoned improvement; structural changes are allowed before stagnation"
    pick = "explore" if (n - after) % 2 == 0 else "refine"
    return pick, f"the decision has stood {n} pass(es): {'a new design' if pick == 'explore' else 'its ladder again'}, in turn"
