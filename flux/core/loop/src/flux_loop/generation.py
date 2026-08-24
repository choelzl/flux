"""The generation <-> test inner loop (D412/D414/D416/D417), problem-agnostic: design or resume from the best, build, fast-check, patch toward the failure with the gradient of D423, revert when an edit path does not converge."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .gradient import Gradient
from .model import _ask, _compose
from .observe import _phase
from .patch import apply_patch, parse_patch
from .prototype import _prototype_stage
from .types import BuildError, Candidate, LoopState

if TYPE_CHECKING:  # pragma: no cover
    from .problem import Problem

__all__ = ["_generate_with_model"]

#: D505: an RTL turn's tools (no `check` here -- the build and the fast test are the loop's)
TOOLS_HELP_RTL = (
    "YOU HAVE TOOLS in this turn: `compute(code)` runs Python (numpy) and returns what it "
    "prints; `history()` lists what this campaign already tried for the part; `knowledge(query)` "
    "searches the method sheet and the papers. Use them for any number you would otherwise "
    "derive by hand.")

def _generate_with_model(problem: Problem, subgoal: str | None, method: str,
                         state: LoopState, human: str | None
                         ) -> tuple[Candidate | None, Any, str]:
    req = state.request
    key = subgoal or "*"
    tag = subgoal or problem.name
    plan = state.plans.get(key) or {}
    attempts = int(plan.get("repair_attempts") or req.repair_attempts)   # D432: per part
    prior_entry = state.best.get(key)
    prior = prior_entry[1] if prior_entry else None
    prior_why = prior_entry[2] if prior_entry else ""
    prefix = problem.prompt_prefix(subgoal, state) or ""
    help_text = TOOLS_HELP_RTL if req.tools else ""
    if req.prototype and key not in state.prototypes:
        proto, why = _prototype_stage(problem, subgoal, state, human, method=method or "")
        if proto is None and why != "no prototype stage":
            # Cedric: "we need 0 over" -- an algorithm that is not exact in Python
            # will not become exact in RTL; do not spend hours transcribing it
            return None, None, f"prototype did not reach 0 over: {why}"
    proto_block = ""
    if key in state.prototypes:
        # TRANSPILED, not transcribed (D478): the prototype is the design; turning it into
        # the target is mechanical, and the step where the model lost widths, signs and
        # shifts every time (D468, D473) is not asked of it. Bit-exactness is the gate's to
        # prove; a mismatch is a transpiler defect and is said as one.
        with _phase(f"generate: transpile {tag}", why="the verified prototype, no model") as out:
            out["prototype"] = state.prototypes[key][:6000]
            try:
                cand = problem.transpile(state.prototypes[key], subgoal, state)
                if cand is not None:
                    out["artifact"] = (f"{cand.name}: {(cand.artifact or '').count(chr(10))} lines\n"
                                       + (cand.artifact or "")[:6000])
            except Exception as exc:  # noqa: BLE001
                out["error"] = str(exc)[:600]
                cand = None
        if cand is not None:
            with _phase(f"test: build {tag}", why=cand.name) as out:
                try:
                    built = problem.build(cand, subgoal, state)
                    out["built"] = "ok"
                except BuildError as exc:
                    out["error"] = str(exc)[:4000]
                    built = None
            if built is not None:
                fails, summary = problem.fast_check(built, cand, subgoal, state)
                if fails == 0:
                    state.say(f"  {tag}: transpiled from the verified prototype; passes the fast check")
                    return cand, built, ""
                state.say(f"  {tag}: the TRANSPILED prototype fails {fails} fast-check vector(s) -- "
                          "a transpiler defect, not the design's; the model transcribes instead")
                state.lessons.append(f"[transpile] {tag}: {fails} failing after transpilation: {summary[:200]}")
            else:
                state.say(f"  {tag}: the transpiled prototype did not build; the model transcribes instead")
        proto_block = ("VERIFIED PROTOTYPE (this exact algorithm passes 0 over on the full "
                       "domain; TRANSCRIBE it 1:1 into the target -- the same widths, shifts, tables and rounding):\n"
                       "```python\n" + state.prototypes[key] + "\n```")

    def wrap(pair: tuple[str, dict | None]) -> tuple[str, dict | None]:
        """Static prefix first, then the results of the model's computations, then
        the verified prototype, the turn's own text and the compute help -- and the
        schema accepts compute."""
        body, schema = pair
        brief = (state.plans.get(key) or {}).get("brief")                    # D577: the plan's method for this part
        brief_block = f"BRIEF (from the orchestrator):\n{brief}" if brief else ""
        return _compose(prefix, brief_block, proto_block, body, help_text), schema

    def fresh(h: str | None) -> tuple[str, dict | None]:
        return wrap(problem.design_prompt(subgoal, method, state, h, prior, prior_why))

    tries = state.attempts.get(key, 0)
    state.attempts[key] = tries + 1
    resume = bool(prior and req.patching and (tries + 1) % max(1, req.explore_every) != 0)
    mode = "patch" if resume else "design"
    cand: Candidate | None = prior if resume else None
    if resume:
        state.say(f"  resuming {tag} from its best design (score {prior_entry[0]:g}) "
                  "and editing toward the failure")
    prompt, schema = ("", None) if resume else fresh(human)
    last_err = prior_why if resume else "no reply"
    prev_reject: str | None = None
    grad = Gradient(req.regress_after, max_tolerance=max(1, int(req.max_tolerance)))   # D423: the trend, and the revert
    last_good: str | None = prior.artifact if prior else None
    broke = 0
    for attempt in range(attempts + 1):
        tools = problem.tools(subgoal, state, "generate") if req.tools else None   # D505
        if mode == "patch" and cand is not None and req.patching:
            with _phase(f"repair: {tag}", why=f"attempt {attempt + 1}"):
                try:
                    p, sch = wrap(problem.patch_prompt(subgoal, cand, last_err, state))
                    reply = _ask(state, p, sch, tools=tools).text
                except Exception as exc:  # noqa: BLE001
                    return None, None, f"patcher did not run ({exc})"
            edits, pwhy = parse_patch(reply)
            if edits is None:
                state.say(f"  patch unusable ({pwhy}); rewriting {tag} instead")
                mode = "design"
                prompt, schema = wrap(problem.rewrite_prompt(subgoal, cand, last_err, state))
                continue
            patched, perr = apply_patch(cand.artifact, edits)
            if patched is None:
                if perr == prev_reject:
                    state.say(f"  patch rejected twice identically ({perr}); rewriting "
                              f"{tag} instead")
                    mode = "design"
                    prompt, schema = wrap(problem.rewrite_prompt(subgoal, cand, last_err, state))
                    prev_reject = None
                    continue
                prev_reject = perr
                last_err = f"{last_err}\n(previous patch rejected: {perr})"
                state.say(f"  patch rejected: {perr}")
                continue
            state.say(f"  patched {tag}: {len(edits)} edit(s) -- {pwhy}")
            cand, tnote = problem.apply_tools(subgoal, cand.with_artifact(patched), reply,
                                              state)
            if tnote:
                last_err = tnote
        else:
            with _phase(f"generate: {tag}", why=f"attempt {attempt + 1}",
                        structured=req.structured) as out:
                try:
                    reply = _ask(state, prompt, schema, tools=tools).text
                except Exception as exc:  # noqa: BLE001
                    out["error"] = str(exc)[:600]
                    return None, None, f"generator did not run ({exc})"
                cand, why = problem.parse_design(reply, subgoal)
                out["parsed"] = cand.name if cand is not None else f"unparseable: {why}"
            if cand is None:
                last_err = why or "unparseable"
                prompt, schema = fresh(None)
                continue
            cand, tnote = problem.apply_tools(subgoal, cand, reply, state)
            if tnote:
                last_err = tnote
        built = None
        with _phase(f"test: build {tag}", why=cand.name) as out:
            try:
                built = problem.build(cand, subgoal, state)
                out["built"] = "ok"
            except BuildError as exc:
                last_err = str(exc)[:600]
                out["error"] = str(exc)[:4000]
        if built is None:
            broke += 1
            if broke >= max(1, req.revert_after) and last_good and last_good != cand.artifact:
                state.say(f"  reverting {tag} to the last version that built; that edit "
                          "path is not converging")
                cand = cand.with_artifact(last_good)
                last_err = ("your edits broke the build and could not be fixed; the "
                            "text has been REVERTED to the last version that built. "
                            "Make a DIFFERENT, smaller change.")
                broke = 0
        else:
            last_good, broke = cand.artifact, 0
            fails, summary = problem.fast_check(built, cand, subgoal, state)
            if fails == 0:
                state.say(f"  {tag} passes the fast check; handing it to the gate")
                return cand, built, ""
            # D423: the trend is feedback the model never had -- did the last edit
            # help, by how much, and where does it stand against the best so far
            trend, is_best = grad.observe(fails, (cand, built), key=cand.artifact, failure=summary)
            if is_best:
                state.say(f"  {tag}: {fails} fast-check failure(s) (best so far)")
            if grad.revert_due(cand.artifact):
                # two regressions in a row: back to the best attempt, and say so --
                # the same rule as reverting a build that broke (D417), for accuracy
                state.say(f"  {tag}: the tolerance for worsening edits is spent; backtracking "
                          f"(best {grad.best_score:g} failing)")
                (cand, _built), note = grad.revert()
                last_good = cand.artifact
                trend += note
                summary = grad.revert_failure() or summary   # the landing text's own failures (D484/D504)
            last_err = f"{trend}\n{summary}"
        mode = "patch" if req.patching else "design"
        if mode == "design":
            prompt, schema = wrap(problem.rewrite_prompt(subgoal, cand, last_err, state))
    if grad.best is not None:
        state.say(f"  {tag}: budget spent; sending the best attempt ({grad.best_score:g} "
                  "failing) to the gate")
        best_cand, best_built = grad.best[1]
        return best_cand, best_built, ""
    return None, None, f"nothing built in {attempts} tries: {last_err}"
