"""The prototype stage (D424): prove the ALGORITHM in Python before any RTL. Seconds per attempt against the exhaustive reference, so the model can iterate on the math dozens of times an hour; only a prototype at 0 over earns a transcription. The same patch / gradient / revert machinery runs on the prototype's text as on the artifact's."""

from __future__ import annotations

import os
import time
import json
import math
import re
from typing import TYPE_CHECKING, Any, Callable

from .tools import Checked, hops_summary
from .gradient import Gradient
from .model import _ask, _compose, _json
from .observe import _phase
from .provenance import stamp, turn_cost
from .patch import apply_patch, parse_patch, patch_schema
from .types import LoopState, StageNames, Verdict

if TYPE_CHECKING:  # pragma: no cover
    from .problem import Problem

__all__ = ["PROTOTYPE_HELP", "prototype_schema"]

PROTOTYPE_HELP = (
    'Reply with ONLY JSON: {"prototype": "<complete python>", "why": "<one line>"} for a '
    'new prototype, or {"edits": [{"find": "...", "replace": "..."}], "why": "..."} to edit '
    "the current one.")

#: D505: with tools on, the turn is told it has them -- `check` most of all
TOOLS_HELP = (
    "YOU HAVE TOOLS in this turn: `check(prototype)` runs THE test on a complete prototype text "
    "and returns PASSES or the score with the failure report; `compute(code)` runs Python "
    "(numpy) and returns what it prints; `history()` lists what this campaign already tried for "
    "the part; `knowledge(query)` searches the method sheet and the papers. CHECK BEFORE YOU "
    "SUBMIT: write the prototype, call check, read the report, fix, check again -- and reply "
    "with the text that passed (or the best you reached). A prototype that passes `check` "
    "inside the turn is taken as your answer.")


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

        d = os.path.join(state.workdir, "prototypes", re.sub(r"[^A-Za-z0-9_.-]", "_", key),
                         f"pass{state.part(key).proto_passes or 1}")
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
    # D506: the answering round carried a `check(prototype=...)` written as text -- the model
    # asking to check it IS the model submitting it; and a fenced block with `def design(`
    from flux_llm.tools import text_tool_calls

    for call in text_tool_calls(reply or ""):
        try:
            args = json.loads(call["function"]["arguments"])
        except Exception:  # noqa: BLE001
            continue
        text = args.get("prototype")
        if isinstance(text, str) and "def design" in text:
            return text
    m = re.search(r"```(?:python)?\s*\n(.*?def design\(.*?)```", reply or "", re.S)
    if m:
        return m.group(1)
    return None



# ---- the language's prose (D515): what every prototype in `python-int` is told, once
LANGUAGE_RULES = (
    "RULES so it transcribes to hardware 1:1: use only integer and bit operations on numpy "
    "integer arrays -- shifts, masks, integer multiply/add, comparisons, np.where, small lookup "
    "tables you build from integer arithmetic (you may compute table constants with float math "
    "ONCE at definition time, as a ROM would be generated). No float arithmetic in the data path, "
    "no np.exp/np.log on the inputs. Decide the fixed-point formats (bit widths, Q positions) "
    "explicitly and keep them consistent; rounding must be round-to-nearest-even where it matters.\n"
    "The harness calls design() on every input and judges it exactly as the gate does; you will "
    "get the failures by input region with examples, and a PROGRESS line each turn. Use the "
    "compute tool to check pieces in isolation.\n"
    "YOU NEVER WRITE THE TARGET: a prototype that passes is TRANSPILED by the harness, every "
    "signal width measured over all inputs. For that it must stay in the subset: integers only; "
    "`for` only over a constant range; helper functions are inlined; tables are 1-D constant "
    "arrays indexed by an integer expression; division only by a constant, and `//` of a value "
    "that can be negative only by a power of two. WRITE IT FOR ONE INPUT if you like: "
    "`if`/`elif`/`else`, an early `return`, `and`/`or`/`not`, `min`/`max`/`abs` on values are all "
    "fine -- the harness converts them to array form itself (only a `while` on data cannot be: "
    "use a fixed `for _ in range(N)`). np.where and the array idioms are equally fine. A prototype "
    "outside the subset is refused with the construct named; keep its numerics and rewrite that "
    "construct.")
FAMILY_RULES = (
    "DO NOT GUESS SIZES -- DECLARE KNOBS. Anything you would otherwise pick by feel (table index "
    "bits, table value bits, fixed-point widths, correction bits, a constant's precision) goes in "
    "module-level integer constants with a SPACE of choices, e.g. `T = 6`, `M = 13`, `CB = 6` and "
    "`SPACE = {\"T\": [5, 6, 7], \"M\": [12, 13, 14], \"CB\": [0, 4, 6]}`; read them inside "
    "design() or in helpers it calls. The harness runs EVERY member (up to 256) on all inputs, "
    "reports the search, and SELECTS the cheapest member that passes by measured widths -- that "
    "member is the prototype from then on. List the likeliest choices first.")


def spec_prompt(problem: Problem, cap: Any, subgoal: str | None, state: LoopState) -> str:
    """The first prompt of the stage: what to compute (the problem's), the language's rules
    (the loop's), the toolkit and how to compose it (the problem's), the shape, the family
    rule, then what the campaign knows -- a redesign note, the part's history on record
    (D500), its shortest verified prototype as the example (D501)."""
    part = subgoal or problem.name
    kit = cap.toolkit
    ops = verified_operators(state, subgoal)
    text = [f"PROTOTYPE FIRST. Before any target code, write the `{part}` algorithm as a Python "
            f"function `design(x)` that takes {cap.domain} and returns {cap.reference(subgoal)}, "
            f"{cap.gate}.",
            LANGUAGE_RULES]
    if kit is not None:
        if kit.docs:
            text.append("TOOLS -- verified blocks ALREADY DEFINED in your prototype's namespace; call them. "
                        "Do not paste, redefine or re-derive any of them (a `def` with a block's name is "
                        "refused), do not test your design at module level (the harness does), and put no "
                        "print in it. The transpiler inlines the blocks and the gate checks the whole:\n" + kit.docs)
        if ops and kit.operator_docs is not None:
            text.append(kit.operator_docs(ops))
        if kit.compose:
            text.append(kit.compose)
        if kit.shape:
            text.append("THE SHAPE every prototype takes (fill in the algorithm; nothing else is needed):\n" + kit.shape)
    if cap.family:
        text.append(FAMILY_RULES)
    prompt = "\n".join(text)
    redesign = state.part(subgoal).redesign
    if redesign:
        prompt += "\n\n" + redesign                        # D499: a DIFFERENT algorithm, asked for
    hist = history(state, subgoal)
    if hist:
        prompt += "\n\n" + hist                            # D500
    prompt += example(state, subgoal, problem.subgoals())  # D501
    return prompt


def prefix_for(problem: Problem, cap: Any, subgoal: str | None, state: LoopState) -> str:
    """The stage's static prefix (D491): the mentor's sheet and the library's PAPER excerpts
    (D500: capped, never source-file lines), then the problem's contract line for the
    stage -- not the target's interface and not its reply shape, which in one prompt with
    the prototype's own had the model answer the Python stage in Verilog."""
    mentor = problem.knowledge()
    sheet = mentor.text("sheet", state) if mentor is not None else ""
    library = paper_excerpts(mentor.text("library", state)) if mentor is not None else ""
    contract = cap.contract.format(part=subgoal or problem.name) if cap.contract else ""
    return "\n\n".join(p for p in (sheet, library, contract) if p)


def paper_excerpts(library: str) -> str:
    """The library for the PROTOTYPE stage (D500): the excerpts that are prose from papers,
    never the lines of source files (`.sv`, `.cpp`, `.vhdl` -- an RTL comment or a C++
    stream write is nothing to a Python prototype). Measured: 7,000 of the 18,000 characters
    of a repair prompt were such lines. Whole (D548): the window bounds knowledge, no cap here."""
    if not library:
        return ""
    lines = library.splitlines()
    head = [ln for ln in lines if not ln.startswith("  * [")][:2]
    keep: list[str] = []
    for ln in lines:
        if not ln.startswith("  * ["):
            continue
        src = ln[5:ln.find("]")] if "]" in ln else ""
        if src.lower().endswith(".pdf"):
            keep.append(ln)
    return "\n".join(head + keep) if keep else ""


def reminder_for(cap: Any, subgoal: str | None, state: LoopState) -> str:
    """What every REPAIR turn carries (D483): the toolkit's signatures, the campaign's
    verified operators, the part's history."""
    kit = cap.toolkit
    ops = verified_operators(state, subgoal)
    text = (kit.reminder if kit is not None else "")
    if ops:
        text += (f"; VERIFIED OPERATORS: {', '.join(f'{op}_fp16(x)' for op in sorted(ops))}" if text else
                 "VERIFIED OPERATORS: " + ", ".join(f"{op}_fp16(x)" for op in sorted(ops)))
    hist = history(state, subgoal)
    if hist:
        text += ("\n" if text else "") + hist
    return text


def verified_operators(state: LoopState, subgoal: str | None) -> dict[str, str]:
    """The campaign's VERIFIED prototypes of the OTHER parts, in array form, as blocks the part
    being designed may call (D487: sigmoid = recip(1 + exp(-x)))."""
    from .pyint import vectorize

    out: dict[str, str] = {}
    for op, code in (getattr(state, "prototypes", None) or {}).items():
        if op == subgoal or op == "*" or not isinstance(code, str) or not code.strip():
            continue
        try:
            out[op] = vectorize(code)[0]
        except Exception:  # noqa: BLE001
            continue
    return out


def history(state: LoopState, subgoal: str | None, limit: int = 8) -> str:
    """What this part's earlier passes reached, from the record (D500): each recorded
    prototype's score and the first line of its report, best first, deduplicated --
    directions, not instructions."""
    rec = state.records
    if rec is None or getattr(rec, "store", None) is None:
        return ""
    seen: dict[float, str] = {}
    try:
        for t in rec.store.trials(rec.campaign_id):
            c = t.candidate or {}
            if t.stage != StageNames.PROTOTYPE or (c.get("meta") or {}).get("kind") != "prototype":
                continue
            if (c.get("subgoal") or "*") != (subgoal or "*"):
                continue
            sc = c.get("score")
            if not isinstance(sc, (int, float)):
                continue
            why = str(c.get("why") or "").splitlines()
            first = next((ln for ln in why if ln.startswith(("pattern:", "0 over;"))), None)
            if first is None:
                first = next((ln for ln in why if "pattern:" in ln), None)
            if first is None:
                first = next((ln for ln in why if not ln.startswith(("PROGRESS", "FAMILY", "  "))), why[0] if why else "")
            first = first[first.find("pattern:"):] if "pattern:" in first else first
            seen.setdefault(float(sc), first[:140])
    except Exception:  # noqa: BLE001
        return ""
    if not seen:
        return ""
    rows = sorted(seen.items())[:limit]

    def label(sc: float, why: str) -> str:
        if why.startswith("0 over"):
            return f"  0 over, logic depth {sc:g}: {why[len('0 over;'):].strip()}"
        return f"  {sc:g} over: {why}" if why else f"  {sc:g} over"

    return ("HISTORY of this part on record, best first (\"N over\" = inputs beyond the budget; "
            "\"0 over, logic depth D\" = a passing design and its depth proxy):\n"
            + "\n".join(label(sc, why) for sc, why in rows))


def example(state: LoopState, subgoal: str | None, parts: list[str]) -> str:
    """The campaign's SHORTEST verified prototype of another part, verbatim, as the example of
    the shape and the idioms (D501): the flow's own product, not a hand-written one -- what a
    passing design looks like beats a page of rules."""
    others = {op: code for op, code in (state.prototypes or {}).items() if op != subgoal and op in parts}
    if not others:
        return ""
    op, code = min(others.items(), key=lambda kv: len(kv[1]))
    if len(code) > 2500:
        return ""
    return (f"\n\nA VERIFIED PROTOTYPE OF THIS CAMPAIGN ({op}, 0 over on every input, transpiled and admitted) "
            "-- the shape and the idioms, not the algorithm:\n" + code.strip())


def check_for(cap: Any, state: LoopState, subgoal: str | None) -> Callable[[str], Verdict]:
    """The prototype's gate: the capability's own `check`, else the loop's skeleton (D516)
    over the campaign's other verified parts as blocks."""
    if cap.check is not None:
        return lambda code: cap.check(code, subgoal, state)
    from .check import check_prototype

    ops = verified_operators(state, subgoal)
    return lambda code: check_prototype(cap, code, subgoal, state, operators=ops)


def prefers_edits(cap: Any, subgoal: str | None, state: LoopState, best_score: float) -> str | None:
    """A rewrite is refused (D501) when the text in hand fails fewer than 5% of the inputs or
    is a passing design being made faster -- unless a DIFFERENT algorithm was asked for
    (D499's redesign). The reason, or None to let it run."""
    part = state.part(subgoal)
    if part.redesign:
        return None
    if not (best_score == best_score) or best_score == float("inf"):       # nothing in hand
        return None
    opt = part.optimise
    near = (best_score < 0.05 * cap.domain_size) if not opt else (best_score < 1e6)
    if not near:
        return None
    return (f"the text in hand {'passes every input' if opt else f'fails only {best_score:g} of {cap.domain_size} inputs'}; "
            "a rewrite starts over from tens of thousands of failures (measured: rewrites were the "
            "regressions) -- send {\"edits\": [...]} that change the one thing the report names. "
            "If the approach itself is wrong, say so in `why` with the word REWRITE and send the "
            "prototype again")


def score_unit(cap: Any, subgoal: str | None, state: LoopState) -> str:
    """What a prototype score IS in the trend line (D503): the problem's unit, or, while a
    part is being made faster, levels of logic depth (a broken edit is 1e6 + its failures)."""
    return (" levels of logic depth (0 over; a score above 1e6 is 1e6 + failing inputs)"
            if state.part(subgoal).optimise else cap.score_unit)

def _prototype_stage(problem: Problem, subgoal: str | None, state: LoopState,
                     human: str | None, method: str = "") -> tuple[str | None, str]:
    """(verified prototype code, "") or (None, why). Design once, then edit the
    prototype toward 0 over with the gradient feedback of D423; budget in cheap turns."""
    proto = problem.prototype()
    if proto is None:
        return None, "no prototype stage"
    key = subgoal or "*"
    tag = subgoal or problem.name
    req = state.request
    if key in state.prototypes:
        return state.prototypes[key], ""
    prefix = prefix_for(problem, proto, subgoal, state)
    first_prompt = spec_prompt(problem, proto, subgoal, state)
    schema = prototype_schema()
    code: str | None = None
    state.part(key).proto_passes += 1
    unit = score_unit(proto, subgoal, state)                                      # D503
    grad = Gradient(req.regress_after, unit=unit, fmt=lambda x: f"{x:g}", noun="prototype",
                    max_tolerance=max(1, int(req.max_tolerance)))
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
                v = check_for(proto, state, subgoal)(code)
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
    # D506 (Cedric: "the cap of 30 iterations/attempts might cut off a good candidate. Maybe it
    # should be dynamic and increase based on improvement rate"): the budget GROWS while the
    # pass improves -- every new best grants `prototype_patience` more attempts beyond the
    # budget, up to `prototype_attempts_max`; a pass that stops improving ends as before
    budget = max(1, req.prototype_attempts)
    cap = max(budget, int(req.prototype_attempts_max or budget))
    patience = max(0, int(req.prototype_patience or 0))
    attempt = -1
    while attempt + 1 < budget:
        attempt += 1
        parts = []
        help_lines = [PROTOTYPE_HELP, TOOLS_HELP if req.tools else ""]
        if code is None:
            # the plan's method is the MODEL's own idea for this part (D487: "recip of
            # (1 + exp of -x)"); the planning step wrote it, the prototype step reads it
            plan_line = (f"YOUR PLAN for {tag}, from your planning step: {method.strip()}"
                         if method and method.strip() else "")
            parts += [human or "", first_prompt, plan_line, *help_lines]
        else:
            numbered = "\n".join(f"{i + 1:4d} | {ln}" for i, ln in enumerate(code.splitlines()))
            parts += [f"Your prototype for {tag} was refused:\n\n{last_err}",
                      "Fix it with the SMALLEST edits (find must match exactly once), or "
                      "send a new prototype if the approach itself is wrong.",
                      reminder_for(proto, subgoal, state),
                      *help_lines,
                      f"Current prototype (line numbers for reading only):\n\n{numbered}"]
        prompt = _compose(prefix, *parts)
        checked = Checked() if req.tools else None           # D505: what the turn's own checks measured
        t_attempt = time.monotonic()
        answer = None
        with _phase(f"generate: prototype {tag}", why=f"attempt {attempt + 1}") as turn_out:
            try:
                tools = problem.tools(subgoal, state, "prototype", checked) if req.tools else None
                answer = _ask(state, prompt, schema, tools=tools)
                reply = answer.text
            except Exception as exc:  # noqa: BLE001
                return None, f"prototype turn did not run ({exc})"
            if checked is not None and checked.attempts:
                turn_out["checked in the turn"] = "\n".join(
                    f"{i + 1}. {'PASSES' if v.ok else f'score {v.score:g}'}" for i, (_c, v) in enumerate(checked.attempts))
        _trace(state, key, attempt + 1, prompt=prompt, reply=reply,
               hops=hops_summary(answer.hops) or None)
        ran = 0
        new = _parse_prototype(reply)
        if checked is not None and checked.best is not None:
            best_code, best_v = checked.best
            if best_v.ok and new != best_code:
                # a check that PASSED inside the turn is the answer, whatever came after it
                state.say(f"  prototype {tag}: a prototype checked inside the turn passes; taking it")
                new = best_code
            elif new is None and parse_patch(reply)[0] is None:
                # nothing usable submitted, but the turn measured something: its best is the attempt
                state.say(f"  prototype {tag}: the reply carried no prototype; the best the turn "
                          f"checked ({best_v.score:g}) is the attempt")
                new = best_code
        dropped_note = ""
        if new is not None:
            measured_in_turn = checked is not None and checked.verdict_for(new) is not None
            # D501's refusal of an unmeasured rewrite near a good seed does not apply to a text
            # the turn already MEASURED (live: a turn checked a 373 against a best of 1,644 and
            # the stage refused it as "a NEW prototype"; the pass ended at 1,644)
            refusal = (prefers_edits(proto, subgoal, state, grad.best_score)
                       if code and not measured_in_turn else None)
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
            v = checked.verdict_for(code) if checked is not None else None     # measured in the turn already
            if v is None:
                v = check_for(proto, state, subgoal)(code)
            else:
                out["measured"] = "inside the turn, by the model's own check call"
            if checked is not None and checked.best is not None and not v.ok:
                # D506: the turn measured something BETTER than what it submitted (live: a turn
                # checked its way to 1,224 over and sent a 3,270): the best of the turn is the
                # attempt, and the reply is what the model chose to say about it
                b_code, b_v = checked.best
                if math.isfinite(b_v.score) and (not math.isfinite(v.score) or b_v.score < v.score) and b_code != code:
                    out["submitted"] = f"score {v.score:g}; the turn had checked {b_v.score:g} -- taking that"
                    state.say(f"  prototype {tag}: the reply scores {v.score:g} but the turn checked "
                              f"{b_v.score:g}; the best of the turn is the attempt")
                    code, v = b_code, b_v
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
            _record_prototype(state, subgoal, code, v, ok=True, reply=answer,
                              seconds=round(time.monotonic() - t_attempt, 3))
            return code, ""
        trend, is_best = grad.observe(v.score, code, key=code,
                                      failure=problem.describe_failure(subgoal, v))
        if is_best:
            state.say(f"  prototype {tag}: score {v.score:g} (best so far)")
            if patience and math.isfinite(v.score):
                grown = min(cap, max(budget, attempt + 1 + patience))
                if grown > budget:
                    state.say(f"  prototype {tag}: still improving at attempt {attempt + 1}; the pass "
                              f"continues to {grown} attempts (at most {cap})")
                    budget = grown
            # ON RECORD AT ONCE (D491): a pass's best used to be written only when the pass
            # ended, so a run stopped mid-pass (a relaunch for a rule the pass had just
            # taught) resumed from the OLDER seed -- tanh's 5,752 would have gone back to 9,222
            if math.isfinite(v.score) and (seed is None or v.score < seed[0]):
                _record_prototype(state, subgoal, code, v, ok=False, reply=answer, seconds=round(time.monotonic() - t_attempt, 3))
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
                _record_prototype(state, subgoal, code, v, ok=False, reply=answer, seconds=round(time.monotonic() - t_attempt, 3))
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
        _record_prototype(state, subgoal, code, v, ok=False, reply=answer, seconds=round(time.monotonic() - t_attempt, 3))
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
                      *, ok: bool, reply: Any = None, seconds: float = 0.0) -> None:
    if state.records is None:
        return
    key = subgoal or "*"
    trace = (os.path.join(state.workdir, "prototypes", re.sub(r"[^A-Za-z0-9_.-]", "_", key),
                          f"pass{state.part(key).proto_passes or 1}")
             if state.workdir and state.workdir != "." else None)
    try:
        state.records.trial(
            {"name": f"prototype:{subgoal or 'goal'}", "artifact": code, "knobs": {},
             "meta": {"kind": "prototype",
                      "provenance": stamp(seconds=seconds or None, trace=trace, prompt=state.last_prompt_sha, **turn_cost(reply))},
             "subgoal": subgoal, "score": float(v.score), "why": _gist(v.why or "")},
            f"{subgoal or 'goal'}:prototype", stage=StageNames.PROTOTYPE, strategy="loop",
            metrics={"score": float(v.score)}, error=(None if ok else (v.why or "refused")[:300]),
            wall_s=seconds, analytic=True, evaluator="prototype@python")
    except Exception:  # noqa: BLE001
        pass
