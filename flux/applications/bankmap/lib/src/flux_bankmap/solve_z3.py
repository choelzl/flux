"""z3 finds an XOR-fold mapping: propose under sampled constraints, verify exhaustively, refine.

THE LINEARITY THAT MAKES THIS TRACTABLE. An XOR-fold is a linear map M over GF(2), so for two
addresses x and y the banks differ iff M(x XOR y) != 0. Conflict-freeness of a window is therefore
"no pairwise DIFFERENCE in the window lands in M's kernel" -- a constraint on differences, not
on addresses. Each difference d contributes one clause: OR over bank bits i of (XOR over the set
bits j of d of t[i][j]), where t[i][j] is the Boolean "address bit j feeds bank bit i". That is a
small propositional problem, and z3 solves it in milliseconds.

WHY IT IS A LOOP AND NOT ONE CALL. The differences (a + k*s) XOR (a + l*s) depend on the start
address a through carries, and there are 2^address_bits starts. Encoding them all is possible
but pointless: most repeat. So the solver is seeded with the differences from a window of starts,
proposes a mapping, the exhaustive checker (`check.py`) tries EVERY start, and any counter-example
it finds contributes its differences to the next round. Counter-example-guided synthesis, with
the checker as the oracle -- which is the same shape as the rest of this repository's loops: a
cheap proposer and an authority that decides.

COST IS THE OBJECTIVE, conflict-freeness the constraint. Among conflict-free mappings the
hardware cost is the number of address bits folded in beyond one per bank bit -- two-input XOR
gates on the address path -- and z3's optimiser minimises it directly.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from .check import check
from .mapping import XorFold
from .problem import MappingRequest


@dataclass
class SolveTrace:
    """What the solver did, round by round, for the report."""

    rounds: int = 0
    constraints: int = 0
    counter_examples: list[tuple[int, int]] = field(default_factory=list)   # (stride, start)
    elapsed_s: float = 0.0
    outcome: str = ""
    #: Each CEGIS round's candidate, as (cost, clean fraction, description) -- the solver's own
    #: convergence, for the progress figure (D373).
    probes: list[tuple[int, float, str]] = field(default_factory=list)
    #: The lane-to-crossbar wiring the solver chose, when a stage's assignment was FREE (D372).
    partition: tuple[tuple[int, ...], ...] | None = None


def window_differences(stride: int, n: int, starts: np.ndarray, address_bits: int,
                       pairs: list[tuple[int, int]] | None = None) -> set[int]:
    """Every pairwise XOR difference inside the windows that begin at `starts`.

    `pairs` restricts to the window positions that must differ -- for a laned stage, only the
    positions that share one input crossbar.
    """
    mask = (1 << address_bits) - 1
    out: set[int] = set()
    for k, l in (pairs if pairs is not None else
                 [(k, l) for k in range(n) for l in range(k + 1, n)]):
        d = ((starts + k * stride) & mask) ^ ((starts + l * stride) & mask)
        out.update(int(x) for x in np.unique(d))
    out.discard(0)
    return out


def _chunk_pairs(stage, n: int) -> list[tuple[int, int]]:
    """The window positions a stage's capacity relates: every pair within one input chunk."""
    from itertools import combinations

    return [pair for chunk in stage.groups(n) for pair in combinations(chunk, 2)]


def solve(request: MappingRequest, *, seed_starts: int = 256, max_rounds: int = 12,
          timeout_s: float | None = None,
          log: Callable[[str], None] = lambda _m: None) -> tuple[XorFold | None, SolveTrace]:
    """An XOR-fold that the checker accepts for every stride, or None with the trace saying why."""
    import z3

    from flux_profile import phase

    bb, ab, n = request.bank_bits, request.address_bits, request.concurrent
    _budget = timeout_s if timeout_s is not None else request.z3_seconds
    with phase("solver:z3 (xor-fold search)",
               why=f"{n} concurrent over {1 << bb} banks",
               strides=",".join(str(x) for x in request.strides),
               budget_s=_budget):
        return _solve_inner(request, z3, seed_starts=seed_starts,
                            max_rounds=max_rounds, timeout_s=timeout_s, log=log)


def _solve_inner(request: MappingRequest, z3, *, seed_starts: int, max_rounds: int,
                 timeout_s: float | None,
                 log: Callable[[str], None]) -> tuple[XorFold | None, SolveTrace]:
    bb, ab, n = request.bank_bits, request.address_bits, request.concurrent
    budget = timeout_s if timeout_s is not None else request.z3_seconds
    trace = SolveTrace()
    started = time.monotonic()

    opt = z3.Optimize()
    opt.set("timeout", int(max(1, budget) * 1000))
    t = [[z3.Bool(f"t_{i}_{j}") for j in range(ab)] for i in range(bb)]
    for i in range(bb):
        opt.add(z3.Or(*t[i]))                            # a constant bank bit halves the banks
        if request.max_xor_inputs:
            opt.add(z3.PbLe([(v, 1) for v in t[i]], request.max_xor_inputs))
        for h in range(i):                               # so do two identical bank bits
            opt.add(z3.Or(*[z3.Xor(t[i][j], t[h][j]) for j in range(ab)]))
    cost = z3.Sum([z3.If(v, 1, 0) for row in t for v in row])
    opt.minimize(cost)

    def differs_on(d: int, rows) -> "z3.BoolRef":
        """`M d` is non-zero on the given bank bits: some row has an odd number of taps in d."""
        bits = [j for j in range(ab) if (d >> j) & 1]
        return z3.Or(*[_xor_all([t[i][j] for j in bits]) for i in rows])

    def clause_for(d: int):
        # bank(d) != 0  <=>  some bank bit i has an odd number of its taps set in d
        return differs_on(d, range(bb))

    # CROSSBAR STAGES. A stage with capacity 1 is a stricter bank: two accesses in a window may
    # not share its resource, which is "differ on the stage's bank bits" -- the same pairwise
    # difference clause, restricted to those rows. A stage with capacity c > 1 is not pairwise:
    # among any c+1 accesses of one window, at least one pair must differ on the stage's bits.
    # That is a clause per (window, (c+1)-subset), so it is generated per start address rather
    # than per difference, and refined by the same counter-example loop.
    free = [st for st in request.stages if st.lane_key == "free" and st.partition is None]
    if len(free) > 1:
        raise NotImplementedError("one free-assignment stage per request")
    strict = [st for st in request.stages
              if st.capacity == 1 and (st.lane_key != "free" or st.partition is not None)]
    loose = [st for st in request.stages if st.capacity > 1]

    # A FREE assignment (D372): which window positions share an input crossbar is a decision
    # variable. y[k][b] says position k rides crossbar b; a pair on one crossbar must differ
    # on the stage's bits for EVERY window, which collapses per pair to "every difference of
    # its offset is group-safe" -- one shared `ok` literal per offset value, defined by
    # implications that the counter-example loop extends incrementally.
    y: list[list["z3.BoolRef"]] = []
    same: dict[tuple[int, int], "z3.BoolRef"] = {}
    ok_of: dict[int, "z3.BoolRef"] = {}
    ok_seen: dict[int, set[int]] = {}
    if free:
        fs = free[0]
        y = [[z3.Bool(f"y_{k}_{b}") for b in range(fs.blocks)] for k in range(n)]
        for k in range(n):
            opt.add(z3.PbEq([(v, 1) for v in y[k]], 1))
            for b in range(k + 1, fs.blocks):
                opt.add(z3.Not(y[k][b]))          # symmetry: position k rides a crossbar <= k
        for b in range(fs.blocks):
            opt.add(z3.PbLe([(y[k][b], 1) for k in range(n)], fs.lanes))
        for k in range(n):
            for l in range(k + 1, n):
                lit = z3.Bool(f"same_{k}_{l}")
                same[(k, l)] = lit
                for b in range(fs.blocks):
                    opt.add(z3.Or(z3.Not(y[k][b]), z3.Not(y[l][b]), lit))

        def ok_for(offset: int) -> "z3.BoolRef":
            if offset not in ok_of:
                ok_of[offset] = z3.Bool(f"ok_{offset}")
                ok_seen[offset] = set()
            return ok_of[offset]

        for k in range(n):
            for l in range(k + 1, n):
                for stride_ in request.strides:
                    opt.add(z3.Or(z3.Not(same[(k, l)]), ok_for((l - k) * stride_)))

    def stage_clause(d: int, st) -> "z3.BoolRef":
        return differs_on(d, list(st.bits))

    seen_windows: set[tuple[int, int]] = set()

    def add_windows(stride: int, s: np.ndarray) -> int:
        """Per-window clauses for the capacity > 1 stages."""
        from itertools import combinations

        mask = (1 << ab) - 1
        added = 0
        for a in (int(x) for x in np.unique(s)):
            if (stride, a) in seen_windows:
                continue
            seen_windows.add((stride, a))
            addrs = [(a + k * stride) & mask for k in range(n)]
            for st in loose:
                for chunk in st.groups(n):
                    for subset in combinations(chunk, st.capacity + 1):
                        pairs = [addrs[i] ^ addrs[j] for i, j in combinations(subset, 2)]
                        opt.add(z3.Or(*[stage_clause(d, st) for d in pairs if d]))
                        added += 1
        return added

    seen_d: set[int] = set()
    seen_stage_d: dict[int, set[int]] = {i: set() for i in range(len(strict))}
    rng = np.random.default_rng(0)
    starts = np.unique(np.concatenate([
        np.arange(min(seed_starts, 1 << ab), dtype=np.int64),
        rng.integers(0, 1 << ab, size=seed_starts, dtype=np.int64)]))

    def add_differences(stride: int, s: np.ndarray) -> int:
        fresh = window_differences(stride, n, s, ab) - seen_d
        for d in fresh:
            opt.add(clause_for(d))
        seen_d.update(fresh)
        added = len(fresh)
        if free:
            # New starts refine every free-stage offset: ok_o only holds when EVERY difference
            # of that offset keeps the pair in distinct groups. Implications only, so they
            # accumulate soundly across rounds.
            for offset, lit in ok_of.items():
                fresh_o = window_differences(offset, 2, s, ab) - ok_seen[offset]
                for d in fresh_o:
                    opt.add(z3.Or(z3.Not(lit), stage_clause(d, free[0])))
                ok_seen[offset].update(fresh_o)
                added += len(fresh_o)
        # A capacity-1 stage is pairwise too, but over ITS pairs: every pair of the window when
        # one crossbar sees the whole window, only the pairs within a chunk when it is laned.
        for i, st in enumerate(strict):
            pairs = _chunk_pairs(st, n) if st.lanes else None
            fresh_st = window_differences(stride, n, s, ab, pairs) - seen_stage_d[i]
            for d in fresh_st:
                opt.add(stage_clause(d, st))
            seen_stage_d[i].update(fresh_st)
            added += len(fresh_st)
        return added + (add_windows(stride, s) if loose else 0)

    for stride in request.strides:
        trace.constraints += add_differences(stride, starts)

    for round_ in range(1, max_rounds + 1):
        trace.rounds = round_
        if time.monotonic() - started > budget:
            trace.outcome = f"budget of {budget:.0f}s exhausted after {round_ - 1} round(s)"
            break
        verdict = opt.check()
        if verdict != z3.sat:
            trace.outcome = (f"no XOR-fold satisfies the {trace.constraints} constraint(s) "
                             f"({verdict})" if verdict == z3.unsat else f"solver returned {verdict}")
            break
        model = opt.model()
        taps = tuple(tuple(j for j in range(ab) if z3.is_true(model.eval(t[i][j], True)))
                     for i in range(bb))
        candidate = XorFold(taps=taps, name="z3")
        checked = request
        if free:
            blocks: list[list[int]] = [[] for _ in range(free[0].blocks)]
            for k in range(n):
                for b in range(free[0].blocks):
                    if z3.is_true(model.eval(y[k][b], True)):
                        blocks[b].append(k)
                        break
            trace.partition = tuple(tuple(b) for b in blocks if b)
            from dataclasses import replace as _replace

            checked = _replace(request, stages=tuple(
                _replace(st, partition=trace.partition) if st is free[0] else st
                for st in request.stages))
        result = check(candidate, checked)
        trace.probes.append((candidate.hardware_cost(), result.clean_fraction,
                             candidate.describe()))
        if result.conflict_free:
            trace.outcome = f"conflict-free after {round_} round(s), {trace.constraints} constraints"
            trace.elapsed_s = time.monotonic() - started
            log(f"  z3: {trace.outcome}; cost {candidate.hardware_cost()} XOR")
            return candidate, trace
        # refine on every failing stride's worst window, plus a neighbourhood of it
        added = 0
        for v in result.per_stride:
            if v.conflict_free:
                continue
            trace.counter_examples.append((v.stride, v.worst_start))
            near = np.arange(max(0, v.worst_start - 16), min(1 << ab, v.worst_start + 17),
                             dtype=np.int64)
            added += add_differences(v.stride, near)
        trace.constraints += added
        log(f"  z3 round {round_}: proposal fails ({result.summary(n)}); +{added} constraint(s)")
        if added == 0:
            trace.outcome = "counter-example added no new constraint; the family cannot express it"
            break
    else:
        trace.outcome = f"{max_rounds} rounds without convergence"
    trace.elapsed_s = time.monotonic() - started
    log(f"  z3: {trace.outcome}")
    return None, trace


def _xor_all(vars_):
    import z3

    if not vars_:
        return z3.BoolVal(False)
    acc = vars_[0]
    for v in vars_[1:]:
        acc = z3.Xor(acc, v)
    return acc
