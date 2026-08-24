"""What the loop shows whoever watches (D418): the timing tree's phases, the standings and the mentor tab's sections, through flux_profile."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .types import LoopState, StageNames

if TYPE_CHECKING:  # pragma: no cover
    from .problem import Problem

__all__ = ["_phase", "_publish", "_publish_mentor"]

def _phase(name: str, why: str = "", **params: Any):
    from flux_profile import phase

    return phase(name, why=why, **params)


def _publish_mentor(problem: Problem, state: LoopState) -> None:
    """The mentor tab's sections (D418m): the problem's own (knowledge sheet, library,
    read-back) plus what the record holds -- conclusions, refusals, operator notes,
    what is proven and the best refused attempt per part."""
    try:
        from flux_profile import publish
    except Exception:  # noqa: BLE001
        return
    sections: list[dict[str, str]] = []
    try:
        for title, text in problem.mentor_sections(state):
            if text and text.strip():
                sections.append({"title": title, "text": text})
    except Exception as exc:  # noqa: BLE001
        sections.append({"title": "knowledge", "text": f"(unavailable: {exc})"})
    notes = [getattr(n, "text", str(n)) for n in state.human_notes]
    if notes:
        sections.append({"title": "operator notes", "text": "\n".join(f"* {n}" for n in notes)})
    if state.admitted or state.best:
        lines = [f"PROVEN {k}: {c.name}" for k, c in sorted(state.admitted.items())]
        lines += [f"BEST {k}: score {v[0]:g} -- {v[1].name}\n    {v[2][:300]}"
                  for k, v in sorted(state.best.items()) if k not in state.admitted]
        sections.append({"title": "record: proven and best", "text": "\n".join(lines)})
    if state.records is not None:
        try:
            concl = state.records.conclusions(limit=5)
            if concl:
                sections.append({"title": "record: conclusions",
                                 "text": "\n".join(
                                     f"* {c.get('decision', '?')} ({c.get('decided_by', '')})"
                                     for c in concl)})
        except Exception:  # noqa: BLE001
            pass
        try:
            crit = state.records.recall("critique")[-8:]
            if crit:
                sections.append({"title": "record: critiques",
                                 "text": "\n".join(f"* [{c.get('kind', '?')}] {c.get('subject', '')}: "
                                                    f"{str(c.get('why', ''))[:200]}" for c in crit)})
        except Exception:  # noqa: BLE001
            pass
        try:
            ref = state.records.refusals(stage=StageNames.GATE, limit=12)
            if ref:
                sections.append({"title": "record: refusals (gate)",
                                 "text": "\n".join(f"* {c.get('name', '?')} -- {why[:200]}"
                                                    for c, why in ref)})
        except Exception:  # noqa: BLE001
            pass
    publish("mentor", {"sections": sections})


def refresh_standings(state: LoopState, at: str) -> None:
    """Re-publish the standings with the arguments of the last `_publish` (D487: the part
    being TRIED shows its prototype's best as the attempts land)."""
    args = getattr(state, "_standings_args", None)
    if args:
        problem, todo, goals, searching = args
        _publish(problem, state, todo, goals, at, searching=searching)


def _publish(problem: Problem, state: LoopState, todo: list, goals: list[str],
             at: str, *, searching: bool = False) -> None:
    """The loop's live standings, for whoever observes (D418l): the TUI's results
    tab shows them while the run is still going, not only when the report lands.

    A SEARCH-path pass (D446) has no parts to stand: what it has done so far is how many
    candidates the gate admitted, how many it refused and how many are measured, so it
    publishes those and no part rows -- "not yet tried" against a study that has already
    screened forty-eight designs would be a lie the panel tells for the whole run."""
    try:
        from flux_profile import BEST_SO_FAR, NOT_YET_TRIED, PROVEN, TRYING, publish
    except Exception:  # noqa: BLE001
        return
    try:
        state._standings_args = (problem, todo, goals, searching)   # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass
    trying = getattr(state, "_trying", None)          # (key, method) of the part under way
    if searching:
        publish("standings", {
            "problem": problem.name, "at": at, "step": state.step,
            "steps": state.request.steps, "judged": state.judged, "searching": True,
            "parts": [], "proven": 0, "parts_total": 0,
            "gated": len(state.pool), "refused": len(state.refused),
            "measured": len(state.scored),
        })
        return
    parts = []
    cap = 120_000                       # per text: an RTL of two thousand lines fits
    try:
        objective = dict(problem.objectives(state) or {})       # D497
    except Exception:  # noqa: BLE001
        objective = {}
    numbers = objective.pop("parts", {}) or {}
    for g in (goals or [None]):
        key = g or "*"
        row: dict = {"part": g or problem.name}
        if numbers.get(key):
            row["numbers"] = str(numbers[key])[:300]
        if key in state.admitted:
            cand = state.admitted[key]
            row.update({"state": PROVEN, "name": cand.name, "artifact": (cand.artifact or "")[:cap],
                        "report": "admitted: 0 over on every input"})
        elif key in state.best:
            sc, cand, why = state.best[key]
            row.update({"state": BEST_SO_FAR, "score": sc, "name": cand.name,
                        "artifact": (cand.artifact or "")[:cap], "report": (why or "")[:cap]})
        else:
            row["state"] = NOT_YET_TRIED
        if trying and trying[0] == key and key not in state.admitted:
            # the part under way (D487, Cedric: "add 'trying' to the result table"): its
            # prototype's best so far, live, and the plan it is following
            row["state"] = TRYING
            row["via"] = (trying[1] or "")[:80]
            pb = (getattr(state, "proto_best", None) or {}).get(key)
            if pb:
                row["prototype_score"] = pb[0]
        # D487 (Cedric: "clicking a result should show its artifact"): the prototype the
        # part stands on -- verified, or the best refused one and why -- and its tests
        proto = (state.prototypes or {}).get(key)
        if proto:
            row["prototype"] = proto[:cap]
        elif key in (getattr(state, "proto_best", None) or {}):
            psc, pcode, pwhy = state.proto_best[key]
            row["prototype"] = pcode[:cap]
            row["prototype_score"] = psc
            row.setdefault("report", (pwhy or "")[:cap])
        tests = getattr(problem, "describe_tests", None)
        if callable(tests):
            try:
                row["tests"] = str(tests(g))[:cap]
            except Exception:  # noqa: BLE001
                pass
        parts.append(row)
    publish("standings", {
        "problem": problem.name, "at": at, "step": state.step,
        "steps": state.request.steps, "judged": state.judged,
        "proven": len(state.admitted), "parts_total": len(goals) if goals else 1,
        "parts": parts, "measured": len(state.scored),
        "pool": len(state.pool), "refused": len(state.refused),
        "objective": {k: str(v)[:600] for k, v in objective.items()},
    })
