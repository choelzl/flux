"""When NO mapping can exist: a pigeonhole proof that costs microseconds and saves the search.

The first live run of this study spent three and a half minutes asking a model for non-linear
mappings for strides {1, 8, 16, 17} at eight concurrent accesses across eight banks -- after z3
had proved the linear family insufficient -- and every proposal was refused. None could have
worked, and the reason is elementary: a stride-1 window requires a, a+1, ..., a+7 to sit in
eight distinct banks, and a stride-8 window requires a and a+8 to differ too. Then a+8 must also
differ from a+1 (difference 7, inside a stride-1 window starting at a+1), from a+2 (difference 6),
and so on down to a+7. That is NINE addresses that must pairwise occupy distinct banks, with
eight banks to put them in. No function of the address can do it -- linear, hashed, prime
modulus, anything.

The general form: the request fixes a set D of differences that must never share a bank
(D = {k*s : s in strides, 1 <= k < N}). Any set of addresses whose pairwise differences all lie
in D must be pairwise distinct in bank, so if such a set is larger than B the request is
impossible for every mapping. Finding that set is a clique search in a small graph, and it
answers in microseconds a question the solver and the model would otherwise chase for minutes.

Every capacity-1 resource has such a set. The bank's is D above. A crossbar stage that sees the
whole window has the same D on fewer resources. A LANED stage -- one input crossbar per chunk
of `lanes` consecutive accesses -- relates only pairs whose positions fall in one chunk, and a
pair at difference k*s always sits at positions 0 and k of some window, so its set is
D = {k*s : k < min(N, lanes)}. That is where "strides 1 and 2, four lanes, four groups" dies:
D = {1, 2, 3} u {2, 4, 6}, and 0..4 are five addresses that must pairwise differ in group (D363).

When no clique is large enough the graph may still not be colourable with the resources at
hand -- chromatic number exceeds clique number -- so a bounded SAT colouring is the second
proof: if no assignment of R resources to the addresses 0..W respects every must-differ pair,
no mapping exists, since a mapping restricted to 0..W would be such an assignment.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from .problem import MappingRequest


@dataclass(frozen=True)
class Impossibility:
    """A witness: addresses that must all be in distinct resources, more of them than exist."""

    addresses: tuple[int, ...]
    banks: int                                  # how many of the resource there are
    resource: str = "bank"                      # which resource ran out
    proof: str = "clique"                       # "clique" or "colouring"

    def explain(self) -> str:
        a = self.addresses
        if self.proof == "colouring":
            return (f"impossible for ANY mapping: no assignment of {self.banks} {self.resource}"
                    f" resources to the addresses 0..{a[-1]} respects every pair that must differ "
                    f"(a pair at difference k*stride, k < N, or k < lanes for a laned stage) -- "
                    f"proved by SAT over that window; a mapping restricted to it would be such "
                    "an assignment")
        return (f"impossible for ANY mapping: the {len(a)} addresses {list(a)} must pairwise "
                f"occupy distinct {self.resource} resources (every pairwise difference is "
                f"k*stride for some stride and k < N -- or k < lanes, for a laned stage), but "
                f"there are only {self.banks}")


def difference_set(request: MappingRequest, reach: int | None = None) -> set[int]:
    """The differences that must never share a resource: k*stride for 1 <= k < `reach` (N)."""
    reach = request.concurrent if reach is None else reach
    return {k * s for s in request.strides for k in range(1, reach)}


def resources_of(request: MappingRequest) -> list[tuple[str, int, set[int]]]:
    """Every capacity-1 resource on the way to the bank, as (name, count, must-differ set)."""
    out = [("bank", request.banks, difference_set(request))]
    for st in request.stages:
        if st.capacity != 1:
            continue                    # not a pairwise constraint; left to the solver
        offsets = st.pair_offsets(request.concurrent)
        d = {j * s for s in request.strides for j in offsets}
        out.append((st.name or f"stage on bank bits {list(st.bits)}", st.resources(), d))
    return out


def _clique(d: set[int], need: int, limit: int) -> list[int]:
    """A clique of `need` addresses in 0..limit whose pairwise differences all lie in `d`, or []."""
    # Neighbours by offset, not by scanning every pair: at 3,856 nodes (stride 256, N=16) the
    # pairwise scan took seconds per call and this runs once per stride pair.
    adj = {u: {v for diff in d for v in (u - diff, u + diff) if 0 <= v <= limit}
           for u in range(limit + 1)}
    best: list[int] = []

    def grow(clique: list[int], cands: list[int]) -> bool:
        nonlocal best
        if len(clique) >= need:
            best = list(clique)
            return True
        if len(clique) + len(cands) < need:
            return False
        for i, v in enumerate(cands):
            rest = [w for w in cands[i + 1:] if w in adj[v]]
            if grow(clique + [v], rest):
                return True
        return False

    # every clique can be shifted to start at 0, so anchor the search there
    grow([0], sorted(adj[0]))
    return best


def uncolourable_window(d: set[int], colours: int, limit: int, *,
                        seconds: float = 5.0) -> int | None:
    """The smallest tried window 0..W whose must-differ graph is not `colours`-colourable, or None.

    Windows grow geometrically from a few times the colour count up to `limit`; an unsat at any
    of them is a proof, a sat at the largest is not a disproof (the graph beyond it may still
    fail), and a timeout is silence. Bounded, because this is a proof attempt in the path of
    every study, not the study.
    """
    if not d:
        return None
    try:
        import z3
    except ImportError:                                                  # pragma: no cover
        return None
    windows = sorted({min(limit, w) for w in (4 * colours, 16 * colours, 64 * colours, limit)})
    for w in windows:
        solver = z3.Solver()
        solver.set("timeout", int(seconds * 1000))
        # One Boolean per (address, colour), pure propositional: the 257-node, 4-colour instance
        # that refutes strides 1 and 256 is unsat in 0.4 s this way, 7 s as 2-bit bit-vectors
        # and past the timeout as integers. The proof is a long chain of forced equalities
        # (period 4, then a and a+256 alike), which is what unit propagation is for.
        x = [[z3.Bool(f"x{u}_{k}") for k in range(colours)] for u in range(w + 1)]
        for u in range(w + 1):
            solver.add(z3.Or(*x[u]))
            for diff in d:
                if u + diff <= w:
                    for k in range(colours):
                        solver.add(z3.Or(z3.Not(x[u][k]), z3.Not(x[u + diff][k])))
        verdict = solver.check()
        if verdict == z3.unsat:
            return w
        if verdict != z3.sat:
            return None                 # timeout: no claim either way
    return None


def find_impossibility(request: MappingRequest, *, window: int | None = None,
                       colouring: bool = True) -> Impossibility | None:
    """A proof that no mapping can serve the request, if one is found.

    Per capacity-1 resource -- the bank, then every strict crossbar stage on its own must-differ
    set -- first a clique larger than the resource count (microseconds), then, if none, a
    bounded SAT colouring (seconds). Stages with capacity above one are not pairwise
    constraints and are left to the solver.

    Bounded search: candidates are the addresses within `window` of 0 (the graph is translation
    invariant, so starting at 0 loses nothing). The window defaults to the largest difference
    plus the concurrency, which is where any clique built from these differences lives.
    """
    found = resources_of(request)
    for name, count, d in found:
        if not d:
            continue
        limit = window if window is not None else max(d) + request.concurrent
        clique = _clique(d, count + 1, limit)
        if clique:
            return Impossibility(addresses=tuple(clique), banks=count, resource=name)
    if colouring:
        # Tightest resource first: a four-group stage is refuted in well under a second, while
        # a 32-colour instance over a thousand addresses can spend the whole budget being
        # satisfiable. The pass as a whole is bounded, because it sits in the path of every
        # study and of every stride-pair proof.
        started = time.monotonic()
        for name, count, d in sorted(found, key=lambda x: x[1]):
            if not d or time.monotonic() - started > 12.0:
                continue
            limit = window if window is not None else min(max(d) + request.concurrent, 1024)
            w = uncolourable_window(d, count, limit, seconds=4.0)
            if w is not None:
                return Impossibility(addresses=tuple(range(w + 1)), banks=count,
                                     resource=name, proof="colouring")
    return None


def max_feasible_concurrency(request: MappingRequest) -> int:
    """The largest N for which no pigeonhole witness exists. An upper bound on what any mapping
    can achieve; the solver decides what the linear family actually reaches. Clique proofs
    only: this is called once per N on the way down, and a colouring attempt per step would
    cost more than the answer is worth."""
    from dataclasses import replace

    for n in range(request.concurrent, 0, -1):
        if find_impossibility(replace(request, concurrent=n), colouring=False) is None:
            return n
    return 0
