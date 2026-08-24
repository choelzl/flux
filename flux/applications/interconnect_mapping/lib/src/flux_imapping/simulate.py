"""Independent throughput model: discrete-event, greedy-arbitrated, queue-based (D394).

The analytic cycle law (conflict.py) is an optimistic bound that assumes optimal grant
scheduling. This simulator shares none of its arithmetic: it walks cycles with a greedy,
arrival-ordered arbiter, per-bank single-port grants, per-level fabric capacity and per-access
port limits. On traffic a perfect scheduler cannot improve the two agree exactly; elsewhere
sim >= analytic, and the gap is the cost of real arbitration.

Steps are barriers as in the analytic model, so the difference isolates scheduling. Cheaper
than RTL; Verilator and OpenROAD stages sit above it.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .fabric import FabricModel
from .model import Memory
from .workloads import Workload


@dataclass(frozen=True, slots=True)
class SimMetrics:
    cycles: int
    rows: int
    requests: int
    latency_sum: float

    @property
    def avg_latency(self) -> float:
        return self.latency_sum / self.requests if self.requests else 0.0

    @property
    def throughput(self) -> float:
        return self.rows / self.cycles if self.cycles else 0.0


def _drain_step(access_banks: list[np.ndarray], ports: list[int],
                fabric: FabricModel | None, mem: Memory,
                pipe: float) -> tuple[int, float]:
    """Serve one step's accesses to completion; return (cycles, latency_sum).

    Greedy per cycle, in arrival order: a job is granted iff its bank's port is free
    this cycle, its access has port budget left this cycle, and every fabric level
    still has capacity on the job's subtree. No lookahead, no reordering -- the
    pessimism a real single-pass arbiter actually has.

    Latency uses the same barrier semantics as the analytic law (D380): every access in
    a step completes at the phase's drain, otherwise the check would be more optimistic
    than the bound it checks."""
    pending: list[list[int]] = [list(b) for b in access_banks]  # per access, banks left
    cycles = 0
    while any(pending):
        cycles += 1
        bank_busy = np.zeros(mem.banks, dtype=bool)
        level_used = {bits: np.zeros(1 << bits, dtype=np.int64)
                      for bits, _ in (fabric.levels if fabric is not None else ())}
        caps = dict(fabric.levels) if fabric is not None else {}
        for i, banks_left in enumerate(pending):
            if not banks_left:
                continue
            budget = ports[i]
            kept: list[int] = []
            for bank in banks_left:
                if budget <= 0 or bank_busy[bank]:
                    kept.append(bank)
                    continue
                blocked = False
                for bits in level_used:
                    if level_used[bits][bank >> (mem.m - bits)] >= caps[bits]:
                        blocked = True
                        break
                if blocked:
                    kept.append(bank)
                    continue
                bank_busy[bank] = True
                for bits in level_used:
                    level_used[bits][bank >> (mem.m - bits)] += 1
                budget -= 1
            pending[i] = kept
    latency_sum = len(access_banks) * (cycles + pipe)
    return cycles, latency_sum


def simulate_traffic(workload_steps, mem: Memory, hash_of, *,
                     fabric: FabricModel | None = None,
                     schedule: str = "shared", pipe_latency: float = 0.0) -> SimMetrics:
    """The simulator's answer to `run_traffic`'s question, for the schedules the picks
    use: "shared" (a step's accesses contend together) and "ab_stagger"/"stagger"
    (phases drain back to back, later phases charged the earlier phases' cycles)."""
    cycles = rows = requests = 0
    latency_sum = 0.0
    for step in workload_steps:
        requests += len(step)
        if schedule in ("stagger", "ab_stagger"):
            if schedule == "stagger":
                phases = ([a for a in step if not a.write],
                          [a for a in step if a.write])
            else:
                ph2 = [a for a in step
                       if a.unit == "mu" and not a.write and a.ports < 16]
                phases = ([a for a in step if a not in ph2], ph2)
        else:
            phases = (list(step),)
        offset = 0
        for phase_accesses in phases:
            if not phase_accesses:
                continue
            banks = [a.rows(mem) for a in phase_accesses]
            banks = [hash_of(a.layout).banks_of(r) if r.size else np.empty(0, int)
                     for a, r in zip(phase_accesses, banks)]
            rows += sum(int(b.size) for b in banks)
            c, lat = _drain_step([b.tolist() for b in banks],
                                 [a.ports for a in phase_accesses], fabric, mem,
                                 pipe_latency + offset)
            cycles += c
            latency_sum += lat
            offset += c
    return SimMetrics(cycles=cycles, rows=rows, requests=requests,
                      latency_sum=latency_sum)


def cross_check(scored, train_unused, holdout: list[Workload],
                mem: Memory) -> dict[str, float]:
    """One pick through both models on the same holdout traffic: analytic and simulated
    latency/throughput and the latency gap in percent."""
    sol, fabric = scored.solution, scored.fabric
    sim_cycles = sim_rows = sim_requests = 0
    sim_lat = 0.0
    for w in holdout:
        from .flow import _apply_transform

        tw = _apply_transform(w, sol.transform)
        m = simulate_traffic(tw.steps, mem, sol.hash_of, fabric=fabric,
                             schedule=sol.schedule,
                             pipe_latency=sol.extra_latency + fabric.pipe_latency)
        sim_cycles += m.cycles
        sim_rows += m.rows
        sim_requests += m.requests
        sim_lat += m.latency_sum
    sim = SimMetrics(cycles=sim_cycles, rows=sim_rows, requests=sim_requests,
                     latency_sum=sim_lat)
    return {
        "analytic_latency": scored.holdout.avg_latency,
        "sim_latency": sim.avg_latency,
        "analytic_throughput": scored.holdout.throughput,
        "sim_throughput": sim.throughput,
        "latency_gap_pct": (sim.avg_latency / scored.holdout.avg_latency - 1) * 100
        if scored.holdout.avg_latency else 0.0,
    }
