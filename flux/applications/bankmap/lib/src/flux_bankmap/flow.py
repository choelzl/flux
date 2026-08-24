"""The bank-mapping study: baseline, exact linear search, then the open family (D356).

THE LADDER, cheapest and most certain first:

  1. BASELINE   the plain modulo. Checked, not assumed: the study exists because it fails, and
                the report says by how much.
  2. z3         the XOR-fold family, searched EXACTLY. Either the cheapest conflict-free fold
                the checker accepts for every start address, or a proof that none exists.
  3. FEASIBLE   when none exists, what IS achievable: the largest concurrency the request's
                strides admit, and the largest stride subset the requested concurrency admits.
                A study that can only say "no" has not finished.
  4. MODEL      non-linear families a model proposes -- consulted after the linear answer is
                known, told what failed and why, and checked exhaustively like everything else.

The DECISION is the cheapest conflict-free mapping found; if none, the best partial answer,
labelled as partial. Every mapping is scored by the same exhaustive checker, so a model's idea
and the solver's result are compared on one footing.
"""

from __future__ import annotations

import time
from typing import Any, Callable

from .check import Verdict, check
from .impossible import find_impossibility, max_feasible_concurrency
from .mapping import Mapping, Modulo, XorFold
from .problem import MappingRequest, MappingResult
from .solve_z3 import solve


def _feasible_concurrency(request: MappingRequest, log: Callable[[str], None],
                          start: int | None = None) -> tuple[int, XorFold | None]:
    """The largest N for which an XOR-fold exists, by descent. What the strides actually admit.

    `start` is the pigeonhole's own bound when there is one: every N above it is proved
    impossible for any mapping, and asking z3 to rediscover that at twenty seconds a step
    was most of a two-minute report.
    """
    from dataclasses import replace

    top = request.concurrent - 1 if start is None else min(start, request.concurrent - 1)
    for n in range(top, 0, -1):
        m, _ = solve(replace(request, concurrent=n), timeout_s=min(request.z3_seconds, 20))
        if m is not None:
            log(f"  feasible: {n} concurrent accesses are conflict-free for all strides "
                f"({m.describe()}, {m.hardware_cost()} XOR)")
            return n, m
    return 0, None


def _feasible_strides(request: MappingRequest, log: Callable[[str], None]) -> tuple[tuple[int, ...], XorFold | None]:
    """The largest stride subset the requested concurrency admits, greedily by stride order."""
    from dataclasses import replace

    kept: list[int] = []
    best: XorFold | None = None
    proved, unsettled = [], []
    budget = min(request.z3_seconds, 20)
    for s in request.strides:
        trial = tuple(kept + [s])
        m, trace = solve(replace(request, strides=trial), timeout_s=budget)
        if m is not None:
            kept.append(s)
            best = m
        elif "unsat" in trace.outcome:
            proved.append(s)
        else:
            unsettled.append(s)
    if kept and len(kept) < len(request.strides):
        # "Does not" used to cover both a proof and a twenty-second timeout. They are different
        # answers: one closes a door, the other says the door was not tried hard enough.
        parts = []
        if proved:
            parts.append(f"adding any of {proved} is proved impossible for the linear family")
        if unsettled:
            parts.append(f"no fold was found within {budget}s when adding any of {unsettled} "
                         "(not a proof; a larger --z3-seconds may settle it)")
        log(f"  feasible: strides {kept} together admit {request.concurrent} concurrent "
            f"accesses; " + "; ".join(parts))
    return tuple(kept), best


def _stride_compatibility(request: MappingRequest, log: Callable[[str], None]) -> list[str]:
    """Which strides can coexist at all, when the greedy subset stopped at one.

    "Strides [1] admit N; adding any other does not" is true and uninformative when the reason is
    that NO two of the strides can share the hardware: through a first stage of 4-lane crossbars
    into 4 groups, a stride-s chunk makes the group 4s-periodic and a stride-t chunk then
    collides whenever 2s divides t -- every pair of powers of two (D363). Proved per pair with the
    pigeonhole rung (microseconds), so the study says "each alone, never two" rather than leaving
    the reader to infer it from a greedy walk that happened to start at stride 1.
    """
    from dataclasses import replace
    from itertools import combinations

    strides = request.strides
    if len(strides) < 2 or len(strides) > 10:
        return []
    alone = {s: find_impossibility(replace(request, strides=(s,))) is None
             for s in strides}
    pairs = list(combinations(strides, 2))
    bad = [(s, t) for s, t in pairs
           if find_impossibility(replace(request, strides=(s, t))) is not None]
    out: list[str] = []
    if all(alone.values()) and len(bad) == len(pairs):
        out.append(f"each stride alone is servable at {request.concurrent} concurrent, and NO "
                   f"two of them together are -- every pair is proved impossible for any "
                   f"mapping (with a laned first stage, a stride-s chunk fixes the group as "
                   f"a 4s-periodic function of the address and a stride-t chunk then collides "
                   f"whenever 2s divides t)")
    elif bad:
        out.append(f"{len(bad)} of {len(pairs)} stride pairs are proved impossible together: "
                   + ", ".join(f"{s}+{t}" for s, t in bad[:8])
                   + (" ..." if len(bad) > 8 else ""))
    for line in out:
        log(f"  {line}")
    return out


def run_study(request: MappingRequest, *,
              propose: Callable[..., list[tuple[Mapping, str]]] | None = None,
              log: Callable[[str], None] | None = None,
              feedback: Any | None = None) -> MappingResult:
    say = log or (lambda m: print(m, flush=True))
    started = time.monotonic()
    candidates: list[tuple[Mapping, Verdict, str]] = []
    refused: list[tuple[str, str]] = []
    lessons: list[str] = []
    not_established: list[str] = []
    n = request.concurrent

    # The rim (D402). `request.db` used to reach this function and die unread -- the
    # flag was a lie. Now it names the campaign record: every checked mapping lands
    # as a trial (refused ones with the checker's verdict, the record's cheapest
    # teaching signal), the decision as an INFERENCE conclusion, and a resumed run
    # seeds the model round's ALREADY TRIED list from its own past refusals.
    records = None
    if request.db:
        from flux_records import Records

        records = Records(request.db, objective={
            "study": "bankmap", "strides": list(request.strides), "concurrent": n,
            "banks": request.banks, "address_bits": request.address_bits,
            "topology": request.topology,
            "stages": [st.describe() for st in request.stages]}, log=say)
    from flux_feedback import reload_notes

    human_notes: list[Any] = reload_notes(records, say=say)

    def _on_note(note: Any) -> None:
        if records is not None:
            records.note(note.text)
        has_model = propose is not None and request.llm_round > 0
        lessons.append(f"[human] operator guidance: {note.text!r}"
                       + (" -- it goes into the model round's prompt" if has_model
                          else " -- no model role in this run; recorded and reported, "
                               "it reached no prompt"))

    def _finish(decision, conflict_free: bool) -> None:
        """Every exit passes here: the last typed note is drained (recorded even when
        it reached no prompt), and the decision joins the record as INFERENCE."""
        from flux_feedback import drain_guidance

        drain_guidance(feedback, human_notes, on_note=_on_note)
        if records is not None and decision is not None:
            records.conclude({"decision": decision.describe(),
                              "conflict_free": conflict_free,
                              "hardware_cost": decision.hardware_cost()})

    progress: list[dict] = []

    def track(mapping, verdict, phase: str) -> None:
        progress.append({"quality": verdict.clean_fraction, "cost": mapping.hardware_cost(),
                         "label": mapping.describe(), "phase": phase,
                         "solved": verdict.conflict_free})
        if records is not None:
            records.trial(
                {"family": type(mapping).__name__, "describe": mapping.describe()},
                mapping.describe(), rung="exhaustive", strategy=phase,
                metrics={"clean_fraction": verdict.clean_fraction,
                         "hardware_cost": float(mapping.hardware_cost())}
                if verdict.conflict_free else None,
                error=None if verdict.conflict_free else verdict.summary(n)[:200],
                analytic=True, evaluator="bankmap@exhaustive-check")

    say(f"problem: {request.describe()}")
    for note in request.notes:
        say(f"  interconnect: {note}")
        lessons.append(f"interconnect: {note}")

    def rule_wired(req: MappingRequest) -> MappingRequest:
        """Unsolved free stages wired by rule (the interleave) for CHECKING only (D372).

        An unsolved free stage constrains no pair, so a mapping checked against it is judged
        against hardware that does not exist -- the first version declared plain modulo
        conflict-free that way. Until the solver chooses a wiring, verdicts use the least
        constrained concrete one, and say so.
        """
        from dataclasses import replace as _replace

        if not any(st.lane_key == "free" and st.partition is None for st in req.stages):
            return req
        fixed = []
        for st in req.stages:
            if st.lane_key == "free" and st.partition is None:
                blocks = min(st.blocks, req.concurrent)
                fixed.append(_replace(st, partition=tuple(
                    tuple(range(b, req.concurrent, blocks)) for b in range(blocks))))
            else:
                fixed.append(st)
        return _replace(req, stages=tuple(fixed))

    # 1. baseline -- against a CONCRETE wiring: rule-fixed interleave until the solver chooses
    base = Modulo(0)
    bv = check(base, rule_wired(request))
    candidates.append((base, bv, "baseline"))
    track(base, bv, "baseline")
    say(f"baseline {base.describe()}: {bv.summary(n)}")
    if bv.conflict_free:
        lessons.append("the plain modulo mapping is already conflict-free for these strides; "
                       "no hashing is needed")
        _finish(base, True)
        return MappingResult(decision=base, conflict_free=True, hardware_cost=0,
                             candidates=candidates, lessons=lessons,
                             progress=progress,
                             provenance={"wall_clock_s": round(time.monotonic() - started, 1)})

    # 1b. is ANY mapping possible? A pigeonhole witness costs microseconds and, when it exists,
    #     makes every solver round and every model proposal a waste (D356).
    witness = find_impossibility(request)
    if witness is not None:
        bound = max_feasible_concurrency(request)
        say(f"impossible: {witness.explain()}")
        lessons.append(witness.explain())
        lessons.append(f"any mapping can serve at most {bound} of these accesses concurrently "
                       f"without a conflict; the request asks for {n}")
        not_established.append(
            f"no conflict-free mapping exists for {n} concurrent accesses across "
            f"{list(request.strides)} -- proved, not searched. Ask for at most {bound}, or "
            "drop a stride")
        partial: list[tuple[str, XorFold]] = []
        n_ok, m_n = _feasible_concurrency(request, say, start=bound)
        if m_n is not None:
            partial.append((f"{n_ok} concurrent (all strides)", m_n))
            progress.append({"quality": 1.0, "cost": m_n.hardware_cost(),
                             "label": f"{m_n.describe()} (at N={n_ok})", "phase": "feasible",
                             "solved": False})
        strides_ok, m_s = _feasible_strides(request, say)
        if m_s is not None and strides_ok:
            partial.append((f"strides {list(strides_ok)} at {n} concurrent", m_s))
        if len(strides_ok) <= 1:
            lessons.extend(_stride_compatibility(request, say))
        best = partial[0][1] if partial else None
        _finish(best, False)
        return MappingResult(
            decision=best, conflict_free=False,
            hardware_cost=best.hardware_cost() if best else None, candidates=candidates,
            lessons=lessons + [f"best partial answer: {lb} -- {m.describe()}" for lb, m in partial],
            not_established=not_established, progress=progress,
            provenance={"impossible": True, "wall_clock_s": round(time.monotonic() - started, 1)})

    # 2. z3, exact over the linear family
    say(f"z3: searching XOR-folds over {request.bank_bits}x{request.address_bits} taps "
        f"(budget {request.z3_seconds}s)")
    fold, trace = solve(request, log=say)
    if trace.partition is not None:
        # The solver CHOSE the lane wiring (D372): from here on it is the concrete hardware
        # every rung checks against, and the report says what to build.
        from dataclasses import replace as _replace

        request = _replace(request, stages=tuple(
            _replace(st, partition=trace.partition)
            if st.lane_key == "free" and st.partition is None else st
            for st in request.stages))
        say(f"  z3 chose the lane wiring: {[list(b) for b in trace.partition]}")
        lessons.append(f"the solver chose the lane-to-crossbar wiring jointly with the "
                       f"mapping: {[list(b) for b in trace.partition]}")
    elif any(st.lane_key == "free" and st.partition is None for st in request.stages):
        # Joint-unsat says NO wiring rescues the linear family (D372) -- but the model's
        # non-linear round still needs a concrete wiring to be judged against. Fix the least
        # constrained one: the interleave spreads the window as evenly as possible, so it
        # minimises co-located pairs, and it is the best wiring measured (D364). Chosen by
        # rule, stated in the report, never presented as the solver's finding.
        from dataclasses import replace as _replace

        request = rule_wired(request)
        say("  the wiring cannot rescue the linear family; fixing the interleave (fewest "
            "co-located pairs) so the model round has a concrete target")
        lessons.append(
            "no wiring admits a linear fold (proved over every assignment); the model round "
            "ran on the interleaved wiring, fixed by rule for having the fewest co-located "
            "pairs -- not chosen by the solver")
    counter_examples: list[str] = []
    z3_summary = trace.outcome
    if fold is not None:
        fv = check(fold, request)
        candidates.append((fold, fv, "z3"))
        for cost, clean, desc in trace.probes[:-1]:
            progress.append({"quality": clean, "cost": cost, "label": desc, "phase": "z3",
                             "solved": False})
        track(fold, fv, "z3")
        lessons.append(f"z3 found {fold.describe()} -- conflict-free for every start address, "
                       f"{fold.hardware_cost()} XOR gate(s), in {trace.rounds} round(s)")
    else:
        for stride, start in trace.counter_examples[:6]:
            counter_examples.append(f"stride {stride}, start 0x{start:x}")
        if "unsat" in z3_summary:
            lessons.append(
                f"NO XOR-fold is conflict-free for {n} concurrent accesses across strides "
                f"{list(request.strides)}: the solver proved the linear family cannot do it, "
                f"so any answer must be non-linear")

    # 3. what IS feasible, when the request is not
    partial: list[tuple[str, XorFold]] = []
    if fold is None:
        n_ok, m_n = _feasible_concurrency(request, say)
        if m_n is not None:
            partial.append((f"{n_ok} concurrent (all strides)", m_n))
            progress.append({"quality": 1.0, "cost": m_n.hardware_cost(),
                             "label": f"{m_n.describe()} (at N={n_ok})", "phase": "feasible",
                             "solved": False})
        strides_ok, m_s = _feasible_strides(request, say)
        if m_s is not None and strides_ok:
            partial.append((f"strides {list(strides_ok)} at {n} concurrent", m_s))
        if n_ok:
            lessons.append(f"the strides admit at most {n_ok} conflict-free concurrent accesses "
                           f"in the linear family (asked for {n})")

    # 4. the model, told what the solver could not do
    tried: list[tuple[str, str]] = []
    # The record, read back (D402): a resumed campaign's past refusals seed the
    # ALREADY TRIED list, so the model is told what failed before, not just in this run.
    if records is not None and records.resumed:
        past = [(c.get("describe", "?"), why[:90])
                for c, why in records.refusals(rung="exhaustive", limit=8)]
        if past:
            tried.extend(past)
            say(f"  the record seeds ALREADY TRIED with {len(past)} past refusal(s)")
    assignment_open = any(st.lane_key == "free" and st.partition is None
                          for st in request.stages)
    if propose is not None and request.llm_round > 0 and not assignment_open:
        from flux_feedback import drain_guidance

        base_summary = bv.summary(n)
        for round_ in range(1, 3):
            human = drain_guidance(feedback, human_notes, on_note=_on_note)
            try:
                proposals = propose(
                    request, baseline_summary=base_summary, z3_summary=z3_summary,
                    counter_examples=counter_examples, count=request.llm_round, tried=tried,
                    problem=request.problem, guidance=human)
            except Exception as exc:                                      # noqa: BLE001
                not_established.append(
                    f"the proposer did not run ({type(exc).__name__}: {exc!s:.100})")
                break
            say(f"model round {round_}: {len(proposals)} proposal(s)")
            if not proposals:
                break
            for mapping, why in proposals:
                v = check(mapping, request)
                candidates.append((mapping, v, "llm"))
                track(mapping, v, "llm")
                if v.conflict_free:
                    say(f"  {mapping.describe()}: CONFLICT-FREE, cost {mapping.hardware_cost()}")
                    tried.append((mapping.describe(), "conflict-free"))
                else:
                    why_not = v.summary(n)
                    refused.append((mapping.describe(), why_not))
                    tried.append((mapping.describe(), why_not[:90]))
                    if v.worst is not None:
                        counter_examples.append(
                            f"{mapping.describe()} -> stride {v.worst.stride}, start "
                            f"0x{v.worst.worst_start:x} reaches {v.worst.worst_distinct}/{n}")
                    say(f"  {mapping.describe()}: refused -- {why_not[:80]}")
            if any(v.conflict_free for _, v, who in candidates if who == "llm"):
                break

    # decision: cheapest conflict-free; otherwise the best partial
    winners = [(m, v) for m, v, _ in candidates if v.conflict_free]
    if winners:
        best, _ = min(winners, key=lambda mv: mv[0].hardware_cost())
        say(f"decision: {best.describe()} ({best.hardware_cost()} XOR-equivalent)")
        _finish(best, True)
        return MappingResult(
            decision=best, conflict_free=True, hardware_cost=best.hardware_cost(),
            candidates=candidates, refused=refused, lessons=lessons, progress=progress,
            not_established=not_established,
            provenance={"z3_rounds": trace.rounds, "z3_constraints": trace.constraints,
                        "wall_clock_s": round(time.monotonic() - started, 1)})

    not_established.append(
        f"no conflict-free mapping was found for {n} concurrent accesses across all of "
        f"{list(request.strides)}; the linear family was proved insufficient and the model's "
        f"proposals were all refused")
    partial_best = partial[0][1] if partial else None
    _finish(partial_best, False)
    return MappingResult(
        decision=partial_best, conflict_free=False,
        hardware_cost=partial_best.hardware_cost() if partial_best else None,
        candidates=candidates, refused=refused, lessons=lessons + [
            f"best partial answer: {label} -- {m.describe()}" for label, m in partial],
        not_established=not_established, progress=progress,
        provenance={"z3_rounds": trace.rounds, "z3_constraints": trace.constraints,
                    "wall_clock_s": round(time.monotonic() - started, 1)})
