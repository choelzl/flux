"""A result a critic rejected (D1040): marked, shown, never chosen."""

from __future__ import annotations

from .types import LoopState, Scored


def reject(state: LoopState, got: Scored, why: str, wrong: list[str], by: str) -> None:
    """D1040: a result rejected by a critic -- marked on its rows with why and by whom (shown, not
    hidden: the record's rejection), the values it calls wrong cleared, never chosen; remembered
    for the passes after this one and in the lessons."""
    key = got.candidate.key()
    state.rejected[key] = {"why": why, "wrong": wrong, "by": by, "name": got.name}
    for s in [got, *state.scored]:
        if s.candidate.key() == key:
            s.payload["rejected"] = why
            for m in wrong if s.stage == got.stage else ():      # what it read on that stage
                s.metrics.pop(m, None)
    state.lessons.append(f"[critique] {got.name} rejected by {by}: {why[:200]}"
                         + (f" (wrong: {', '.join(wrong)})" if wrong else ""))
    state.say(f"  {by} rejected {got.name}: {why[:200]}")
    if state.records is not None:
        try:
            state.records.reject(key, got.name, why=why, wrong=wrong, by=by)
        except Exception:  # noqa: BLE001
            pass
