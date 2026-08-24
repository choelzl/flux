"""THE PROTOTYPE CHECK, as the loop runs it (docs/decisions.md D516, review step 5.2): the
skeleton every `python-int` prototype goes through, with the problem's judge at the end.

    toolkit misuse -> array form -> "is this even Python" -> the hardware subset ->
    the family search -> the static screen -> the sandbox -> the problem's JUDGE ->
    can the target spell it, how deep is it -> the verdict (or, while a part is being
    made faster, the depth as the score)

Before D516 this was the NLU's `prototype_check`, 180 lines, of which a dozen were about
FP16 (the reference, the ULP rule, the residue) and the rest about the language, the
sandbox and the ladder's optimise mode. A problem now declares (`flux_loop.Prototype`) a
`harness` (what to run after the prototype: it prints `OUT <hex>`, and may print `TABLES`
and `DIAG` lines), a `judge` (the output as bytes -> a `Judgement`), a `family_judge` (the
gate as source, for the family harness), a `cost` (how to rank a family's passing members)
and a `target` (`flux_loop.Target`: can it be spelled, how deep is it); its `Toolkit` says
how the blocks are put before the code and how a sandbox error is explained.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .compute import preamble_lines, run_compute, screen_snippet
from .pyint import (VectorizeError, bind, configurations, describe_search, family_harness,
                    hardware_subset_violations, parse_family_output, relocate_lines, space_of, vectorize)
from .types import LoopState, Verdict

__all__ = ["Judgement", "check_prototype", "group_sites", "looks_like_rtl"]


@dataclass
class Judgement:
    """What the problem's judge says about a prototype's output: whether it passes, how many
    inputs it fails, the failure text a repair prompt carries, and the report (the payload
    of the verdict; the loop adds the family's note, the tables, the audit)."""

    ok: bool
    failing: int
    why: str
    report: dict[str, Any] = field(default_factory=dict)


def looks_like_rtl(code: str) -> bool:
    """A prototype reply that is RTL (D491: `module nlu_tanh`, `assign`, `logic [15:0]` in
    the `prototype` field) -- named as such instead of "does not parse"."""
    return not re.search(r"^\s*def\s+design\s*\(", code, re.M) and bool(
        re.search(r"^\s*(module\s+\w+|assign\s+\w+\s*=|logic\s*\[|always\s*@|endmodule)", code, re.M))


def group_sites(items: list[str]) -> list[str]:
    """"line 35: X", "line 36: X" -> "lines 35, 36: X" (D482: a scalar-style design broke
    one rule eight times; eight copies of the sentence teach less than one with the sites)."""
    groups: dict[str, list[int]] = {}
    order: list[str] = []
    for it in items:
        m = re.match(r"line (\d+): (.*)", it, re.S)
        if not m:
            if it not in groups:
                groups[it] = []
                order.append(it)
            continue
        msg = m.group(2)
        if msg not in groups:
            groups[msg] = []
            order.append(msg)
        groups[msg].append(int(m.group(1)))
    out = []
    for msg in order:
        lines = sorted(set(groups[msg]))
        if not lines:
            out.append(msg)
        elif len(lines) == 1:
            out.append(f"line {lines[0]}: {msg}")
        else:
            out.append(f"lines {', '.join(map(str, lines))}: {msg}")
    return out


def _refuse(why: str, payload: dict | None = None) -> Verdict:
    return Verdict(False, float("inf"), why, payload or {})


def check_prototype(proto: Any, code: str, subgoal: str | None, state: LoopState, *,
                    operators: dict[str, str] | None = None) -> Verdict:
    """One prototype through the skeleton. `operators` are the campaign's other verified
    parts in array form (the toolkit puts them beside its blocks)."""
    kit = proto.toolkit
    ops = dict(operators or {})
    n_pre = kit.prelude_lines(ops) if kit is not None and kit.prelude_lines else 0
    say = getattr(state, "say", None) or (lambda _m: None)

    # COMPOSE, DON'T RE-DERIVE (D482): a block defined again by hand, or a self-test at
    # module level, is refused before the subset rules -- named with the lines to delete.
    if kit is not None and kit.misuse is not None:
        fought = kit.misuse(code, {f"{op}_fp16" for op in ops})
        if fought:
            return _refuse("prototype fights the toolkit instead of composing it:\n  "
                           + "\n  ".join(fought[:8]) + (f"\n  ... {len(fought) - 8} more" if len(fought) > 8 else ""),
                           {"toolkit_misuse": fought})
    # ARRAY FORM (D483): per-element `if`/`return`/`and`/`min` become np.where and friends
    # before anything runs or judges; every message below is mapped back to the model's
    # lines. The model's text stays the prototype it edits.
    try:
        vec, linemap = vectorize(code)
    except VectorizeError as exc:
        return _refuse(f"prototype cannot be made array form: {exc}")
    except SyntaxError:
        vec, linemap = code, {}                   # the rules report the parse error
    relocate = kit.relocate if kit is not None and kit.relocate else (lambda text, n: text)

    def loc(text: str) -> str:
        return relocate_lines(relocate(text, n_pre), linemap)

    def compose(text: str) -> str:
        return kit.prelude(text, ops) if kit is not None and kit.prelude else text

    full = compose(vec)                            # the toolkit's blocks are the helpers
    if looks_like_rtl(code):
        return _refuse("this is RTL -- the prototype stage wants PYTHON: a `design(x)` on integer arrays, "
                       "built from the blocks; the target is transpiled from it by the harness once it "
                       "passes. Send the algorithm as Python.")
    broken = [r for r in (loc(b) for b in hardware_subset_violations(full))
              if "toolkit line" not in r]         # the toolkit's own lines are not the model's
    broken = group_sites(broken)                   # one line per rule, every site listed
    if broken:
        return _refuse("prototype is not hardware-implementable (it must transcribe to masks, shifts, "
                       "integer arithmetic and tables built once at module level):\n  "
                       + "\n  ".join(broken[:8]) + (f"\n  ... {len(broken) - 8} more" if len(broken) > 8 else ""),
                       {"hardware_subset": broken})
    # THE FAMILY SEARCH (D479): knobs declared as module-level constants with a SPACE of
    # choices are searched exhaustively in one sandboxed pass; the cheapest member that
    # passes (by the problem's cost) is bound and becomes the prototype.
    search_note = ""
    extra: dict[str, Any] = {}
    space = space_of(code) if proto.family else {}
    if space:
        refused = screen_snippet(code)             # the model's text, screened first
        if refused:
            return _refuse(f"prototype refused: {refused}")
        members, cap_note = configurations(space)
        judge_src = proto.family_judge(subgoal) if proto.family_judge else None
        if not judge_src:
            return _refuse("the prototype declares a SPACE but this problem has no family judge")
        (_name, out), = run_compute(
            [{"name": "family", "code": family_harness(full, members, judge=judge_src, prelude_lines=n_pre)}],
            timeout_s=max(120.0, state.request.compute_timeout_s * 6 * max(1, len(members) // 8)),
            max_chars=600_000, trusted=True)       # the harness is the framework's
        overs, _best, _hexout = parse_family_output(out)
        measured = {i: v for i, v in overs.items() if isinstance(v, int)}
        if not measured:
            text = describe_search(members, overs, None, cap_note)
            return _refuse("the family produced no result: "
                           + (kit.explain(text, linemap=linemap, n_pre=n_pre) if kit is not None and kit.explain else text))
        zero = sorted(i for i, v in measured.items() if v == 0)
        cost: dict[int, int] = {}
        if zero:
            for i in zero[:24]:
                try:
                    cost[i] = int(proto.cost(compose(vectorize(bind(code, members[i]))[0]))) if proto.cost else 0
                except Exception:  # noqa: BLE001 -- unspellable: refused below when picked
                    cost[i] = 1 << 30
            picked = min(zero[:24], key=lambda i: cost[i])
        else:
            picked = min(measured, key=lambda i: measured[i])
        search_note = describe_search(members, overs, picked, cap_note, cost)
        say("  " + search_note.replace("\n", "\n  "))
        code = bind(code, members[picked])
        vec, linemap = vectorize(code)
        full = compose(vec)
        extra = {"prototype": code, "search": search_note, "member": members[picked]}
    refused = screen_snippet(code)                 # the model's text is screened ...
    if refused:
        return _refuse(f"prototype refused: {refused}")
    audit = kit.audit() if kit is not None and kit.audit else ""
    (_name, out), = run_compute([{"name": "prototype", "code": full + audit + proto.harness}],
                                timeout_s=max(30.0, state.request.compute_timeout_s * 6),
                                max_chars=600_000, trusted=True)   # ... the harness is ours
    line = next((ln for ln in out.splitlines() if ln.startswith("OUT ")), None)
    if line is None:
        tail = out[-1000:]
        if len(out) > 1000:                        # cut at a line, not mid-word
            tail = tail[tail.find("\n") + 1:]
        explained = (kit.explain(tail, offset=preamble_lines(), linemap=linemap, n_pre=n_pre, code=code)
                     if kit is not None and kit.explain else tail)
        return _refuse("prototype did not produce output: " + explained)
    tables = next((ln[7:] for ln in out.splitlines() if ln.startswith("TABLES ")), "")
    if tables:
        extra["tables"] = tables
    diag = [ln[5:] for ln in out.splitlines() if ln.startswith("DIAG ")]
    if diag:
        extra["audit"] = diag
    try:
        got = bytes.fromhex(line[4:].strip())
    except ValueError as exc:
        return _refuse(f"prototype output unreadable: {exc}")
    # THE JUDGE: the problem's
    j: Judgement = proto.judge(subgoal, got, code, state, extra)
    rep = dict(j.report)
    prefix = (search_note + "\n") if search_note else ""
    part = state.part(subgoal)
    if j.ok:
        # passes -- and SPELLABLE by the target (D478): a prototype the transpiler cannot
        # spell is refused here, naming the construct, so the prototype stage fixes it
        # instead of the target stage falling back to transcription
        target = proto.target
        depth: dict | None = None
        if target is not None:
            try:
                target.spell(full, subgoal)
                depth = target.depth(full) if target.depth else None
            except Exception as exc:  # noqa: BLE001 -- the target's own error type
                return _refuse(prefix + f"the prototype passes but the transpiler cannot spell it: {loc(str(exc))}. "
                               "Rewrite that construct in the subset (masks, shifts, integer arithmetic, "
                               "comparisons, np.where, np.minimum/maximum/clip, tables indexed by an integer, "
                               "division only by a constant); keep the numerics exactly as they are.", rep)
        if depth is not None:
            rep["depth"] = depth
            depth_txt = target.describe_depth(depth) if target.describe_depth else f"logic depth ~{depth.get('depth')}"
            goal = part.optimise
            if goal:
                # OPTIMISE mode (D496): passing is the gate, the logic depth is the score
                d, d0, tgt = depth["depth"], goal["depth"], goal["target"]
                ok = d <= tgt
                why = (f"0 over; {depth_txt} -- was {d0} when this optimisation began, the goal is "
                       f"<= {tgt} ({'REACHED' if ok else 'not yet'}). " + (target.faster_advice() if target.faster_advice else ""))
                return Verdict(ok, float(d), prefix + why, rep)
            return Verdict(True, 0.0, prefix + depth_txt, rep)
        return Verdict(True, 0.0, prefix + (j.why or "passes"), rep)
    goal = part.optimise
    return Verdict(False, float(j.failing) + (1e6 if goal else 0.0),
                   prefix + j.why
                   + ("\nOPTIMISING for depth: 0 over stays the gate -- this edit broke the numerics; "
                      "the depth counts only once every input passes again" if goal else ""), rep)
