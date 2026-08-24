"""The record (D412/D415): trials written as they happen; proven parts re-verified and best-so-far attempts resumed on the next pass -- state that is not in the record does not exist."""

from __future__ import annotations

import hashlib
from typing import Any, TYPE_CHECKING

from .observe import _phase
from .provenance import stamp
from .types import Candidate, LoopState, StageNames, Verdict

if TYPE_CHECKING:  # pragma: no cover
    from .problem import Problem

__all__ = ["_record_trial", "_reload", "prototype_digest"]


def prototype_digest(code: str) -> str:
    """How a transpiled design names the prototype it was made from (D504): the first 16
    hex digits of the text's SHA-256, kept in the candidate's `meta["prototype_sha"]`."""
    return hashlib.sha256(code.encode()).hexdigest()[:16]

REASON_CHARS = 4000     # D507: how much of a refusal's reason the record keeps


def _prompt_sha(state: LoopState) -> str | None:
    """The digest of the prompt that made this row: the calling thread's own turn when a part
    was drafted on a worker thread (D569), else the last turn's."""
    import threading

    return state.prompt_sha_by_thread.get(threading.get_ident()) or state.last_prompt_sha


def _record_trial(state: LoopState, cand: Candidate | None, subgoal: str | None,
                  verdict: Verdict | None, *, admitted: bool = False,
                  error: str | None = None) -> None:
    if state.records is None:
        return
    doc = (cand.to_record() if cand else {"name": subgoal or "?", "artifact": ""})
    doc["subgoal"] = subgoal
    # D510: what made the row -- and, on an admitted design, the versions of the transpiler
    # and the judge that made and passed it, so a reload knows whether to trust it
    meta = dict(doc.get("meta") or {})
    meta["provenance"] = stamp(seconds=(verdict.seconds if verdict is not None and verdict.seconds else None),
                               prompt=_prompt_sha(state))
    if admitted and state.versions:
        meta["versions"] = dict(state.versions)
    doc["meta"] = meta
    if verdict is not None and not admitted:
        doc["score"] = float(verdict.score)
        # D507: the reason WHOLE (to a page) -- the exhaustive gate's counterexamples, region by
        # region and worst cases with raw bits, sat past 400 chars and never reached the record
        doc["why"] = (verdict.why or "")[:REASON_CHARS]
    try:
        state.records.trial(
            doc, f"{subgoal or 'goal'}:{doc['name']}",
            stage=StageNames.ADMIT if admitted else StageNames.GATE,
            strategy="loop",
            metrics=({"score": float(verdict.score)} if verdict is not None else None),
            error=(None if admitted else (error or (verdict.why[:REASON_CHARS] if verdict else "refused"))),
            wall_s=(verdict.seconds if verdict is not None else 0.0),
            analytic=False, evaluator=f"{state.request.params.get('evaluator', 'loop')}@gate")
    except Exception:  # noqa: BLE001
        pass


def shortlist_of(problem: Problem, options: list[Candidate], stage: str | None, numbers_of, standing: Candidate | None,
                 limit: int = 6) -> list[dict[str, Any]]:
    """A part's admitted designs on record as a shortlist (D528): one entry per distinct
    artifact, best first by the objectives' one rule on `stage`'s numbers (a design never
    measured there comes last, oldest first), at most `limit`; each entry says its name, the
    artifact's digest, its numbers, its register count, its prototype's digest and whether
    it is the design that stands."""
    import functools

    objs = problem.objectives()
    stages = list(problem.stages() or [])
    seen: dict[str, Candidate] = {}
    for c in options:                                  # the last row of each artifact
        seen[prototype_digest(c.artifact or "")] = c
    rows = [(c, numbers_of(c, stage)) for c in seen.values()]

    def cmp(a, b) -> int:
        ma, mb = a[1], b[1]
        if ma and not mb:
            return -1
        if mb and not ma:
            return 1
        if not ma and not mb:
            return 0
        if objs.better(ma, mb, stage, stages) and not objs.better(mb, ma, stage, stages):
            return -1
        if objs.better(mb, ma, stage, stages) and not objs.better(ma, mb, stage, stages):
            return 1
        return 0

    rows.sort(key=functools.cmp_to_key(cmp))
    stands = prototype_digest((standing.artifact or "") if standing is not None else "")
    out = []
    for c, m in rows[:limit]:
        dg = prototype_digest(c.artifact or "")
        out.append({"name": c.name, "digest": dg, "metrics": dict(m or {}), "stage": stage,
                    "pipeline": int((c.meta or {}).get("pipeline", 0) or 0),
                    "prototype_sha": str((c.meta or {}).get("prototype_sha") or ""),
                    "standing": dg == stands})
    return out


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
    admits: dict[str, list[Candidate]] = {}         # every admitted design per part, oldest first
    measured: dict[str, dict[str, dict[str, float]]] = {}   # stage -> artifact digest -> its numbers
    stages_all = list(problem.stages() or [])
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
            if t.stage in stages_all and t.status == "ok" and t.result is not None:
                # a part measured ALONE is recorded without its part name (the wrap's own
                # candidate); the artifact says which design it was
                try:
                    measured.setdefault(t.stage, {})[prototype_digest(cand.artifact or "")] = {
                        k: float(e.value) for k, e in t.result.metrics.items()}
                except Exception:  # noqa: BLE001
                    pass
            sg = cand.subgoal
            if sg not in goals and not (sg is None and None in goals):
                continue
            key = sg or "*"
            if _fresh(key):
                continue
            if t.stage == StageNames.ADMIT and t.status == "ok":
                proven[key] = cand
                admits.setdefault(key, []).append(cand)
            elif sc is not None:
                if key not in best or sc < best[key][0]:
                    best[key] = (sc, cand, why or f"score {sc:g}")
    except Exception:  # noqa: BLE001
        return
    def numbers_of(c: Candidate, stage: str | None) -> dict[str, float] | None:
        return measured.get(stage or "", {}).get(prototype_digest(c.artifact or ""))

    for key, options in admits.items():
        # THE BEST MEASURED design of a part stands (D504), not the last admitted: the numbers
        # decide which of two bit-exact designs is better, and the record holds them -- from
        # the DEEPEST stage on which two of the part's designs were measured (D506: the screen
        # ranked gelu's designs the wrong way round; placement is the fidelity that decides).
        # Alone measurements land on the deepest stage by rule (D507), so this is the last
        # stage as soon as the record has caught up, and the first stage before that.
        stage_used = next((st for st in reversed(stages_all)
                           if sum(numbers_of(c, st) is not None for c in options) >= 2),
                          stages_all[0] if stages_all else None)
        rows = [(c, numbers_of(c, stage_used)) for c in options]
        try:
            pick = problem.prefer_admitted(None if key == "*" else key, rows, state, stage_used)
        except Exception:  # noqa: BLE001
            pick = None
        def ident(c: Candidate) -> tuple:
            return ((c.meta or {}).get("prototype_sha"), c.artifact or "")
        if pick is not None and ident(pick) != ident(proven[key]):
            def nums(c: Candidate) -> str:
                m = numbers_of(c, stage_used) or {}
                keys = [k for k in ("fmax_mhz", "area_um2") if k in m] or list(m)[:2]
                return ", ".join(f"{k} {m[k]:.4g}" for k in keys) or "not measured alone"
            state.say(f"reload: {key}: {pick.name} ({nums(pick)}) stands over the last admitted "
                      f"{proven[key].name} ({nums(proven[key])}) -- the record's {stage_used} numbers say it is the better design")
            proven[key] = pick
        if key != "*":
            # THE SHORTLIST (D528, review step 10): every admitted design of the part the record
            # holds, with its numbers on the stage the reload compared on, best first by the
            # objectives -- what the standings show and what `take` and `contender` draw from
            state.part(key).shortlist = shortlist_of(problem, options, stage_used, numbers_of, proven[key])
    today = dict(state.versions or problem.versions() or {})
    state.versions = state.versions or dict(today)        # the rows written below carry it
    for key, cand in proven.items():
        sg = None if key == "*" else key
        k = int((cand.meta or {}).get("pipeline", 0) or 0)         # its register count stays (D496)
        can_spell = bool((cand.meta or {}).get("transpiled")) and callable(getattr(problem, "transpile", None))
        # ITS prototype (D504): the one the RTL was transpiled from, by digest. A row admitted
        # before digests has no prototype of its own on record; the part's ONE verified
        # prototype stands in when there is exactly one, and none when there are several --
        # D510 ended the matching-by-re-transpiling that guessed among them.
        want = str((cand.meta or {}).get("prototype_sha") or "")
        source = by_digest.get(key, {}).get(want) if want else None
        if source is None and not want and len(by_digest.get(key, {})) == 1:
            source = next(iter(by_digest[key].values()))
        # WHAT THE ROW WAS MADE BY (D510): a row from today's transpiler and judge is trusted
        # as it stands; a transpiler change re-spells the design from its prototype once and
        # records the result; a judge change re-judges once. No versions declared, or
        # none on the row: re-verify, as every reload did before.
        made_by = dict((cand.meta or {}).get("versions") or {})
        respell_due = bool(can_spell and source and today.get("transpiler")
                           and made_by.get("transpiler") != today.get("transpiler"))
        judge_due = (not today or not made_by or respell_due
                     or made_by.get("judge") != today.get("judge"))
        if not judge_due:
            state.admitted[key] = cand
            if source:
                state.prototypes[key] = source
            state.say(f"reload: {key} kept frozen (its row was made by today's transpiler and judge)")
            continue
        with _phase(f"records: re-verify {key}", why=cand.name) as out:
            out["design"] = f"{cand.name}: {len(cand.artifact or '')} chars of {problem.name} artifact"
            if can_spell and not source:
                out["prototype"] = ("the prototype this RTL was made from is not on record as verified"
                                    + (f" (digest {want})" if want else " (admitted before digests)")
                                    + "; the record's RTL stands, not re-spelled")
            # A TRANSPILED design is re-spelled by today's transpiler from its verified
            # prototype (D495): the prototype is the design, the RTL is derived from it, and
            # a transpiler fix (sigmoid's `~2'(v)`, which yosys read as a cast of size ~2)
            # must reach a design admitted before the fix. The record's text stays as the
            # fallback when the re-spelling does not pass today's judge.
            respelled = None
            if respell_due:
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
            if today:                                      # push for fmax reads it)
                # ONCE (D510): the re-verified design goes on record with today's versions, so
                # the next reload trusts it instead of spelling and judging it again
                _record_trial(state, cand, sg, v, admitted=True)
            state.say(f"reload: {key} re-verified, kept frozen"
                      + (" (re-spelled by today's transpiler)" if respelled is not None else "")
                      + ("; recorded with today's versions" if today else ""))
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
                        from .prototype import check_for

                        v = check_for(problem.prototype(), state, sg)(c["artifact"])
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


def sibling_design(problem: Problem, part: str, proto: str, state: LoopState) -> dict | None:
    """A VERIFIED design of `part` in a SIBLING campaign of this document (D506: the focused
    gelu campaign found a 716 MHz gelu the seven-operator campaign could not see) -- the
    sibling's last admitted design of the part, with its prototype and its own numbers on
    its deepest stage: `{campaign, artifact (prototype), digest, value, <metrics>}`.

    A sibling is another campaign in the same store whose identity document names this
    document (`study` = the document's id, D540); what it worked on is read from its rows,
    never from a study-specific key. The design is re-checked under today's rules by the
    import step, so a sibling under other params is still worth reading. None when there is
    none, when it is this campaign's own design already, when it was imported before, or
    when it measured slower here (`not_faster`)."""
    from .ledger import Kind

    rec = getattr(state, "records", None)
    store = getattr(rec, "store", None) if rec is not None else None
    if store is None:
        return None
    mine = rec.campaign_id
    objs = problem.objectives()
    first = objs[0].metric if objs else None
    best: dict | None = None
    try:
        stage_rank = {st: i for i, st in enumerate(problem.stages())}
        for row in store.list_campaigns():
            cid = row["campaign_id"]
            if cid == mine:
                continue
            obj = (store.campaign_row(cid) or {}).get("objective") or {}
            if obj.get("study") != problem.name:
                continue
            verified: dict[str, str] = {}                 # digest -> prototype text
            admitted_rtl: str | None = None
            admitted_sha: str | None = None
            measured: dict[str, dict[str, float]] = {}    # artifact digest -> numbers, deepest stage
            for t in store.trials(cid):
                c = t.candidate or {}
                meta = c.get("meta") or {}
                if t.stage == "prototype" and t.status == "ok" and meta.get("kind") == "prototype" \
                        and c.get("subgoal") == part and c.get("artifact"):
                    verified[prototype_digest(c["artifact"])] = c["artifact"]
                elif t.stage == "admit" and t.status == "ok" and c.get("subgoal") == part:
                    admitted_rtl = c.get("artifact") or admitted_rtl
                    admitted_sha = meta.get("prototype_sha") or admitted_sha
                elif t.stage in stage_rank and t.status == "ok" and t.result is not None and c.get("artifact"):
                    dg = prototype_digest(c["artifact"])
                    prev = measured.get(dg)
                    if prev is None or stage_rank[t.stage] >= prev.get("_rank", -1):
                        m = {k: float(e.value) for k, e in t.result.metrics.items()}
                        m["_rank"] = stage_rank[t.stage]
                        measured[dg] = m
            if not admitted_rtl or not verified:
                continue
            text = verified.get(admitted_sha or "") or list(verified.values())[-1]
            dg = prototype_digest(text)
            if text == proto or state.ledger.count(Kind.NOT_FASTER, part, dg):
                continue
            if state.ledger.count(Kind.IMPORTED, part, dg):
                continue
            nums = {k: v for k, v in (measured.get(prototype_digest(admitted_rtl)) or {}).items() if k != "_rank"}
            cand = {"campaign": cid[:12], "artifact": text, "digest": dg,
                    "value": float(nums.get(first, 0.0)) if first else 0.0, **nums}
            if best is None or cand["value"] > best["value"]:
                best = cand
    except Exception:  # noqa: BLE001
        return None
    return best
