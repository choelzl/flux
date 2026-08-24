"""The prototype stage (D424): prove the ALGORITHM in Python before any RTL. Seconds per attempt against the exhaustive reference, so the model can iterate on the math dozens of times an hour; only a prototype at 0 over earns a transcription. The same patch / gradient / revert machinery runs on the prototype's text as on the artifact's."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from .compute import COMPUTE_HELP, _compute_block, _take_compute, _with_compute
from .gradient import Gradient
from .model import _ask, _compose, _json
from .observe import _phase
from .patch import apply_patch, parse_patch, patch_schema
from .types import LoopState, StageNames, Verdict

if TYPE_CHECKING:  # pragma: no cover
    from .problem import Problem

__all__ = ["PROTOTYPE_HELP", "prototype_schema"]

PROTOTYPE_HELP = (
    'Reply with ONLY JSON: {"prototype": "<complete python>", "why": "<one line>"} for a '
    'new prototype, or {"edits": [{"find": "...", "replace": "..."}], "why": "..."} to edit '
    "the current one. You may also add \"compute\" snippets as before.")


def prototype_schema() -> dict:
    return {"type": "object",
            "properties": {"prototype": {"type": "string"}, "why": {"type": "string"},
                           "edits": patch_schema()["properties"]["edits"]},
            "required": []}


def _trace(state: LoopState, key: str, attempt: int, **texts: str | None) -> None:
    """The attempt on disk (D482): `<workdir>/prototypes/<part>/passN/NN.<name>.txt` per text
    -- the reply, the code checked, the verdict. The record keeps a pass's END; reading a run
    attempt by attempt needs the attempts. A second pass on the same part gets its own
    directory (D485: it overwrote the first's). Best effort, never in the loop's way."""
    if not state.workdir or state.workdir == ".":     # tests pass "." -- not a trace target
        return
    try:
        import os
        import re

        passes = getattr(state, "_proto_passes", {})
        d = os.path.join(state.workdir, "prototypes", re.sub(r"[^A-Za-z0-9_.-]", "_", key),
                         f"pass{passes.get(key, 1)}")
        os.makedirs(d, exist_ok=True)
        for name, text in texts.items():
            if text:
                with open(os.path.join(d, f"{attempt:02d}.{name}.txt"), "w") as f:
                    f.write(text[:200_000])
    except Exception:  # noqa: BLE001
        pass


def _dropped_definitions(old: str, new: str) -> list[str]:
    """Module-level names the old text defined, the new text uses and no longer defines."""
    import ast

    try:
        o, n = ast.parse(old), ast.parse(new)
    except SyntaxError:
        return []

    def defined(tree: ast.Module) -> set[str]:
        out: set[str] = set()
        for st in tree.body:
            if isinstance(st, ast.Assign):
                out |= {t.id for t in st.targets if isinstance(t, ast.Name)}
            elif isinstance(st, (ast.FunctionDef, ast.ClassDef)):
                out.add(st.name)
        return out
    used = {m.id for m in ast.walk(n) if isinstance(m, ast.Name) and isinstance(m.ctx, ast.Load)}
    return sorted((defined(o) - defined(n)) & used)


def _restore_dropped(old: str, new: str, dropped: list[str]) -> str:
    """The new text with the old text's module-level definitions of `dropped` put back, in
    the old order, ahead of the first function (D501: 44 of 211 unmeasurable attempts in a
    day were "name X is not defined" -- a table the model's rewrite still used and no longer
    declared; its own lines, restored, make the attempt measurable)."""
    import ast

    try:
        o, n = ast.parse(old), ast.parse(new)
    except SyntaxError:
        return new
    old_lines = old.splitlines()
    blocks: list[str] = []
    for st in o.body:
        names = ({t.id for t in st.targets if isinstance(t, ast.Name)} if isinstance(st, ast.Assign)
                 else {st.name} if isinstance(st, (ast.FunctionDef, ast.ClassDef)) else set())
        if names & set(dropped):
            blocks.append("\n".join(old_lines[st.lineno - 1:(st.end_lineno or st.lineno)]))
    if not blocks:
        return new
    # before the first function of the new text (a table must precede design())
    first_fn = next((st for st in n.body if isinstance(st, (ast.FunctionDef, ast.ClassDef))), None)
    new_lines = new.splitlines()
    at = (first_fn.lineno - 1) if first_fn is not None else len(new_lines)
    while at > 0 and new_lines[at - 1].strip().startswith("@"):
        at -= 1
    return "\n".join(new_lines[:at] + blocks + new_lines[at:]) + ("\n" if new.endswith("\n") else "")


def _parse_prototype(reply: str) -> str | None:
    doc = _json(reply)
    if isinstance(doc, dict) and isinstance(doc.get("prototype"), str) and doc["prototype"].strip():
        return doc["prototype"]
    return None


def _prototype_stage(problem: Problem, subgoal: str | None, state: LoopState,
                     human: str | None, method: str = "") -> tuple[str | None, str]:
    """(verified prototype code, "") or (None, why). Design once, then edit the
    prototype toward 0 over with the gradient feedback of D423; budget in cheap turns."""
    spec = problem.prototype_spec(subgoal, state)
    if spec is None:
        return None, "no prototype stage"
    key = subgoal or "*"
    tag = subgoal or problem.name
    req = state.request
    if key in state.prototypes:
        return state.prototypes[key], ""
    prefix = problem.prototype_prefix(subgoal, state) or ""
    spec_prompt, spec_schema = spec
    schema = _with_compute(spec_schema or prototype_schema()) if req.compute else (spec_schema or prototype_schema())
    code: str | None = None
    passes = getattr(state, "_proto_passes", None)
    if passes is None:
        passes = {}
        try:
            state._proto_passes = passes            # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass
    passes[key] = passes.get(key, 0) + 1
    unit = str(getattr(problem, "score_unit", lambda s, st: "")(subgoal, state) or "")   # D503
    grad = Gradient(req.regress_after, unit=unit, fmt=lambda x: f"{x:g}", noun="prototype")
    last_err = ""
    last: tuple[str, Verdict] | None = None     # the newest checked attempt, measured or not
    prev_reject: str | None = None
    unmeasured = 0                              # consecutive attempts refused before their test
    seed = state.proto_best.get(key)
    if seed and seed[1].strip():
        # RESUME (D480): the best refused prototype on record is the starting point, its
        # failure text the first repair prompt, its score the gradient's first mark.
        # The text is RE-DESCRIBED by today's check (D488): the record's words are the
        # rules of the day it was written -- sigmoid resumed from 106 with a report that
        # predated the diagnosis of its 106.
        sc, code, why = seed
        with _phase(f"records: re-describe prototype {tag}", why=f"{sc:g} over on record") as out:
            out["prototype"] = code[:6000]
            try:
                v = problem.prototype_check(code, subgoal, state)
                if math.isfinite(v.score):
                    sc, why = float(v.score), problem.describe_failure(subgoal, v)
                else:
                    # the seed breaks a rule written since it was measured (D491: tanh's
                    # 2,680 used a 4,096-entry table under the new static cap): its text is
                    # still the starting point, its score is not today's mark -- the prompt
                    # says what to fix first and the first measured attempt becomes the best
                    why = (f"the best on record ({sc:g} over when it was measured) is REFUSED "
                           f"under today's rules; make it pass them first, keeping its "
                           f"numerics:\n{v.why or 'refused'}")
                    sc = float("inf")
                # the whole report, like a measured attempt's (Cedric: "capped at 310
                # chars leading to no real information inside")
                out["verdict"] = f"{sc:g} over (the record said {seed[0]:g})"
                out["why"] = why or ""
            except Exception as exc:  # noqa: BLE001
                out["verdict"] = f"kept the record's words ({exc!s:.80})"
                out["why"] = why or ""
        grad.observe(sc, code, key=code, failure=why)
        last_err = why or f"{sc:g} over"
        state.say(f"  prototype {tag}: resuming from the best on record ({sc:g} over)")
    for attempt in range(req.prototype_attempts):
        parts = [_compute_block(state, key)]
        if code is None:
            # the plan's method is the MODEL's own idea for this part (D487: "recip of
            # (1 + exp of -x)"); the planning step wrote it, the prototype step reads it
            plan_line = (f"YOUR PLAN for {tag}, from your planning step: {method.strip()}"
                         if method and method.strip() else "")
            parts += [human or "", spec_prompt, plan_line, PROTOTYPE_HELP, COMPUTE_HELP if req.compute else ""]
        else:
            numbered = "\n".join(f"{i + 1:4d} | {ln}" for i, ln in enumerate(code.splitlines()))
            parts += [f"Your prototype for {tag} was refused:\n\n{last_err}",
                      "Fix it with the SMALLEST edits (find must match exactly once), or "
                      "send a new prototype if the approach itself is wrong.",
                      problem.prototype_reminder(subgoal, state) or "",
                      PROTOTYPE_HELP, COMPUTE_HELP if req.compute else "",
                      f"Current prototype (line numbers for reading only):\n\n{numbered}"]
        prompt = _compose(prefix, *parts)
        with _phase(f"generate: prototype {tag}", why=f"attempt {attempt + 1}"):
            try:
                reply = _ask(state, prompt, schema)
            except Exception as exc:  # noqa: BLE001
                return None, f"prototype turn did not run ({exc})"
        _trace(state, key, attempt + 1, prompt=prompt, reply=reply)
        ran = _take_compute(state, key, reply, tag)
        new = _parse_prototype(reply)
        dropped_note = ""
        if new is not None:
            refusal = problem.prefers_edits(subgoal, state, grad.best_score, new) if code else None
            doc = _json(reply)
            if refusal and isinstance(doc, dict) and "REWRITE" in str(doc.get("why", "")).upper():
                refusal = None                              # the model says the approach is wrong
            if refusal:
                # D501: a rewrite where the text in hand is nearly right is refused before it
                # runs -- rewrites were the regressions (583 measured-but-worse attempts in a day)
                last_err = f"{last_err}\n(your NEW prototype was not run: {refusal})"
                continue
            dropped = _dropped_definitions(code, new) if code else []
            if dropped:
                # D483/D501: a "new prototype" that is design() alone, the tables it names left
                # in the previous text -- its own definitions are put back and the attempt runs;
                # the note says so
                new = _restore_dropped(code, new, dropped)
                dropped_note = (f"\n(your NEW prototype dropped the module-level definitions of "
                                f"{', '.join(dropped)} while still using them; the previous text's "
                                "were put back before your functions -- keep them, or send edits instead)")
            code = new
        elif code is not None:
            edits, _why = parse_patch(reply)
            if edits is None:
                if ran:
                    continue
                last_err = f"{last_err}\n(your reply carried neither a prototype nor edits)"
                continue
            patched, perr = apply_patch(code, edits)
            if patched is None:
                if perr == prev_reject:
                    last_err = f"{last_err}\n(patch rejected twice identically: {perr}; send a new prototype)"
                    prev_reject = None
                else:
                    prev_reject = perr
                    last_err = f"{last_err}\n(previous patch rejected: {perr})"
                continue
            code = patched
        else:
            if ran:
                continue
            last_err = "your reply carried no prototype"
            continue
        with _phase(f"test: prototype {tag}", why=f"attempt {attempt + 1}") as out:
            v = problem.prototype_check(code, subgoal, state)
            out["verdict"] = f"{'PASSES' if v.ok else 'refused'}, score {v.score:g}"
            if v.why:
                out["why"] = v.why
        if dropped_note:
            v = Verdict(v.ok, v.score, (v.why or "") + dropped_note, v.payload)
        _trace(state, key, attempt + 1, code=code,
               verdict=f"{'PASSES' if v.ok else 'refused'}, score {v.score:g}\n{v.why or ''}")
        # A check may hand back a REWRITTEN prototype (D479: the family's chosen member,
        # its knobs bound): that concrete text is what is stored, recorded and transpiled.
        bound = (v.payload or {}).get("prototype") if isinstance(v.payload, dict) else None
        if isinstance(bound, str) and bound.strip():
            code = bound
        last = (code, v)
        if v.ok:
            state.say(f"  PROTOTYPE {tag} passes: 0 over on the full domain "
                      f"(attempt {attempt + 1}); transcribing to the target next")
            state.prototypes[key] = code
            _record_prototype(state, subgoal, code, v, ok=True)
            return code, ""
        trend, is_best = grad.observe(v.score, code, key=code,
                                      failure=problem.describe_failure(subgoal, v))
        if is_best:
            state.say(f"  prototype {tag}: score {v.score:g} (best so far)")
            # ON RECORD AT ONCE (D491): a pass's best used to be written only when the pass
            # ended, so a run stopped mid-pass (a relaunch for a rule the pass had just
            # taught) resumed from the OLDER seed -- tanh's 5,752 would have gone back to 9,222
            if math.isfinite(v.score) and (seed is None or v.score < seed[0]):
                _record_prototype(state, subgoal, code, v, ok=False)
        if math.isfinite(v.score):
            unmeasured = 0
            state.proto_best[key] = min(state.proto_best.get(key, (float("inf"), "", "")),
                                        (float(v.score), code, problem.describe_failure(subgoal, v)),
                                        key=lambda e: e[0])
            try:                                       # the results table's "trying" row, live
                from .observe import refresh_standings

                refresh_standings(state, f"{tag} attempt {attempt + 1}")
            except Exception:  # noqa: BLE001
                pass
        else:
            unmeasured += 1
            state.say(f"  prototype {tag}: not measurable -- {(v.why or 'refused')[:90]}")
            if unmeasured >= req.prototype_unmeasured_stop and grad.best is None:
                # NOTHING has measured and the refusals keep coming (D480: 17 of 31 live
                # passes spent all 30 attempts this way): stop the pass, keep the budget
                _record_prototype(state, subgoal, code, v, ok=False)
                return None, (f"{unmeasured} attempts in a row refused before their test "
                              f"({(v.why or 'refused')[:120]}); the pass stops here")
            if unmeasured >= req.prototype_unmeasured_stop and grad.best is not None:
                # DRIFT (D484): a good best on hand and the model rewriting from scratch,
                # unmeasurably, turn after turn (exp at 644 over, then four re-derivations of
                # the toolkit in a row). The pass ends, the best is recorded as the seed, and
                # the next pass starts from it with a fresh context and its own failure text.
                _record_prototype(state, subgoal, grad.best[1],
                                  Verdict(False, grad.best_score, grad.best_failure or last_err), ok=False)
                return None, (f"{unmeasured} unmeasurable rewrites in a row after reaching "
                              f"{grad.best_score:g}; the pass stops here and the next resumes from "
                              "that best")
        failure = problem.describe_failure(subgoal, v)
        if grad.revert_due(code):
            state.say(f"  prototype {tag}: the tolerance for worsening edits is spent; backtracking")
            code, note = grad.revert()
            trend += note
            failure = grad.revert_failure() or failure  # the landing text's own failures (D484/D504)
        last_err = f"{trend}\n{failure}"
    if grad.best is not None:
        _record_prototype(state, subgoal, grad.best[1], Verdict(False, grad.best_score, grad.best_failure or last_err),
                          ok=False)
        return None, f"prototype budget spent; best reached score {grad.best_score:g}, not 0"
    if last is not None:
        # Every attempt was refused before its test ran: nothing was measured, so there
        # is no best -- the record keeps the last attempt and the rule it broke.
        code, v = last
        _record_prototype(state, subgoal, code, v, ok=False)
        return None, (f"prototype budget spent; none of {len(grad.history)} attempt(s) was "
                      f"measurable ({(v.why or 'refused')[:80]})")
    return None, "no prototype was ever produced"


def _gist(why: str, cap: int = 400) -> str:
    """The report's diagnosis first (its `pattern:` line, or a "0 over; ..." line), then the
    head -- so the record's 400 characters carry what a later pass wants to know (D500: the
    part's history in the prompt), not the region table's first rows."""
    lines = why.splitlines()
    lead = [ln for ln in lines if ln.startswith(("pattern:", "0 over;")) or "pattern:" in ln]
    lead = [ln[ln.find("pattern:"):] if "pattern:" in ln else ln for ln in lead]
    rest = [ln for ln in lines if ln not in lead]
    return "\n".join(lead + rest)[:cap]


def _record_prototype(state: LoopState, subgoal: str | None, code: str, v: Verdict,
                      *, ok: bool) -> None:
    if state.records is None:
        return
    try:
        state.records.trial(
            {"name": f"prototype:{subgoal or 'goal'}", "artifact": code, "knobs": {},
             "meta": {"kind": "prototype"}, "subgoal": subgoal, "score": float(v.score),
             "why": _gist(v.why or "")},
            f"{subgoal or 'goal'}:prototype", stage=StageNames.PROTOTYPE, strategy="loop",
            metrics={"score": float(v.score)}, error=(None if ok else (v.why or "refused")[:300]),
            analytic=True, evaluator="prototype@python")
    except Exception:  # noqa: BLE001
        pass
