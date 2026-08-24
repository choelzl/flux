"""The record (D412/D415): trials written as they happen; proven parts re-verified and best-so-far attempts resumed on the next pass -- state that is not in the record does not exist."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from .observe import _phase
from .types import Candidate, LoopState, StageNames, Verdict

if TYPE_CHECKING:  # pragma: no cover
    from .problem import Problem

__all__ = ["_record_trial", "_reload", "prototype_digest"]


def prototype_digest(code: str) -> str:
    """How a transpiled design names the prototype it was made from (D504): the first 16
    hex digits of the text's SHA-256, kept in the candidate's `meta["prototype_sha"]`."""
    return hashlib.sha256(code.encode()).hexdigest()[:16]

def _record_trial(state: LoopState, cand: Candidate | None, subgoal: str | None,
                  verdict: Verdict | None, *, admitted: bool = False,
                  error: str | None = None) -> None:
    if state.records is None:
        return
    doc = (cand.to_record() if cand else {"name": subgoal or "?", "artifact": ""})
    doc["subgoal"] = subgoal
    if verdict is not None and not admitted:
        doc["score"] = float(verdict.score)
        doc["why"] = (verdict.why or "")[:400]
    try:
        state.records.trial(
            doc, f"{subgoal or 'goal'}:{doc['name']}",
            stage=StageNames.ADMIT if admitted else StageNames.GATE,
            strategy="loop",
            metrics=({"score": float(verdict.score)} if verdict is not None else None),
            error=(None if admitted else (error or (verdict.why[:300] if verdict else "refused"))),
            analytic=False, evaluator=f"{state.request.params.get('evaluator', 'loop')}@gate")
    except Exception:  # noqa: BLE001
        pass


def _reload(problem: Problem, state: LoopState, parts: list[str] | None = None) -> None:
    """Proven sub-goals rejoin frozen (re-verified), unproven ones resume from their
    best refused attempt -- state that is not in the record does not exist. `parts`
    is what this pass divides into (the decompose node's answer, D431); None reads the
    problem's declared subgoals."""
    if state.records is None or not getattr(state.records, "resumed", False):
        return
    goals = set(problem.subgoals() if parts is None else parts) or {None}
    # REGENERATE (D476): a part named here is drafted again by today's generator -- its
    # admitted design, best attempt and prototype on record are history the read-back may
    # cite, not state to resume from. "*" means every part. The record keeps every row.
    again = set(state.request.regenerate or ())
    if again:
        for key in sorted(g or "*" for g in goals):
            if "*" in again or key in again:
                state.say(f"reload: {key} is being regenerated; its record is history, not a "
                          "starting point")
    def _fresh(key: str) -> bool:
        return "*" in again or key in again
    proven: dict[str, Candidate] = {}
    best: dict[str, tuple[float, Candidate, str]] = {}
    verified: dict[str, str] = {}                   # the VERIFIED prototype per part (D495): the last
    by_digest: dict[str, dict[str, str]] = {}       # ... and every one, by its digest (D504)
    try:
        for t in state.records.store.trials(state.records.campaign_id):
            if t.stage == StageNames.PROTOTYPE:
                # A prototype is Python and its score is the prototype's, not the
                # target's: reloaded below, on its own. Counted here it was "the best
                # design so far" at 0 over for a part whose best RTL was 27,291 over,
                # and the RTL turns resumed by patching Python (D471).
                c = t.candidate or {}
                if t.status == "ok" and (c.get("meta") or {}).get("kind") == "prototype" and c.get("artifact"):
                    verified[c.get("subgoal") or "*"] = c["artifact"]
                    by_digest.setdefault(c.get("subgoal") or "*", {})[prototype_digest(c["artifact"])] = c["artifact"]
                continue
            mapped = problem.from_record(dict(t.candidate or {}))
            if mapped is None:
                continue
            cand, sc, why = mapped
            if (cand.meta or {}).get("kind") == "prototype":
                continue
            sg = cand.subgoal
            if sg not in goals and not (sg is None and None in goals):
                continue
            key = sg or "*"
            if _fresh(key):
                continue
            if t.stage == StageNames.ADMIT and t.status == "ok":
                proven[key] = cand
            elif sc is not None:
                if key not in best or sc < best[key][0]:
                    best[key] = (sc, cand, why or f"score {sc:g}")
    except Exception:  # noqa: BLE001
        return
    for key, cand in proven.items():
        sg = None if key == "*" else key
        with _phase(f"records: re-verify {key}", why=cand.name) as out:
            out["design"] = f"{cand.name}: {len(cand.artifact or '')} chars of {problem.name} artifact"
            # A TRANSPILED design is re-spelled by today's transpiler from its verified
            # prototype (D495): the prototype is the design, the RTL is derived from it, and
            # a transpiler fix (sigmoid's `~2'(v)`, which yosys read as a cast of size ~2)
            # must reach a design admitted before the fix. The record's text stays as the
            # fallback when the re-spelling does not pass today's judge.
            respelled = None
            # ITS prototype (D504): the one the RTL was transpiled from, by digest -- the LAST
            # verified prototype of a part is not always the admitted design's (a redesign
            # whose alternative passed 0 over but was not faster is on record as verified too,
            # and re-spelling from it would have swapped the admitted design for the slower
            # one at the relaunch); the last one stands in for designs admitted before digests
            want = str((cand.meta or {}).get("prototype_sha") or "")
            source = by_digest.get(key, {}).get(want) if want else None
            if want and source is None and key in verified:
                out["prototype"] = f"the prototype of digest {want} is not on record as verified; the last verified one stands in"
            source = source or verified.get(key)
            if (cand.meta or {}).get("transpiled") and source and callable(getattr(problem, "transpile", None)):
                k = int((cand.meta or {}).get("pipeline", 0) or 0)     # its register count stays (D496)
                try:
                    respelled = problem.transpile(source, sg, state, **({"pipeline": k} if k else {}))
                except Exception as exc:  # noqa: BLE001
                    out["respelled"] = f"today's transpiler could not spell the verified prototype ({exc!s:.120}); the record's RTL stands"
                if respelled is not None and (respelled.artifact or "") != (cand.artifact or ""):
                    out["respelled"] = (f"by today's transpiler from the verified prototype: "
                                        f"{(respelled.artifact or '').count(chr(10))} lines (the record's: "
                                        f"{(cand.artifact or '').count(chr(10))})")
                    cand = Candidate(respelled.name, respelled.artifact, knobs=dict(cand.knobs or {}),
                                     meta={**(cand.meta or {}), **(respelled.meta or {})}, subgoal=cand.subgoal)
                elif respelled is not None:
                    respelled = None                     # the same text: nothing to say
            try:
                built = problem.build(cand, sg, state)
                v = problem.judge(built, cand, sg, state)
            except Exception as exc:  # noqa: BLE001
                out["verdict"] = f"could not be re-verified: {exc!s:.200}"
                continue
            out["verdict"] = ("still admitted: " if v.ok else "NO LONGER PASSES: ") + (v.why or "0 over")[:2000]
        if v.ok:
            state.admitted[key] = cand
            if source:
                state.prototypes[key] = source             # the design behind the RTL (D496: the
            state.say(f"reload: {key} re-verified, kept frozen"       # push for fmax reads it)
                      + (" (re-spelled by today's transpiler)" if respelled is not None else ""))
        elif respelled is not None:
            # today's spelling fails today's judge: the record's RTL, re-verified on its own
            orig = proven[key]
            try:
                v2 = problem.judge(problem.build(orig, sg, state), orig, sg, state)
            except Exception:  # noqa: BLE001
                v2 = Verdict(False, float("inf"), "could not be re-verified")
            if v2.ok:
                state.admitted[key] = orig
                state.say(f"reload: {key} re-verified from the record's RTL (today's re-spelling did not pass: {(v.why or '')[:80]})")
    for key, entry in best.items():
        if key not in state.admitted:
            state.best[key] = entry
            state.say(f"reload: {key} resumes from its best design so far (score {entry[0]:g})")
    try:
        # THE BEST REFUSED PROTOTYPE per part (D480): the stage resumes from it instead of a
        # blank page. The live run reached 512 over on recip and threw it away at the next
        # pass; RTL has had this since D415, prototypes did not.
        for t in state.records.store.trials(state.records.campaign_id):
            c = t.candidate or {}
            if t.stage != StageNames.PROTOTYPE or (c.get("meta") or {}).get("kind") != "prototype":
                continue
            key = c.get("subgoal") or "*"
            if key in state.admitted or key in state.prototypes:
                continue
            sc = c.get("score")
            if t.status == "ok" or not isinstance(sc, (int, float)) or not (0 < sc < float("inf")):
                continue
            # a REGENERATED part (D476) drops its admitted design and its verified prototype,
            # not the model's own best refused attempt: that is the flow's work, and the seed
            if key not in state.proto_best or sc < state.proto_best[key][0]:
                state.proto_best[key] = (float(sc), str(c.get("artifact") or ""), str(c.get("why") or ""))
        for key, (sc, _code, _why) in sorted(state.proto_best.items()):
            state.say(f"reload: {key} resumes its prototype from the best on record ({sc:g} over)")
    except Exception:  # noqa: BLE001
        pass
    try:
        for t in state.records.store.trials(state.records.campaign_id, status="ok"):
            c = t.candidate or {}
            if t.stage == StageNames.PROTOTYPE and c.get("artifact") and (c.get("meta") or {}).get("kind") == "prototype":
                key = c.get("subgoal") or "*"
                if key in state.admitted or key in state.prototypes or _fresh(key):
                    continue
                # RE-VERIFIED, like an admitted part (D468): a prototype the record blessed
                # under an older check is not verified under today's. The live NLU run's two
                # "verified" prototypes were the reference function in a costume, and a
                # resume that trusted them would have fed them to every prompt again.
                sg = None if key == "*" else key
                with _phase(f"records: re-verify prototype {key}", why="0 over, today's rules") as out:
                    out["prototype"] = (c["artifact"] or "")[:6000]
                    try:
                        v = problem.prototype_check(c["artifact"], sg, state)
                    except Exception as exc:  # noqa: BLE001
                        out["verdict"] = f"could not be re-checked: {exc!s:.200}"
                        state.say(f"reload: the prototype on record for {key} could not be "
                                  f"re-checked ({exc!s:.80}); not reused")
                        continue
                    out["verdict"] = ("verified again, 0 over" if v.ok else "NO LONGER PASSES") + (
                        f": {v.why[:1500]}" if v.why else "")
                if v.ok:
                    state.prototypes[key] = c["artifact"]
                    state.say(f"reload: {key} has a verified prototype (0 over) on record")
                else:
                    state.say(f"reload: the prototype on record for {key} no longer passes "
                              f"({v.why[:100]}); the part starts without one")
    except Exception:  # noqa: BLE001
        pass
