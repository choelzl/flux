"""Interconnect topologies: each prices area and adds its own conflicts.

A fabric is a capacity tree over bank-id bit-prefixes (bankmap's stage model, D344): `levels`
lists (bits, cap) pairs, "into each of the 2^bits bank subtrees, at most `cap` requests in
flight per cycle". A full crossbar has no levels; a slimmed butterfly trades links for blocking.

    cycles = max( ceil(rows/ports),          # port arithmetic, unavoidable
                  max bank multiplicity,      # the hash's responsibility
                  max_k max_g ceil(n_g/cap) ) # the fabric's responsibility

so a report can say which term limited a request; a design point is a pair (map function x
interconnect).

Area is the structural gate-unit rule from solutions.py (inputs x outputs x row bits per
switching level, flops at 4x a mux bit): a ranking, not um2. The whole-fabric OpenROAD flow
(D272) is the upgrade path for frontier rows.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

ROW_BITS = 128
CLIENT_PORTS = 28 + 24


@dataclass(frozen=True, slots=True)
class FabricModel:
    """`levels`: (bits, cap) capacity constraints over bank-id prefixes, root to leaf.
    Empty levels = non-blocking. `gate_units`/`buffer_bits` price it structurally."""

    name: str
    levels: tuple[tuple[int, int], ...]
    gate_units: int
    buffer_bits: int = 0
    pipe_latency: float = 1.0   # pipeline depth in cycles: Cost C's pipeline-latency term
    note: str = ""

    @property
    def area_score(self) -> float:
        return self.gate_units + 4 * self.buffer_bits

    def load(self, per_bank: np.ndarray, bank_bits: int) -> int:
        """The fabric term of the cycle law: the worst ceil(subtree traffic / cap)
        over every level and subtree. 1 means the fabric never got in the way."""
        worst = 1
        for bits, cap in self.levels:
            if cap <= 0:
                raise ValueError(f"{self.name}: level {bits} has no capacity")
            groups = per_bank.reshape(1 << bits, -1).sum(axis=1)
            need = int(groups.max())
            if need:
                worst = max(worst, math.ceil(need / cap))
        return worst

    def describe(self) -> str:
        if not self.levels:
            return f"{self.name}: non-blocking"
        lv = ", ".join(f"2^{b} groups x{c}" for b, c in self.levels)
        return f"{self.name}: capacity tree [{lv}]"


def xbar_full(banks: int, clients: int = CLIENT_PORTS) -> FabricModel:
    """Every client reaches every bank privately: nothing internal shared, max area."""
    # pipe_latency=4 (D383): a single-cycle 52:1 x 128b selector misses the 1667 ps clock
    # by over three periods on ASAP7, so it must be pipelined to ~4 stages.
    return FabricModel(name="xbar-full", levels=(),
                       gate_units=clients * banks * ROW_BITS, pipe_latency=4.0,
                       note="non-blocking; 52:1 selector needs ~4 stages at 600 MHz (measured)")


def butterfly(banks: int, bank_bits: int, slim: int = 1,
              clients: int = CLIENT_PORTS) -> FabricModel:
    """Log-stage fabric: stage k feeds 2^k subtrees through ceil(clients/(2^k * slim))
    links each. slim=1 keeps full bisection; slim=2 halves links and area and pays in
    fabric conflicts. Each link crosses one radix-2 element per stage, so a stage costs
    links x 2 x ROW_BITS gate-units."""
    levels = []
    gate_units = 0
    for k in range(1, bank_bits + 1):
        cap = max(1, math.ceil(clients / ((1 << k) * slim)))
        links = cap * (1 << k)
        levels.append((k, cap))
        gate_units += links * 2 * ROW_BITS
    return FabricModel(name=f"fly-r2{'-slim' + str(slim) if slim > 1 else ''}",
                       levels=tuple(levels), gate_units=gate_units,
                       pipe_latency=float(bank_bits),
                       note=f"{bank_bits} stages, bisection 1/{slim}")


def unit_split(banks: int, clients: int = CLIENT_PORTS) -> FabricModel:
    """Three per-unit crossbars (MU 36, VU 8, DMA 8 ports) merged 3:1 at each bank.
    Cheaper than one 52-wide crossbar; the merge allows 2 concurrent winners per bank,
    so cross-unit bursts to one bank neighbourhood queue in the fabric."""
    gate_units = (36 + 8 + 8) * banks * ROW_BITS // 2 + banks * 3 * ROW_BITS
    return FabricModel(name="unit-split", levels=((5, 2),), gate_units=gate_units,
                       pipe_latency=2.0,
                       note="per-unit crossbars, 3:1 bank merge")


def butterfly_r4(banks: int, bank_bits: int, slim: int = 1,
                 clients: int = CLIENT_PORTS) -> FabricModel:
    """Radix-4 butterfly: half the stages of radix-2 (pipeline depth), wider switch
    elements (a link crosses one radix-4 element per stage: 4 x ROW_BITS per link).
    Capacity checked at every 2-bit prefix boundary."""
    levels = []
    gate_units = 0
    stages = (bank_bits + 1) // 2
    for si in range(1, stages + 1):
        bits = min(2 * si, bank_bits)
        cap = max(1, math.ceil(clients / ((1 << bits) * slim)))
        levels.append((bits, cap))
        gate_units += cap * (1 << bits) * 4 * ROW_BITS
    return FabricModel(name=f"fly-r4{'-slim' + str(slim) if slim > 1 else ''}",
                       levels=tuple(levels), gate_units=gate_units,
                       pipe_latency=float(stages),
                       note=f"{stages} radix-4 stages, bisection 1/{slim}")


def benes(banks: int, bank_bits: int, clients: int = CLIENT_PORTS) -> FabricModel:
    """Rearrangeably non-blocking at 2*log2(N)-1 stages of radix-2 elements: crossbar
    permutation capability at a fraction of the gates. Rearrangement needs global routing
    of the whole cycle, so it pays the deepest pipeline, and a real router may fall short
    of the ideal modelled here."""
    n = max(clients, banks)
    stages = 2 * bank_bits - 1
    gate_units = stages * n * 2 * ROW_BITS
    return FabricModel(name="benes", levels=(), gate_units=gate_units,
                       pipe_latency=float(stages + 2),
                       note=f"{stages} stages + global route compute; "
                            "assumes per-cycle rearrangement")


def concentrator_xbar(banks: int, active: int,
                      clients: int = CLIENT_PORTS) -> FabricModel:
    """A concentrator picks the cycle's <=`active` winners, then a slim active x banks
    crossbar carries them, betting that peak concurrency, not port count, sizes the
    fabric. Blocking is a single root capacity."""
    gate_units = clients * active * ROW_BITS // 4 + active * banks * ROW_BITS
    return FabricModel(name=f"cx-{active}", levels=((0, active),),
                       gate_units=gate_units, pipe_latency=2.0,
                       note=f"concentrate to {active}, then {active}x{banks} xbar")


def hier_xbar(banks: int, uplinks: int, groups_bits: int = 2,
              clients: int = CLIENT_PORTS) -> FabricModel:
    """Two-level crossbar: clients into 2^groups_bits group switches through `uplinks`
    links each, then a small per-group crossbar to that group's banks."""
    groups = 1 << groups_bits
    per_group_banks = banks // groups
    gate_units = (clients * groups * uplinks * ROW_BITS // 4
                  + groups * uplinks * per_group_banks * ROW_BITS)
    return FabricModel(name=f"hier-{groups}x{uplinks}",
                       levels=((groups_bits, uplinks),), gate_units=gate_units,
                       pipe_latency=2.0,
                       note=f"{groups} groups, {uplinks} uplinks each")


def ring(banks: int, stops: int = 8, clients: int = CLIENT_PORTS) -> FabricModel:
    """The cheap extreme: a slotted ring with `stops` concurrent transactions. Almost
    no gates, tight root capacity, half-a-ring average hop latency; the area anchor."""
    gate_units = (clients + banks) * ROW_BITS // 2
    return FabricModel(name=f"ring-{stops}", levels=((0, stops),),
                       gate_units=gate_units, pipe_latency=float(banks // 4),
                       note=f"{stops} slots, ~{banks // 4}-hop mean latency")


def generate_fabrics(bank_bits: int) -> list[FabricModel]:
    """The interconnect candidate space (D392): parameter grids over every family plus
    the fixed anchors, ~30 candidates. Screened under a reference mapping; the Pareto
    set is kept."""
    banks = 1 << bank_bits
    out = [xbar_full(banks), benes(banks, bank_bits), unit_split(banks)]
    for slim in (1, 2, 4):
        out.append(butterfly(banks, bank_bits, slim=slim))
        out.append(butterfly_r4(banks, bank_bits, slim=slim))
    for gb in (1, 2, 3):
        for up in (4, 8, 16):
            out.append(hier_xbar(banks, uplinks=up, groups_bits=gb))
    for active in (12, 16, 24, 32):
        out.append(concentrator_xbar(banks, active=active))
    for stops in (4, 8, 16):
        out.append(ring(banks, stops=stops))
    # de-duplicate by name (grids can collide on canonical corners)
    seen: dict[str, FabricModel] = {}
    for f in out:
        seen.setdefault(f.name, f)
    return list(seen.values())
