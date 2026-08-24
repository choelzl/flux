"""Interconnects between the requesters and the banks, as the stages they add (D364).

Every topology here reduces to the one question the checker, the solver and the pigeonhole all
answer: which accesses of a window can meet at one resource, and how many that resource can
carry. A `Stage` says it with three things -- the bank bits that identify the resource, the
lanes that share it (consecutive chunks, or classes modulo a shuffle), and a capacity. What a
topology is, then, is a list of stages plus what it assumes:

  crossbar          one switch, every input to every bank: no stage, the bank is the only conflict
  staged GxH[xK]    a tree of crossbars routing on bank bits from the top; the first stage may be
                    split into small crossbars that each see `lanes` consecutive accesses
  omega N           log2 N stages of 2x2 switches with a perfect shuffle between them, self-routed
                    on destination bits MSB first: after stage j a packet from source s to bank d
                    is on the link labelled (low n-j bits of s, top j bits of d), so two packets
                    collide there iff their sources agree modulo 2^(n-j) AND their banks agree on
                    the top j bits -- a "mod" lane grouping on the top bank bits, one stage per j
  butterfly N       the mirror: stage j keys on the low j bank bits, lanes that agree on their
                    high bits (chunks of 2^j) share it -- the LSB-first delta network
  clos n,m,r        r ingress n x m switches, m middle r x r, r egress m x n, with per-cycle
                    (rearrangeable) routing: an ingress switch carries at most m of its n lanes,
                    an egress switch at most m accesses, and the middle assignment then exists
                    (Konig: a bipartite multigraph of max degree m is m-edge-colourable). With
                    m >= n both bounds are vacuous and only the bank conflicts remain.
  benes N           a Clos of 2x2 switches, rearrangeably non-blocking: no stage at all

The blocking ones (staged, omega, butterfly) assume SELF-ROUTING: the path is a function of the
destination, so the stage is a real conflict point. The non-blocking ones (clos with m >= n,
benes) assume a per-cycle route computation; with greedy or fixed routing they block like a
staged network, and that is a different request -- say so with explicit `--stage`s.
"""

from __future__ import annotations

from dataclasses import dataclass

from .problem import InvalidRequest, Stage


@dataclass(frozen=True)
class Topology:
    name: str
    stages: tuple[Stage, ...] = ()
    notes: tuple[str, ...] = ()


def _bank_bits(banks: int) -> int:
    if banks < 2 or banks & (banks - 1):
        raise InvalidRequest(f"banks must be a power of two >= 2, got {banks}")
    return banks.bit_length() - 1


def crossbar_stages(bank_bits: int, layout: str, capacities: tuple[int, ...] | None = None,
                    lanes: int | None = None) -> tuple[Stage, ...]:
    """Stages for a `GxH[xK...]` layout, e.g. "2x4" over 8 banks: stage 1 routes on the top bit
    into 2 groups, stage 2 on the low two bits into 4 banks within a group.

    The product must equal the bank count. Capacities default to 1 for every stage; a stage
    with parallel links gets a larger one. The LAST stage's resource is the bank itself, so it
    is not emitted as a stage: bank-level checking already covers it. `lanes` is the input
    width of the FIRST stage's crossbars: "7 4x4s feeding 4 7x8s" over 32 banks is `4x8` with
    `lanes=4` -- each 4x4 must send its four accesses to four different 7x8s.
    """
    sizes = [int(x) for x in layout.lower().split("x")]
    if any(s < 1 or s & (s - 1) for s in sizes):
        raise InvalidRequest(f"crossbar layout {layout!r}: every factor must be a power of two")
    total = 1
    for s in sizes:
        total *= s
    if total != (1 << bank_bits):
        raise InvalidRequest(f"crossbar layout {layout!r} spans {total} banks, not {1 << bank_bits}")
    caps = list(capacities or [1] * len(sizes))
    if len(caps) != len(sizes):
        raise InvalidRequest("one capacity per stage")
    # stage i selects on the next `width_i` bits from the top down
    stages: list[Stage] = []
    hi = bank_bits
    for i, s in enumerate(sizes[:-1]):
        width = s.bit_length() - 1
        lo = hi - width
        stages.append(Stage(bits=tuple(range(lo, hi)), capacity=caps[i],
                            name=f"stage {i + 1} ({s} groups)", lanes=lanes if i == 0 else None))
        hi = lo
    return tuple(stages)


def crossbar(banks: int) -> Topology:
    """A single full crossbar: only the bank can conflict."""
    _bank_bits(banks)
    return Topology(name=f"full {banks}x{banks} crossbar",
                    notes=("a full crossbar adds no conflict point: bank-level "
                           "conflict-freeness is the whole requirement",))


def staged(banks: int, layout: str, capacities: tuple[int, ...] | None = None,
           lanes: int | None = None) -> Topology:
    """A tree of crossbars, e.g. `4x8` with `lanes=4`: seven 4x4s feeding four 7x8s."""
    stages = crossbar_stages(_bank_bits(banks), layout, capacities, lanes=lanes)
    name = f"staged crossbar {layout}" + (f", first stage split into {lanes}-lane crossbars"
                                          if lanes else "")
    return Topology(name=name, stages=stages)


def omega(banks: int, inputs: int | None = None) -> Topology:
    """A shuffle-exchange (omega) network of 2x2 switches, self-routed MSB first."""
    n = _bank_bits(banks)
    if inputs is not None and inputs != banks:
        raise InvalidRequest(f"an omega network is square: {banks} inputs for {banks} banks")
    stages = tuple(
        Stage(bits=tuple(range(n - j, n)), capacity=1, lanes=1 << (n - j), lane_key="mod",
              name=f"omega stage {j} (link = low {n - j} source bits, top {j} bank bits)")
        for j in range(1, n))
    return Topology(name=f"omega network, {banks} inputs, {n} stages of 2x2 switches",
                    stages=stages,
                    notes=("self-routed on destination bits, so every internal link is a "
                           "conflict point; lane i is network input i",))


def butterfly(banks: int, inputs: int | None = None) -> Topology:
    """A butterfly (LSB-first delta) network: the mirror of omega's collision structure."""
    n = _bank_bits(banks)
    if inputs is not None and inputs != banks:
        raise InvalidRequest(f"a butterfly network is square: {banks} inputs for {banks} banks")
    stages = tuple(
        Stage(bits=tuple(range(0, j)), capacity=1, lanes=1 << j, lane_key="chunk",
              name=f"butterfly stage {j} (link = high source bits, low {j} bank bits)")
        for j in range(1, n))
    return Topology(name=f"butterfly network, {banks} inputs, {n} stages of 2x2 switches",
                    stages=stages,
                    notes=("self-routed on destination bits, so every internal link is a "
                           "conflict point; lane i is network input i",))


def clos(banks: int, n: int, m: int, r: int) -> Topology:
    """A three-stage Clos(n, m, r): r ingress n x m, m middle r x r, r egress m x n."""
    bb = _bank_bits(banks)
    if n * r != banks:
        raise InvalidRequest(f"clos({n},{m},{r}) has {n * r} outputs, not {banks} banks")
    if r < 1 or r & (r - 1):
        raise InvalidRequest("the egress switch count r must be a power of two here")
    rb = r.bit_length() - 1
    stages = (
        Stage(bits=(), capacity=m, lanes=n, lane_key="chunk",
              name=f"clos ingress switch ({n}x{m}): {m} links out"),
        Stage(bits=tuple(range(bb - rb, bb)), capacity=m,
              name=f"clos egress switch ({m}x{n}): {m} links in"),
    )
    note = (f"rearrangeably non-blocking with per-cycle routing: m={m} >= n={n}, so the "
            "ingress and egress bounds are vacuous and only bank conflicts remain"
            if m >= n else
            f"blocking: m={m} < n={n}, so an ingress switch can pass only {m} of its {n} lanes "
            f"per cycle and an egress switch accept only {m}; the middle assignment then exists "
            "whenever both bounds hold (Konig)")
    return Topology(name=f"Clos({n},{m},{r}) over {banks} banks", stages=stages, notes=(note,))


def benes(banks: int) -> Topology:
    """A Benes network: a Clos of 2x2 switches, rearrangeably non-blocking."""
    n = _bank_bits(banks)
    return Topology(name=f"Benes network, {banks} inputs, {2 * n - 1} stages of 2x2 switches",
                    notes=("rearrangeably non-blocking with per-cycle routing: every "
                           "conflict-free bank assignment is routable, so the network adds no "
                           "constraint; with self-routing it would block like a butterfly",))


def parse(spec: str, banks: int, *, capacities: tuple[int, ...] | None = None,
          lanes: int | None = None) -> Topology:
    """`crossbar` | `staged:4x8` | `omega` | `butterfly` | `clos:n,m,r` | `benes`."""
    kind, _, arg = spec.strip().lower().partition(":")
    if kind in ("crossbar", "full"):
        return crossbar(banks)
    if kind in ("staged", "tree"):
        if not arg:
            raise InvalidRequest("staged needs a layout, e.g. staged:4x8")
        return staged(banks, arg, capacities, lanes)
    if kind == "omega":
        return omega(banks)
    if kind in ("butterfly", "delta", "banyan"):
        return butterfly(banks)
    if kind == "clos":
        try:
            n, m, r = (int(x) for x in arg.split(","))
        except ValueError:
            raise InvalidRequest("clos needs n,m,r, e.g. clos:4,4,8") from None
        return clos(banks, n, m, r)
    if kind == "benes":
        return benes(banks)
    raise InvalidRequest(f"unknown topology {spec!r}; one of crossbar, staged:GxH, omega, "
                         "butterfly, clos:n,m,r, benes")


__all__ = ["Topology", "benes", "butterfly", "clos", "crossbar", "crossbar_stages", "omega", "parse",
           "staged"]
