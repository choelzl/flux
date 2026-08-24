"""The bank-mapping study: baseline, exact linear search, then the open family (D356) -- run
on the loop since D446 (`loop.py` is the problem; this module is the entry point and the
feasibility helpers).

THE CHAIN, cheapest and most certain first:

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

from .impossible import find_impossibility
from .mapping import Mapping, XorFold
from .problem import MappingRequest, MappingResult
from .solve_z3 import solve   # looked up through this module, so a test can stub the solver


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
    pigeonhole stage (microseconds), so the study says "each alone, never two" rather than leaving
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
              proposer: Any | None = None,
              propose: Callable[..., list[tuple[Mapping, str]]] | None = None,
              log: Callable[[str], None] | None = None,
              feedback: Any | None = None) -> MappingResult:
    """The chain on the loop (D446): `BankmapProblem.search` yields the baseline, the solver's
    fold and the model's proposals as batches, the exhaustive checker is the gate, and the
    decision is the cheapest conflict-free mapping -- or, when there is none, the best partial
    answer, labelled as partial.

    `proposer` is the model role in the loop's one shape (a `prompt -> text` callable or
    anything with `.propose`); `propose` is the older `(request, **context) -> [(mapping,
    why)]` callable and is still accepted for callers written before D446. `request.db`
    names the campaign record (D402): every checked mapping lands as a trial, the decision
    as an INFERENCE conclusion, and a resumed run seeds the model round's ALREADY TRIED
    list from its own past refusals.
    """
    from flux_loop import LoopRequest, run_loop

    from .loop import BankmapProblem

    problem = BankmapProblem(request, propose=propose)
    out = run_loop(problem, LoopRequest(db=request.db, steps=8, critique_rounds=0,
                                        prototype=False, compute=False,
                                        params={"evaluator": "bankmap"}),
                   proposer=proposer, feedback=feedback, log=log)
    lessons = list(out.lessons)
    not_established = [n for n in out.not_established
                       if not n.startswith("nothing was measured")]
    provenance: dict[str, Any] = {"wall_clock_s": round(time.monotonic() - problem.started, 1)}
    if problem.impossible:
        provenance["impossible"] = True
    elif problem.trace is not None:
        provenance.update({"z3_rounds": problem.trace.rounds,
                           "z3_constraints": problem.trace.constraints})
    refused = [(name, why) for name, why in out.refused
               if any(m.describe() == name and who == "llm" for m, _v, who in problem.candidates)]
    if out.decision is not None:
        best = problem.mappings[out.decision.name]
        return MappingResult(
            decision=best, conflict_free=True, hardware_cost=best.hardware_cost(),
            candidates=problem.candidates, refused=refused, lessons=lessons,
            progress=problem.progress, not_established=not_established,
            provenance=provenance)
    partial_best = problem.partial[0][1] if problem.partial else None
    return MappingResult(
        decision=partial_best, conflict_free=False,
        hardware_cost=partial_best.hardware_cost() if partial_best else None,
        candidates=problem.candidates, refused=refused,
        lessons=lessons + [f"best partial answer: {label} -- {m.describe()}"
                           for label, m in problem.partial],
        not_established=not_established, progress=problem.progress, provenance=provenance)


__all__ = ["run_study"]
