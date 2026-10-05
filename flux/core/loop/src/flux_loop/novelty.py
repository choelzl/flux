"""Away from the first design (D839). A loop whose every draft starts from its best design, told
to edit it, stays near it: the generator sees one design and one failure, never what was tried,
and may send a design already measured again. Three things here:

- `tried_block`: the designs measured for a part -- the best first, then the latest -- with their
  numbers and, where the generator said it, what each one was; a fresh draft reads them.
- `twin`: a drafted design that is one already measured (its text, spacing aside) -- refused
  before it is built, told which one and to write a different one.
- `explore_brief`: an exploring pass (the campaign at rest, D593) asks for a NEW design, with the
  standing one shown to beat, not to edit.
"""

from __future__ import annotations

import hashlib
from typing import Any

__all__ = ["explore_brief", "tried_block", "twin", "twin_said"]


def _digest(text: str) -> str:
    return hashlib.sha256(" ".join((text or "").split()).encode()).hexdigest()[:20]


def _measured(state: Any, subgoal: str | None) -> list[Any]:
    """The part's measured designs, the deepest stage each reached, in the order measured."""
    order: dict[str, int] = {}
    by: dict[str, Any] = {}
    earlier = list(state.__dict__.get("_history") or []) if hasattr(state, "__dict__") else []   # D845: the record's
    for i, s in enumerate(earlier + list(getattr(state, "scored", None) or [])):
        c = s.candidate
        if (c.subgoal or None) != (subgoal or None) or not (c.artifact or "").strip():
            continue
        d = _digest(c.artifact)
        order.setdefault(d, i)
        order[d] = i                          # the latest measurement places it
        by[d] = s
    return [by[d] for d in sorted(by, key=lambda d: order[d])]


def twin(state: Any, cand: Any) -> Any | None:
    """The measured design `cand` repeats (the same text, spacing aside), or None."""
    if not (getattr(cand, "artifact", "") or "").strip():
        return None
    d = _digest(cand.artifact)
    return next((s for s in reversed(_measured(state, cand.subgoal)) if _digest(s.candidate.artifact) == d), None)


def _numbers(metrics: dict[str, Any]) -> str:
    return ", ".join(f"{k} {v:.4g}" for k, v in metrics.items() if isinstance(v, (int, float)) and not isinstance(v, bool))


def twin_said(s: Any) -> str:
    return (f"This design is {s.candidate.name} again, already measured ({_numbers(s.metrics) or 'no numbers'} "
            f"on the {s.stage} stage): measuring it again teaches nothing. Write a DIFFERENT design -- another "
            "structure, algorithm or set of choices, not the same text again.")


def tried_block(problem: Any, state: Any, subgoal: str | None, limit: int = 8) -> str:
    """What was tried for the part: the best by the objectives, then the latest, `limit` in all."""
    try:
        from .records import history

        history(problem, state)                  # D845: the record's designs too
    except Exception:  # noqa: BLE001 -- this pass's alone
        pass
    rows = _measured(state, subgoal)
    if not rows:
        return ""
    best = None
    try:
        objs = problem.objectives()
        stages = list(problem.stages() or [])
        deep = max((s.stage for s in rows), key=lambda st: stages.index(st) if st in stages else -1)
        best, _why = objs.decide([s for s in rows if s.stage == deep], stages) if objs else (None, "")
    except Exception:  # noqa: BLE001 -- the list without a best
        best = None
    shown = ([best] if best is not None else []) + [s for s in reversed(rows) if s is not best][: limit - (best is not None)]
    lines = []
    for s in shown:
        what = str((s.candidate.meta or {}).get("why") or "").strip().splitlines()
        lines.append(f"- {s.candidate.name}{' (the best so far)' if s is best else ''}: {_numbers(s.metrics) or 'no numbers'}"
                     f" on the {s.stage} stage" + (f" -- {what[0][:160]}" if what else ""))
    more = len(rows) - len(shown)
    return (f"TRIED SO FAR ({len(rows)} design(s) measured for this part" + (f"; {more} older not listed" if more > 0 else "")
            + "). Do not send one of these again; a design near the best that changes little measures little "
            "different -- a new idea is worth more than a small edit:\n" + "\n".join(lines))


def explore_brief(why: str, standing: Any, fence: str = "") -> str:
    """The exploring pass's request (D839): beat the standing design with a different one."""
    return (f"{why.strip()}\n\nWrite a NEW design that beats it -- a different structure, algorithm or set of "
            "choices, not an edit of it. Take a risk: an idea that may fail is worth more here than a small "
            "change that measures the same. It must still pass the gate.\n\n"
            f"THE STANDING DESIGN, {standing.name} (to beat; for reference only -- do not edit or resend it):\n"
            f"```{fence}\n{standing.artifact}\n```")
