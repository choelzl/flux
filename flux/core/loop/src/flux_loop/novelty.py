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
import re
from contextlib import contextmanager
from typing import Any

__all__ = ["explore_brief", "fresh_context", "tried_block", "twin", "twin_said"]


@contextmanager
def fresh_context(state: Any, subgoal: str | None):
    """Draft an independent alternative without seeding it with an earlier design or session.

    Repairs within this experiment still see their own draft. Retain earlier verified designs
    and refused prototypes afterward, including when generation fails or is interrupted.
    """
    key = subgoal or "*"
    best = state.best.pop(key, None)
    proto = state.prototypes.pop(key, None)
    seed = state.proto_best.pop(key, None)
    state.part(subgoal).sessions.clear()
    state.__dict__.get("_idea_selected", {}).pop(key, None)
    try:
        yield
    finally:
        if best is not None:
            state.best[key] = best
        if proto is not None and key not in state.prototypes:
            state.prototypes[key] = proto
        if seed is not None:
            state.proto_best[key] = min(seed, state.proto_best.get(key, seed), key=lambda s: s[0])


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


def _intent(standing: Any) -> str:
    """Only an INTENT section in the leading comments/docstring, never implementation text."""
    lines: list[str] = []
    block = ""
    for raw in standing.artifact.splitlines():
        text = raw.strip()
        if not text:
            if lines:
                break
            continue
        if text.startswith(('"""', "'''", "/*")) and not block:
            block = text[:3] if text[0] != "/" else "*/"
            text = text[3:] if text[0] != "/" else text[2:]
        elif not block and not re.match(r"^(#|//|--|\*)", text):
            break
        if block and block in text:
            text = text.split(block, 1)[0]
            closing = True
        else:
            closing = False
        text = re.sub(r"^(#|//|--|\*)\s*", "", text).strip()
        head = re.match(r"INTENT\b\s*:?\s*(.*)", text, re.I)
        if head:
            lines.append("INTENT: " + head[1])
        elif lines:
            if re.match(r"[A-Z][A-Z _-]+:", text):
                break
            if text:
                lines.append(text)
        if closing:
            if lines:
                break
            block = ""
    if lines:
        return "\n".join(lines)
    meta = standing.meta or {}
    summary = str(meta.get("intent") or meta.get("why") or "").strip()
    return f"INTENT: {summary}" if summary else "INTENT: not recorded."


def explore_brief(why: str, standing: Any, state: Any = None, *, variations: bool = False) -> str:
    """Explore or vary a design, with incumbent intent and measurements only."""
    rows = _measured(state, standing.subgoal) if state is not None else []
    measured = next((s for s in reversed(rows) if _digest(s.candidate.artifact) == _digest(standing.artifact)), None)
    numbers = (f"MEASURED ({measured.stage}): {_numbers(measured.metrics) or 'no numbers'}" if measured is not None
               else "MEASURED: no recorded numbers available.")
    directive = ("Write a DISTINCT alternative to the promising approaches. Use their intent and measured "
                 "trade-offs as evidence, not their implementation as a starting point. Develop your own "
                 "structure and choices; do not reproduce or patch an earlier design. It must still pass the gate."
                 if variations else
                 "Write a NEW design that beats it -- a different structure, algorithm or set of "
            "choices, not an edit of it. Take a risk: an idea that may fail is worth more here than a small "
            "change that measures the same. It must still pass the gate.")
    return (f"{why.strip()}\n\n{directive}\n\n"
            f"THE STANDING DESIGN, {standing.name} (to beat; do not edit or resend it):\n"
            f"{_intent(standing)}\n{numbers}")
