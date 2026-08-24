"""Conflict accounting: from bank-row sets to cycles, for all three conflict categories.

The single-ported-bank law: in one cycle a bank serves at most one row, and a client
uses at most its port count. So a request set is served in
`max(ceil(rows/ports), max_bank_multiplicity)` cycles -- the first term is the port
bound (unavoidable arithmetic), the second is the CONFLICT term the whole application
exists to minimize. Categories (problem statement): intra-operand (one request),
intra-unit (operands of one unit sharing a cycle), system (all units + DMA).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .model import Memory, TileAccess


@dataclass(frozen=True, slots=True)
class BankHash:
    """A concrete bank-mapping function: rows -> bank ids. Wraps a flux_bankmap Mapping
    (mod/XOR family, D344) with the two policy levers solutions pull: `group_bits`
    restricts a tensor to a 2^(m-group_bits)-bank group at `group_base` (space
    separation), leaving fewer effective bank bits."""

    mapping: object            # flux_bankmap.mapping.Mapping (duck-typed: banks_of)
    bank_bits: int
    group_bits: int = 0        # bits of the bank id fixed by placement, not by hash
    group_base: int = 0        # which group (0 .. 2^group_bits - 1)

    @property
    def effective_bits(self) -> int:
        return self.bank_bits - self.group_bits

    def banks_of(self, rows: np.ndarray) -> np.ndarray:
        eff = self.effective_bits
        low = self.mapping.banks_of(rows.astype(np.uint64), eff).astype(np.int64)
        return (self.group_base << eff) | low


@dataclass(frozen=True, slots=True)
class CycleReport:
    cycles: int
    port_bound: int           # ceil(rows/ports): the floor no hash can beat
    conflict_bound: int       # max bank multiplicity: what the HASH answers for
    rows: int
    fabric_bound: int = 1     # max subtree overload: what the FABRIC answers for

    @property
    def conflict_limited(self) -> bool:
        return max(self.conflict_bound, self.fabric_bound) > self.port_bound


def intra_operand(access: TileAccess, mem: Memory, hash_of,
                  fabric=None) -> CycleReport:
    """`hash_of(layout) -> BankHash` lets per-tensor (metadata-driven) hashes exist;
    a global hash is just a constant function. `fabric` (a FabricModel, or None for an
    ideal non-blocking interconnect) contributes its own bound: a blocking topology
    can serialize a request the hash spread perfectly."""
    rows = access.rows(mem)
    if rows.size == 0:
        return CycleReport(cycles=0, port_bound=0, conflict_bound=0, rows=0)
    counts = np.bincount(hash_of(access.layout).banks_of(rows), minlength=mem.banks)
    mult = int(counts.max())
    fab = fabric.load(counts, mem.m) if fabric is not None else 1
    port = math.ceil(rows.size / access.ports)
    return CycleReport(cycles=max(port, mult, fab), port_bound=port,
                       conflict_bound=mult, rows=int(rows.size), fabric_bound=fab)


def shared_cycle(accesses: list[TileAccess], mem: Memory, hash_of,
                 fabric=None) -> CycleReport:
    """Several operands issued in the SAME cycle (intra-unit, or system-wide when the
    list spans units): per-bank load adds up, and each operand still has its own port
    bound. One extra law: a bank port is read-or-write per cycle, but a row is a row --
    multiplicity already counts reads and writes together, which is exactly right for
    single-ported banks."""
    per_bank = np.zeros(mem.banks, dtype=np.int64)
    port_bound = 0
    total_rows = 0
    for a in accesses:
        rows = a.rows(mem)
        if rows.size == 0:
            continue
        banks = hash_of(a.layout).banks_of(rows)
        per_bank += np.bincount(banks, minlength=mem.banks)
        port_bound = max(port_bound, math.ceil(rows.size / a.ports))
        total_rows += int(rows.size)
    mult = int(per_bank.max()) if total_rows else 0
    fab = fabric.load(per_bank, mem.m) if fabric is not None and total_rows else 1
    return CycleReport(cycles=max(port_bound, mult, fab), port_bound=port_bound,
                       conflict_bound=mult, rows=total_rows, fabric_bound=fab)


@dataclass(frozen=True, slots=True)
class TrafficMetrics:
    """What a workload measured through one solution: the demo's Cost C and Cost D.

    Latency is per ACCESS, charged honestly: under a shared schedule every access in a
    step completes when the step's slowest bank does, so each is charged the step's
    cycles; under time-slots access i is also charged the slots served before it. The
    first cut divided step cycles across the step's requests, which understated Cost C
    by the step width and compressed every policy toward 1.0 (D380)."""

    cycles: int
    rows: int
    requests: int
    conflict_limited_requests: int
    latency_sum: float = 0.0      # sum over accesses of their completion cycles
    extra_latency: float = 0.0    # pipeline/buffer latency a solution adds per access

    @property
    def avg_latency(self) -> float:      # Cost C (cycles until an access completes)
        if not self.requests:
            return 0.0
        return self.latency_sum / self.requests + self.extra_latency

    @property
    def throughput(self) -> float:       # Cost D (bank-rows retired per cycle)
        return self.rows / self.cycles if self.cycles else 0.0


def run_traffic(groups: list[list[TileAccess]], mem: Memory, hash_of,
                schedule: str = "shared", extra_latency: float = 0.0,
                fabric=None) -> TrafficMetrics:
    """Each entry in `groups` is the set of accesses that WANT the same cycle (a system
    step). schedule="shared": all hit the banks together (the conflict-exposed
    default). schedule="timeslot": units take turns -- each access group is served
    alone, conflicts across units vanish by construction, and the cost shows up as
    `extra_latency` (slot wait + fetch buffers), which the caller supplies."""
    cycles = rows = limited = requests = 0
    latency_sum = 0.0
    for group in groups:
        requests += len(group)
        if schedule == "ab_stagger":
            # D382's sharpened hypothesis, made testable: the intra-unit cost is
            # read-read interference between the MU's two big operands, so phase 1
            # issues A (and everything else), phase 2 issues B and the bias C. B
            # operands arrive one phase late -- the MU must pipeline its B input,
            # which is the assumption the solution carries.
            ph2 = [a for a in group
                   if a.unit == "mu" and not a.write and a.ports < 16]
            ph1 = [a for a in group if a not in ph2]
            c1 = shared_cycle(ph1, mem, hash_of, fabric)
            c2 = shared_cycle(ph2, mem, hash_of, fabric)
            cycles += c1.cycles + c2.cycles
            rows += c1.rows + c2.rows
            limited += int(c1.conflict_limited) + int(c2.conflict_limited)
            latency_sum += c1.cycles * len(ph1)
            latency_sum += (c1.cycles + c2.cycles) * len(ph2)
        elif schedule == "stagger":
            # The study's own recommendation (D380's scope breakdown), made testable:
            # reads issue first, writes the phase after. Bank pressure per phase drops
            # (28R then 24W instead of 52 at once); writes pay one phase of latency
            # and a small hold buffer the policy prices. Whether this actually beats
            # the shared schedule is the MEASURED verdict, not the assumption.
            reads = [a for a in group if not a.write]
            writes = [a for a in group if a.write]
            c1 = shared_cycle(reads, mem, hash_of, fabric)
            c2 = shared_cycle(writes, mem, hash_of, fabric)
            cycles += c1.cycles + c2.cycles
            rows += c1.rows + c2.rows
            limited += int(c1.conflict_limited) + int(c2.conflict_limited)
            latency_sum += c1.cycles * len(reads)
            latency_sum += (c1.cycles + c2.cycles) * len(writes)
        elif schedule == "timeslot":
            served = 0
            for a in group:
                rep = shared_cycle([a], mem, hash_of, fabric)
                cycles += rep.cycles
                rows += rep.rows
                limited += int(rep.conflict_limited)
                served += rep.cycles
                latency_sum += served      # waits for every earlier slot, then its own
        else:
            rep = shared_cycle(group, mem, hash_of, fabric)
            cycles += rep.cycles
            rows += rep.rows
            limited += int(rep.conflict_limited)
            latency_sum += rep.cycles * len(group)   # everyone waits for the slowest bank
    return TrafficMetrics(cycles=cycles, rows=rows, requests=requests,
                          conflict_limited_requests=limited, latency_sum=latency_sum,
                          extra_latency=extra_latency)
