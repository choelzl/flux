"""A campaign's ideas notebook: hypotheses on the record, outcomes from actual trials.

Ideas never contain design source. An idea may stay untested or have several designs and
evaluations across passes. Failed experiments remain evidence, rather than deleting the idea.
"""

from __future__ import annotations

import hashlib
import json
import math
import stat
from pathlib import Path
from typing import Any

EVENT = "idea"
#: D1112: an idea queued for testing (`{"id", "on"}`), from Results › Ideas: each of the next passes builds a design that
#: follows the oldest queued idea, until one has an evaluation after it was queued.
QUEUE = "idea-queue"
FIELDS = {
    "idea": {"type": "object", "properties": {
        "id": {"type": "string", "description": "An existing notebook idea ID, or omit for a new idea."},
        "title": {"type": "string"}, "hypothesis": {"type": "string"}, "test": {"type": "string"}},
        "description": "The hypothesis tested by this design. For a new idea include title and hypothesis."},
    "ideas": {"type": "array", "items": {"type": "object", "properties": {
        "title": {"type": "string"}, "hypothesis": {"type": "string"}, "test": {"type": "string"}},
        "required": ["title", "hypothesis"]}, "description": "Other ideas to save for later, not evaluated by this design."},
}


def _memory(state: Any) -> dict[str, dict]:
    return state.__dict__.setdefault("_ideas_memory", {})


def _record(state: Any):
    rec = getattr(state, "records", None)
    return rec if rec is not None and getattr(rec, "store", None) is not None else None


def _proposals(store: Any, campaign: str, until: str | None = None) -> dict[str, dict]:
    out = {}
    for event in store.events(campaign):
        if event["kind"] != EVENT or (until is not None and event["created_at"] > until):
            continue
        doc = event.get("detail") or {}
        if isinstance(doc, dict) and isinstance(doc.get("id"), str) and doc.get("title") and doc.get("hypothesis"):
            out.setdefault(doc["id"], {**doc, "created": event["created_at"]})
    return out


def proposals(state: Any) -> dict[str, dict]:
    rec = _record(state)
    if rec is not None:
        try:
            return {**_memory(state), **_proposals(rec.store, rec.campaign_id)}
        except Exception:  # noqa: BLE001 -- notebook availability must not stop a loop
            pass
    return _memory(state)


def _persist(state: Any, idea: dict) -> None:
    """Retry an in-memory proposal when storage recovers, without duplicating saved notes."""
    rec = _record(state)
    if rec is not None:
        try:
            if idea["id"] not in _proposals(rec.store, rec.campaign_id):
                rec.store.append_event(rec.campaign_id, EVENT, idea)
        except Exception:  # noqa: BLE001 -- keep the in-memory idea if the record cannot be written
            pass


def propose(state: Any, part: str | None, value: Any) -> str:
    """Save a hypothesis, deduplicated by its content and part; return its stable ID."""
    if not isinstance(value, dict):
        raise ValueError("an idea must be an object")
    if value.get("id"):
        idea = proposals(state).get(str(value["id"]))
        if idea is None or idea.get("part") != (part or ""):
            raise ValueError("unknown idea for this part")
        _persist(state, idea)
        return idea["id"]
    title = str(value.get("title") or "").strip()[:160]
    hypothesis = str(value.get("hypothesis") or "").strip()[:2000]
    test = str(value.get("test") or "").strip()[:1000]
    if not title or not hypothesis:
        raise ValueError("a new idea needs title and hypothesis")
    digest = hashlib.sha256(json.dumps([part or "", title, hypothesis, test]).encode()).hexdigest()[:16]
    ident = f"idea-{digest}"
    if ident not in proposals(state):
        doc = {"id": ident, "part": part or "", "title": title, "hypothesis": hypothesis, "test": test}
        _memory(state)[ident] = doc
    _persist(state, _memory(state).get(ident) or proposals(state)[ident])
    return ident


def capture(state: Any, part: str | None, payload: Any, *, inherited: list[str] = (), name: str | None = None) -> list[str]:
    """Keep future proposals and link only the selected idea to this experiment (`name`: the design's, when known)."""
    if not isinstance(payload, dict):
        return _forced(state, part, list(inherited), name)
    for idea in (payload.get("ideas") or [])[:12] if isinstance(payload.get("ideas"), list) else []:
        try:
            propose(state, part, idea)
        except ValueError:
            continue
    if payload.get("idea") is not None:
        try:
            return _forced(state, part, [propose(state, part, payload["idea"])], name)
        except ValueError as exc:
            state.say(f"  ideas: {exc}; the design is still evaluated")
    return _forced(state, part, list(inherited or state.__dict__.get("_idea_selected", {}).get(part or "*", [])), name)


def _forced(state: Any, part: str | None, ids: list[str], name: str | None = None) -> list[str]:
    """D1112: a pass testing a queued idea ties every draft to it, whatever else the draft names -- not the standing
    design it starts from (`_idea_forced_from`), which the pass handles too."""
    if name is not None and name == state.__dict__.get("_idea_forced_from"):
        return ids
    forced = state.__dict__.get("_idea_forced", {}).get(part or "*", [])
    return list(dict.fromkeys([*forced, *ids]))


def bind(state: Any, cand: Any, payload: Any = None) -> list[str]:
    """Attach notebook IDs before a draft is evaluated; metadata follows repairs and trials."""
    if not isinstance(cand.meta, dict):
        return []
    if isinstance(payload, dict):
        # Patches retain the old candidate's metadata; an explicitly new hypothesis replaces it.
        cand.meta.update({k: payload[k] for k in ("idea", "ideas") if k in payload})
    ids = capture(state, cand.subgoal, payload if payload is not None else cand.meta,
                  inherited=cand.meta.get("idea_ids") or [], name=cand.name)
    if not ids and not cand.meta.get("baseline"):
        from .novelty import _intent

        intent = _intent(cand).removeprefix("INTENT: ").strip()
        if intent != "not recorded." and len(intent) >= 8:
            ids = [propose(state, cand.subgoal, {"title": intent.splitlines()[0][:160], "hypothesis": intent})]
    if ids:
        cand.meta["idea_ids"] = ids
    return ids


def code_ids(state: Any, code: str, part: str | None = None) -> list[str]:
    key = (part or "", hashlib.sha256(code.encode()).hexdigest())
    by = state.__dict__.setdefault("_idea_code", {})
    if key not in by and not state.__dict__.get("_idea_codes_loaded"):
        rec = _record(state)
        if rec is not None:
            try:
                for trial in rec.store.trials(rec.campaign_id):
                    cand = trial.candidate or {}
                    ids = (cand.get("meta") or {}).get("idea_ids") or []
                    if trial.stage == "prototype" and ids and cand.get("artifact"):
                        by.setdefault((cand.get("subgoal") or "", hashlib.sha256(cand["artifact"].encode()).hexdigest()), ids)
            except Exception:  # noqa: BLE001
                pass
        state.__dict__["_idea_codes_loaded"] = True
    return list(by.get(key, []))


def bind_code(state: Any, part: str | None, code: str, payload: Any, previous: str = "") -> list[str]:
    ids = capture(state, part, payload, inherited=code_ids(state, previous, part) if previous else [])
    if ids:
        state.__dict__.setdefault("_idea_code", {})[(part or "", hashlib.sha256(code.encode()).hexdigest())] = ids
    return ids


def record_failure(state: Any, cand: Any, stage: str, error: str, score: float | None = None) -> None:
    """Keep failed inner-loop attempts too, before a repair replaces the draft -- D1094: every one, not
    only those carrying ideas: a draft its check refused is a design the page lists, with why."""
    rec = _record(state)
    if rec is None:
        return
    from .provenance import stamp

    doc = cand.to_record()
    doc["meta"] = {**cand.meta, "provenance": stamp()}
    try:
        rec.trial(doc, cand.name, stage=stage, strategy="loop", error=error[:4000],
                  metrics={"score": score} if score is not None else None)
    except Exception:  # noqa: BLE001 -- optional memory must never stop evaluation
        pass


def sidecar(artifact: Path) -> dict:
    """Optional agent proposals, confined to this turn's file; malformed notes are advisory."""
    path = artifact.with_name(artifact.name + ".ideas.json")
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > 65536:
            return {}
        got = json.loads(path.read_text())
        return got if isinstance(got, dict) else {}
    except (OSError, ValueError):
        return {}


def queued(store: Any, campaign: str) -> list[str]:
    """D1112: the ideas queued for testing, oldest first: each one's last queue event turned it on, and no trial carrying
    it was recorded since (an evaluation of any outcome clears it; queue it again to test it again)."""
    since: dict[str, str] = {}
    order: list[str] = []
    for e in store.events(campaign):
        doc = e.get("detail") or {}
        if e.get("kind") != QUEUE or not isinstance(doc.get("id"), str):
            continue
        order = [x for x in order if x != doc["id"]]
        if doc.get("on"):
            since[doc["id"]] = e["created_at"]
            order.append(doc["id"])
    if not order:
        return []
    last: dict[str, str] = {}
    for trial in store.trials(campaign, artifacts=False):
        if trial.status in ("running",):
            continue
        for ident in ((trial.candidate or {}).get("meta") or {}).get("idea_ids") or []:
            if ident in since and trial.created_at > last.get(ident, ""):
                last[ident] = trial.created_at
    return [i for i in order if last.get(i, "") <= since[i]]


def set_queued(store: Any, campaign: str, ident: str, on: bool) -> list[str]:
    """Queue an idea for testing, or take it off the queue (D1112); the queue after."""
    if on and ident not in _proposals(store, campaign):
        raise ValueError(f"no idea {ident} in this campaign")
    store.append_event(campaign, QUEUE, {"id": ident, "on": bool(on)})
    return queued(store, campaign)


def next_queued(state: Any) -> dict | None:
    """The oldest idea queued for testing, its proposal (id, part, title, hypothesis, test); None when none is."""
    rec = _record(state)
    if rec is None:
        return None
    try:
        ids = queued(rec.store, rec.campaign_id)
        return _proposals(rec.store, rec.campaign_id).get(ids[0]) if ids else None
    except Exception:  # noqa: BLE001 -- a queue that cannot be read is no queue
        return None


def notebook(store: Any, campaign: str, *, until: str | None = None) -> dict:
    """Read proposals and their real evaluation rows. An ok check is not a measured win."""
    ideas = _proposals(store, campaign, until)
    for idea in ideas.values():
        idea["evaluations"] = []
        idea["status"] = "proposed"
    for trial in store.trials(campaign):
        if until is not None and trial.created_at > until:
            continue
        meta = (trial.candidate or {}).get("meta") or {}
        metrics = {k: e.value for k, e in (trial.result.metrics.items() if trial.result else [])
                   if isinstance(e.value, (float, int)) and math.isfinite(e.value)}
        for ident in meta.get("idea_ids") or []:
            if ident not in ideas:
                continue
            idea = ideas[ident]
            idea["evaluations"].append({"seq": trial.seq, "design": trial.candidate.get("name") or "",
                "stage": trial.stage, "status": trial.status, "metrics": metrics, "error": trial.error or "",
                "pass": (meta.get("provenance") or {}).get("pass"), "at": trial.created_at})
    for idea in ideas.values():
        rows = idea["evaluations"]
        if any(r["status"] == "ok" and r["stage"] not in ("prototype", "gate", "admit") and r["metrics"] for r in rows):
            idea["status"] = "measured"
        elif any(r["status"] == "ok" for r in rows):
            idea["status"] = "checked"
        elif rows:
            idea["status"] = "interrupted" if rows[-1]["status"] in ("running", "interrupted") else "failed"
    queue = queued(store, campaign) if until is None else []
    for idea in ideas.values():
        idea["queued"] = queue.index(idea["id"]) + 1 if idea["id"] in queue else None
    return {"campaign": campaign, "ideas": list(ideas.values()), "queue": queue}


def context(state: Any, part: str | None, limit: int = 8) -> str:
    """Bounded source-free memory for the next turn: untested ideas and recent evidence."""
    rec = _record(state)
    try:
        rows = notebook(rec.store, rec.campaign_id)["ideas"] if rec else list(_memory(state).values())
        saved = {row["id"] for row in rows}
        rows.extend(row for ident, row in _memory(state).items() if ident not in saved)
    except Exception:  # noqa: BLE001
        rows = list(_memory(state).values())
    rows = [r for r in rows if r.get("part") == (part or "")]
    # Reserve room for untested ideas while retaining the latest experiments.
    pending = [r for r in rows if r.get("status", "proposed") == "proposed"][-max(1, limit // 2):]
    recent = [r for r in reversed(rows) if r not in pending][:max(0, limit - len(pending))]
    shown = pending + recent
    if not shown:
        return ""
    lines = ["IDEAS NOTEBOOK (hypotheses and recorded evidence across passes; measured does not mean better):"]
    for row in shown:
        lines.append(f"- {row['id']}: {row['title']} [{row.get('status', 'proposed')}]\n  {row['hypothesis'][:500]}")
        outcomes = row.get("evaluations", [])
        failed = next((r for r in reversed(outcomes) if r["status"] != "ok"), None)
        evidence = ([failed] if failed is not None else []) + [r for r in outcomes[-2:] if r is not failed]
        for outcome in evidence:
            numbers = ", ".join(f"{k}={v:.4g}" for k, v in outcome["metrics"].items())[:400]
            lines.append(f"  {outcome['design']} / {outcome['stage']}: {outcome['status']} {numbers} {outcome['error'][:160]}".rstrip())
    return "\n".join(lines)


def instructions(artifact: Path) -> str:
    return (f"IDEAS. Read the ideas notebook before repeating an experiment. You may save the tested hypothesis "
            f"and ideas for later in `{artifact.name}.ideas.json` beside your artifact: "
            '{"idea":{"title":"...","hypothesis":"...","test":"what to measure"},'
            '"ideas":[{"title":"future alternative","hypothesis":"..."}]}. '
            'To revisit an existing idea use "idea":{"id":"idea-..."}. The loop records actual checks and '
            "measurements; do not invent results. Notes are optional and never replace the artifact.")
