"""The interconnect_mapping loop: evaluate the field, search the hash space, certify
what is provable, and hand back a four-cost Pareto front with the receipts attached.

Shape (docs/decisions.md D378, same skeleton as the other five applications):
1. Curated field (`solutions.catalog`) measured on TRAIN workloads.
2. Optional searches extend the field: a hill-climb over injective XOR tap sets, and
   optional LLM proposal rounds (a model may suggest tap sets; every proposal passes
   the injectivity gate and the same evaluator, or is refused with the reason).
3. Everything re-measured on HOLDOUT workloads -- the anti-overfitting split; the
   report carries both numbers so a gap is visible.
4. Four costs per solution: A = area score (structural gate-units; --phys upgrades the
   frontier to placed um2 through the interconnect app's whole-fabric flow),
   B = padding fraction (stored/true - 1, exact), C = average access latency,
   D = throughput (rows/cycle). Pareto dominance is computed over all four.
5. Certificates: per (mode, tile, pitch) family, intra-operand conflict-freedom is
   PROVED by exhaustion over every origin in the bounded runtime domain (dims <= 64 --
   finite, so exhaustion is a proof, not a sample), and impossibility floors come from
   pigeonhole: a request of k rows needs >= ceil(k / min(P, B)) cycles, hash-free.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np

from flux_bankmap.mapping import XorFold

from .conflict import BankHash, TrafficMetrics, intra_operand, run_traffic
from .fabric import FabricModel, xbar_full
from .model import BLOCK_OF, Memory, Mode, TensorLayout, TileAccess, VECTOR_MODES
from .solutions import Solution, catalog, injective, solution_to_dict
from .workloads import Workload, train_holdout


# ---------------------------------------------------------------- evaluation

@dataclass(frozen=True, slots=True)
class Scored:
    """One DESIGN POINT: a map policy PAIRED with an interconnect topology. The pair
    is the unit of reporting (Cedric's requirement): a hash is only as good as the
    fabric that carries it, and a cheap fabric only as good as the hash that keeps
    traffic off its shared links."""

    solution: Solution
    fabric: FabricModel
    train: TrafficMetrics
    holdout: TrafficMetrics
    pad_fraction: float          # Cost B, exact
    area_score: float            # Cost A (structural; um2 under --phys)
    area_um2: float | None = None
    fmax_mhz: float | None = None

    @property
    def pair_name(self) -> str:
        return f"{self.solution.name} + {self.fabric.name}"

    @property
    def costs(self) -> tuple[float, float, float, float]:
        """(A, B, C, -D) -- all minimized, judged on HOLDOUT, never train."""
        return (self.area_score, self.pad_fraction,
                self.holdout.avg_latency, -self.holdout.throughput)

    def to_dict(self) -> dict[str, Any]:
        return {
            **solution_to_dict(self.solution),
            "pair": self.pair_name,
            "fabric": {"name": self.fabric.name, "levels": list(self.fabric.levels),
                       "note": self.fabric.note},
            "train": {"avg_latency": self.train.avg_latency,
                      "throughput": self.train.throughput,
                      "conflict_limited": self.train.conflict_limited_requests},
            "holdout": {"avg_latency": self.holdout.avg_latency,
                        "throughput": self.holdout.throughput,
                        "conflict_limited": self.holdout.conflict_limited_requests},
            "pad_fraction": self.pad_fraction,
            "area_um2": self.area_um2, "fmax_mhz": self.fmax_mhz,
        }


def _apply_transform(w: Workload, transform) -> Workload:
    """Re-place every tensor through the solution's transform (padding changes sizes,
    so bases shift too -- a fresh bump pass keeps tensors disjoint), then rebuild each
    access against its re-placed layout."""
    if transform is None:
        return w
    e = 4
    new_layouts: dict[int, TensorLayout] = {}
    cursor = 0
    for i, t in enumerate(w.tensors):
        nt = transform(t, i)
        nt = TensorLayout(r=nt.r, c=nt.c, l=nt.l, mode=nt.mode, base=cursor,
                          pad_inner_to=nt.pad_inner_to)
        cursor += ((nt.stored_elems + e - 1) // e) * e
        new_layouts[id(t)] = nt
    steps = [[TileAccess(layout=new_layouts[id(a.layout)], r0=a.r0, c0=a.c0, l0=a.l0,
                         rt=a.rt, ct=a.ct, lt=a.lt, ports=a.ports, write=a.write)
              for a in step] for step in w.steps]
    return Workload(steps=steps, tensors=list(new_layouts.values()), seed=w.seed)


def _measure(sol: Solution, fabric: FabricModel, workloads: list[Workload],
             mem: Memory) -> tuple[TrafficMetrics, float]:
    cycles = rows = requests = limited = 0
    latency_sum = 0.0
    true_e = stored_e = 0
    for w in workloads:
        tw = _apply_transform(w, sol.transform)
        m = run_traffic(tw.steps, mem, sol.hash_of, schedule=sol.schedule,
                        extra_latency=sol.extra_latency + fabric.pipe_latency,
                        fabric=fabric)
        cycles += m.cycles
        rows += m.rows
        requests += m.requests
        limited += m.conflict_limited_requests
        latency_sum += m.latency_sum
        true_e += sum(t.true_elems for t in tw.tensors)
        stored_e += sum(t.stored_elems for t in tw.tensors)
    agg = TrafficMetrics(cycles=cycles, rows=rows, requests=requests,
                         conflict_limited_requests=limited, latency_sum=latency_sum,
                         extra_latency=sol.extra_latency + fabric.pipe_latency)
    pad = (stored_e - true_e) / true_e if true_e else 0.0
    return agg, pad


def score(sol: Solution, fabric: FabricModel, train: list[Workload],
          holdout: list[Workload], mem: Memory) -> Scored:
    t, pad = _measure(sol, fabric, train, mem)
    h, pad_h = _measure(sol, fabric, holdout, mem)
    return Scored(solution=sol, fabric=fabric, train=t, holdout=h,
                  pad_fraction=max(pad, pad_h),
                  area_score=fabric.area_score + 4 * sol.buffer_bits)


def pareto_front(scored: list[Scored]) -> list[Scored]:
    """4-objective dominance (A, B, C, -D), all minimized on holdout numbers."""
    front = []
    for s in scored:
        dominated = any(
            all(oc <= sc for oc, sc in zip(o.costs, s.costs)) and o.costs != s.costs
            for o in scored)
        if not dominated:
            front.append(s)
    return front


# ---------------------------------------------------------------- hash search

def _mutate_taps(rng: np.random.Generator, taps: tuple[tuple[int, ...], ...],
                 addr_bits: int, m: int) -> tuple[tuple[int, ...], ...]:
    i = int(rng.integers(0, m))
    t = list(taps[i])
    b = int(rng.integers(0, addr_bits))
    if b in t and len(t) > 1:
        t.remove(b)
    elif b not in t and len(t) < 4:
        t.append(b)
    out = list(taps)
    out[i] = tuple(sorted(t))
    return tuple(out)


def climb_xor(train: list[Workload], holdout: list[Workload], mem: Memory,
              base: Solution, *, rounds: int = 40, seed: int = 0,
              addr_bits: int = 16, fabric: FabricModel | None = None,
              name: str = "S6-xor-searched") -> Scored | None:
    """Hill-climb over injective XOR tap sets, judged on TRAIN average latency (the
    holdout stays untouched until the final scoring -- that is the whole point of the
    split). `fabric` is the topology the climb tunes AGAINST -- the ideal crossbar by
    default, but the mapping little-loop passes a real (blocking) fabric, because a
    hash tuned on the ideal crossbar has never felt a subtree capacity and a slim
    butterfly deserves a hash shaped for its bisection. Returns None if nothing beat
    the base."""
    fabric = fabric or xbar_full(mem.banks)
    rng = np.random.default_rng(seed)
    cur_taps = tuple((i, i + mem.m) for i in range(mem.m))
    cur = score(_with_taps(base, cur_taps, mem, name), fabric, train, holdout, mem)
    improved = False
    for _ in range(rounds):
        cand_taps = _mutate_taps(rng, cur_taps, addr_bits, mem.m)
        mapping = XorFold(taps=cand_taps, name="xor-searched")
        if not injective(mapping, mem.m):
            continue
        cand = score(_with_taps(base, cand_taps, mem, name), fabric, train, holdout, mem)
        if cand.train.avg_latency < cur.train.avg_latency:
            cur, cur_taps, improved = cand, cand_taps, True
            from flux_profile import mark

            mark(f"climb: improved to {cand.train.avg_latency:.2f} cy",
                 why=f"{name} on {fabric.name}")
    return cur if improved else None


def _with_taps(base: Solution, taps, mem: Memory,
               name: str = "S6-xor-searched") -> Solution:
    mapping = XorFold(taps=taps, name="xor-searched")
    h = BankHash(mapping=mapping, bank_bits=mem.m)
    return Solution(
        name=name, hash_of=lambda layout: h,
        schedule=base.schedule, extra_latency=base.extra_latency,
        transform=base.transform, targets=("intra-operand",),
        metadata=(), assumptions=("taps tuned on train workloads; judge on holdout",))


def category_breakdown(sol: Solution, fabric: FabricModel,
                       workloads: list[Workload], mem: Memory) -> dict[str, float]:
    """Average access latency measured at each conflict SCOPE, answering the problem
    statement's 'state which category each proposal minimizes' with numbers instead of
    claims: 'operand' runs every access alone (intra-operand conflicts only), 'unit'
    groups accesses by issuing unit (adds intra-unit), 'system' is the full step (adds
    cross-unit and DMA). The gap between two rows is the cost of exactly that
    category. Uses the solution's own transform, hash, and this fabric throughout."""
    from flux_profile import phase as _tphase

    from .conflict import run_traffic as _rt

    with _tphase("report: latency by conflict scope",
                 why=f"{sol.name} + {fabric.name}"):
        return _breakdown_scopes(sol, fabric, workloads, mem, _rt)


def _breakdown_scopes(sol, fabric, workloads, mem, _rt) -> dict[str, float]:
    out: dict[str, float] = {}
    for scope in ("operand", "unit", "system"):
        cycles = requests = 0
        latency = 0.0
        for w in workloads:
            tw = _apply_transform(w, sol.transform)
            if scope == "system":
                groups = tw.steps
            elif scope == "unit":
                groups = []
                for step in tw.steps:
                    per_unit: dict[str, list] = {}
                    for a in step:
                        per_unit.setdefault(a.unit or "mu", []).append(a)
                    groups.extend(per_unit.values())
            else:
                groups = [[a] for step in tw.steps for a in step]
            m = _rt(groups, mem, sol.hash_of, schedule=sol.schedule,
                    extra_latency=sol.extra_latency + fabric.pipe_latency,
                    fabric=fabric)
            cycles += m.cycles
            requests += m.requests
            latency += m.latency_sum
        out[scope] = (latency / requests + sol.extra_latency + fabric.pipe_latency
                      if requests else 0.0)
    return out


# ---------------------------------------------------------------- certificates

@dataclass(frozen=True, slots=True)
class Certificate:
    """One PROVED statement: for `solution`, every origin of a `tile` sweep over every
    legal tensor with dims <= bound in `mode` at pitch `pitch` is intra-operand
    conflict-free (conflict_bound <= port_bound). Proof is exhaustion over the finite
    domain, which the problem statement's dims-in-1..64 makes legitimate."""

    solution: str
    mode: str
    tile: tuple[int, int, int]
    pitch: int
    holds: bool
    checked_origins: int
    counterexample: dict[str, Any] | None = None


def certify(sol: Solution, mem: Memory, *, mode: Mode, rt: int, ct: int, lt: int,
            dim: int = 64, ports: int = 16, fabric: FabricModel | None = None,
            label: str | None = None) -> Certificate:
    e = mem.elems_per_row
    if mode in VECTOR_MODES:
        r, c, l = (dim, 1, dim) if mode is Mode.Loop_Row else (dim, 1, dim)
    elif mode in BLOCK_OF:
        b = BLOCK_OF[mode]
        r = c = (dim // b) * b
        l = 4
    else:
        r = c = (dim // e) * e
        l = 4
    layout = TensorLayout(r=r, c=c, l=l, mode=mode, base=0)
    if sol.transform is not None:
        layout = sol.transform(layout, 0)
    checked = 0
    for l0 in range(0, layout.l, max(1, lt)):
        for r0 in range(0, layout.r, max(1, rt)):
            for c0 in range(0, max(1, layout.c), max(1, ct)):
                a = TileAccess(layout=layout, r0=r0, c0=c0, l0=l0,
                               rt=rt, ct=ct, lt=lt, ports=ports)
                rep = intra_operand(a, mem, sol.hash_of, fabric)
                checked += 1
                if rep.conflict_limited:
                    return Certificate(
                        solution=label or sol.name, mode=mode.name, tile=(rt, ct, lt),
                        pitch=layout.inner_pitch, holds=False,
                        checked_origins=checked,
                        counterexample={"origin": [r0, c0, l0],
                                        "conflict_bound": rep.conflict_bound,
                                        "fabric_bound": rep.fabric_bound,
                                        "port_bound": rep.port_bound})
    return Certificate(solution=label or sol.name, mode=mode.name, tile=(rt, ct, lt),
                       pitch=layout.inner_pitch, holds=True, checked_origins=checked)


def pigeonhole_floor(rows: int, ports: int, mem: Memory) -> int:
    """No hash, fabric, or schedule beats this: k distinct rows through min(P, B)
    single-ported lanes take ceil(k / min(P, B)) cycles. Printed beside measured
    latencies so 'conflict-free' is never confused with 'free'."""
    import math
    return math.ceil(rows / min(ports, mem.banks))


def interconnect_loop(train: list[Workload], holdout: list[Workload], mem: Memory,
                      ref_policy: Solution, *, keep: int = 8,
                      track=None) -> list[FabricModel]:
    """LITTLE LOOP A (the interconnect-app half, D392): FIND interconnects before
    marrying them to mappings. Generates ~30 fabric candidates over parameter grids
    (radix, slimming, group counts, uplinks, active ports, ring slots), screens each
    under one reference mapping on the storage-mode traffic, and keeps the Pareto set
    over (area, latency, throughput) plus the best-latency and best-area corners.
    Every screen is its own task in the TUI."""
    from flux_profile import mark, phase as _tphase

    from .fabric import generate_fabrics

    mark("interconnect little-loop: find fabrics")
    candidates = generate_fabrics(mem.m)
    screened: list[Scored] = []
    for fabric in candidates:
        with _tphase("interconnect: screen fabric", why=fabric.name,
                     levels=len(fabric.levels), areaU=fabric.gate_units):
            s_ = score(ref_policy, fabric, train, holdout, mem)
        screened.append(s_)
        if track:
            track(s_)
    front = pareto_front(screened)
    ranked = sorted(front, key=lambda x: x.holdout.avg_latency)
    chosen = [s_.fabric for s_ in ranked[:keep]]
    print(f"interconnect loop: {len(candidates)} candidates screened -> "
          f"{len(front)} on the screen front -> keeping {len(chosen)}: "
          + ", ".join(f.name for f in chosen))
    return chosen


def strides_from_workloads(workloads: list[Workload], mem: Memory,
                           limit: int = 8) -> list[int]:
    """The dominant access strides the storage modes actually produce, in bank-rows:
    the row-to-row pitch of each tensor (a tile walking rows steps by exactly this),
    plus 1 for the sequential inner walk. This is the reduction that lets bankmap's
    solver speak to this problem: its stride sets are our tensors' pitches."""
    strides: dict[int, int] = {}
    for w in workloads:
        for t in w.tensors:
            pitch_rows = max(1, t.inner_pitch >> mem.e)
            strides[pitch_rows] = strides.get(pitch_rows, 0) + 1
    strides[1] = strides.get(1, 0) + 1
    ranked = sorted(strides, key=lambda k: -strides[k])
    return sorted(ranked[:limit])


def z3_mapping_policy(train: list[Workload], mem: Memory, *,
                      z3_seconds: int = 15) -> Solution | None:
    """LITTLE LOOP B's exact rung (the bankmap half, D392): hand the traffic's
    dominant strides to bankmap's z3 solver and get back a PROVEN conflict-free
    XOR-fold -- or None with the honest reason. bankmap's `XorFold` is the same class
    this study's hashes already are, so the solved mapping drops straight into a
    policy named S10-z3-proven."""
    from flux_bankmap.problem import MappingRequest
    from flux_bankmap.solve_z3 import solve

    strides = strides_from_workloads(train, mem)
    try:
        request = MappingRequest(strides=strides, concurrent=16,
                                 banks=mem.banks, address_bits=16,
                                 z3_seconds=z3_seconds)
        mapping, trace = solve(request)
    except Exception as exc:  # noqa: BLE001 -- an unexpressible request is a report
        print(f"z3 mapping route: refused ({type(exc).__name__}: {exc})")
        return None
    if mapping is None:
        print(f"z3 mapping route: no conflict-free fold for strides {strides} "
              f"(the solver's verdict, not a timeout excuse)")
        return None
    if not injective(mapping, mem.m):
        print("z3 mapping route: solved fold not injective on low bits; refused")
        return None
    print(f"z3 mapping route: PROVEN fold for strides {strides}: {mapping.describe()}")
    h = BankHash(mapping=mapping, bank_bits=mem.m)
    return Solution(
        name="S10-z3-proven", hash_of=lambda layout, _h=h: _h,
        targets=("intra-operand",), metadata=(),
        assumptions=(f"conflict-free PROVEN by z3 for strides {strides} "
                     "(16 concurrent); other patterns measured, not promised",))


def mapping_loop(fabric: FabricModel, base: Solution, train: list[Workload],
                 holdout: list[Workload], mem: Memory, *, rounds: int = 30,
                 seed: int = 0) -> Scored | None:
    """LITTLE LOOP 1 (the bankmap-style half): for one FIXED interconnect, search the
    mapping-function space -- an XOR-tap climb whose cycle law includes this fabric's
    own capacity tree. This is where "mapping changes the experience": a hash tuned
    here spreads traffic the way THIS topology needs, not the way the ideal crossbar
    tolerates. Returns a Scored pair named for the fabric it was tuned against."""
    return climb_xor(train, holdout, mem, base, rounds=rounds, seed=seed,
                     fabric=fabric, name=f"S6-xor@{fabric.name}")


def fit_fabric(policy: Solution, fabric: FabricModel, train: list[Workload],
               mem: Memory) -> FabricModel | None:
    """LITTLE LOOP 2 (the interconnect-app-style half): rightsize one capacity-tree
    fabric to the RESIDUAL traffic a mapping policy actually leaves it. Measure, per
    level, the peak subtree load over every train step under this policy's own hash,
    transform and schedule; set each level's capacity to that measured peak (never
    above the original). By construction the fitted fabric adds zero blocking on the
    train traffic; holdout re-scoring judges whether that held out of sample. Area
    shrinks proportionally to the links removed (the same structural rule that priced
    the family). Non-blocking fabrics have nothing to fit."""
    if not fabric.levels:
        return None
    peaks = {bits: 1 for bits, _ in fabric.levels}
    for w in train:
        tw = _apply_transform(w, policy.transform)
        for step in tw.steps:
            per_bank = np.zeros(mem.banks, dtype=np.int64)
            for a in step:
                rows = a.rows(mem)
                if rows.size:
                    per_bank += np.bincount(policy.hash_of(a.layout).banks_of(rows),
                                            minlength=mem.banks)
            for bits, _ in fabric.levels:
                groups = per_bank.reshape(1 << bits, -1).sum(axis=1)
                peaks[bits] = max(peaks[bits], int(groups.max()))
    new_levels = tuple((bits, min(cap, peaks[bits])) for bits, cap in fabric.levels)
    if new_levels == fabric.levels:
        return None
    old_links = sum(cap * (1 << bits) for bits, cap in fabric.levels)
    new_links = sum(cap * (1 << bits) for bits, cap in new_levels)
    return FabricModel(
        name=f"{fabric.name}-fit", levels=new_levels,
        gate_units=max(1, int(fabric.gate_units * new_links / old_links)),
        buffer_bits=fabric.buffer_bits, pipe_latency=fabric.pipe_latency,
        note=f"{fabric.note}; capacities fitted to residual traffic of {policy.name}")


def coordinate(scored: list[Scored], train: list[Workload], holdout: list[Workload],
               mem: Memory, *, top_k: int = 3, climb_rounds: int = 30,
               seed: int = 0, track=None) -> list[Scored]:
    """THE BIG LOOP's one step: block-coordinate descent between the two little loops.
    From the current front, take the top fabrics and tune a mapping for each
    (mapping_loop), and take the top pairs and fit their fabric to their policy's
    residual traffic (fit_fabric); score every new pair the same way as everything
    else. The caller iterates until the front stops moving."""
    front = pareto_front(scored)
    out: list[Scored] = []
    seen_fabrics: list[FabricModel] = []
    for s_ in sorted(front, key=lambda x: x.holdout.avg_latency):
        if s_.fabric.name not in {f.name for f in seen_fabrics}:
            seen_fabrics.append(s_.fabric)
        if len(seen_fabrics) >= top_k:
            break
    base = next((s_.solution for s_ in scored
                 if s_.solution.name == "S1-xor-global"), front[0].solution)
    from flux_profile import phase as _tphase

    for fabric in seen_fabrics:
        with _tphase("mapping little-loop: tune hash for fabric", why=fabric.name,
                     rounds=climb_rounds):
            tuned = mapping_loop(fabric, base, train, holdout, mem,
                                 rounds=climb_rounds, seed=seed)
        if tuned is not None:
            out.append(tuned)
            if track:
                track(tuned)
    for s_ in sorted(front, key=lambda x: x.holdout.avg_latency)[:top_k]:
        with _tphase("interconnect little-loop: fit fabric to mapping",
                     why=s_.pair_name):
            fitted = fit_fabric(s_.solution, s_.fabric, train, mem)
        if fitted is not None:
            ns = score(s_.solution, fitted, train, holdout, mem)
            out.append(ns)
            if track:
                track(ns)
    return out


def conclude(study: "ConflictStudy") -> dict[str, Any]:
    """The decision-first summary, DERIVED from the measured field -- never hard-coded,
    so it stays honest when a different seed or regime knob flips a verdict. What it
    names: the front's three corners, the consensus fabric (the topology appearing on
    the most frontier rows), every policy and fabric that made no frontier row at all
    together with its best measured showing (the reason it lost), and the software
    contract of the recommended pair (its PROVED tile families)."""
    front = study.front
    if not front:
        return {"note": "empty front"}
    # The shared decision arithmetic (D397 phase 3): corners with deterministic
    # tie-breaks (equal throughput goes to the lower latency, equal latency to the
    # smaller area -- a tie must not pick arbitrarily) and the balanced pick as the
    # KNEE over all four costs. Both rules were learned here and now live in
    # flux_decide for every loop.
    from flux_decide import corner, knee_ranked

    lat = corner(front, lambda s: s.holdout.avg_latency, lambda s: s.area_score)
    thr = corner(front, lambda s: -s.holdout.throughput,
                 lambda s: s.holdout.avg_latency)
    cheap = corner(front, lambda s: s.area_score, lambda s: s.holdout.avg_latency)
    ranked = knee_ranked(front, [lambda s: s.holdout.avg_latency,
                                 lambda s: -s.holdout.throughput,
                                 lambda s: s.area_score,
                                 lambda s: s.pad_fraction])
    fabric_rows: dict[str, int] = {}
    for s_ in ranked[:max(5, len(ranked) // 3)]:
        fabric_rows[s_.fabric.name] = fabric_rows.get(s_.fabric.name, 0) + 1
    consensus = max(fabric_rows, key=fabric_rows.get)

    front_policies = {s_.solution.name for s_ in front}
    front_fabrics = set(fabric_rows)
    losers: dict[str, str] = {}
    for s_ in study.scored:
        name = s_.solution.name
        if name not in front_policies:
            best = min((x for x in study.scored if x.solution.name == name),
                       key=lambda x: x.holdout.avg_latency)
            losers.setdefault(
                name, f"best showing {best.holdout.avg_latency:.2f} cy / "
                      f"{best.holdout.throughput:.2f} rows/cy ({best.fabric.name})")
    for s_ in study.scored:
        fname = s_.fabric.name
        if fname not in front_fabrics:
            best = min((x for x in study.scored if x.fabric.name == fname),
                       key=lambda x: x.holdout.avg_latency)
            losers.setdefault(
                fname, f"best showing {best.holdout.avg_latency:.2f} cy at "
                       f"{best.area_score:.0f} areaU ({best.solution.name})")

    balanced = ranked[0]
    proved = [f"{c.mode} tile {c.tile}" for c in study.certificates
              if c.solution == balanced.pair_name and c.holds]
    refuted = sum(1 for c in study.certificates
                  if c.solution == balanced.pair_name and not c.holds)
    return {
        "latency_corner": {"pair": lat.pair_name,
                           "latency": lat.holdout.avg_latency,
                           "throughput": lat.holdout.throughput},
        "throughput_corner": {"pair": thr.pair_name,
                              "latency": thr.holdout.avg_latency,
                              "throughput": thr.holdout.throughput},
        "area_corner": {"pair": cheap.pair_name, "area_score": cheap.area_score,
                        "latency": cheap.holdout.avg_latency},
        "consensus_fabric": consensus,
        "consensus_frontier_rows": fabric_rows[consensus],
        "knee_rank": [s_.pair_name for s_ in ranked[:5]],
        "balanced_pick": {"pair": balanced.pair_name,
                          "latency": balanced.holdout.avg_latency,
                          "throughput": balanced.holdout.throughput,
                          "pad_fraction": balanced.pad_fraction,
                          "metadata": list(balanced.solution.metadata),
                          "proved_families": proved,
                          "refuted_families": refuted},
        "never_on_front": losers,
    }


# ---------------------------------------------------------------- the loop

@dataclass(frozen=True, slots=True)
class ConflictStudy:
    scored: list[Scored]
    front: list[Scored]
    certificates: list[Certificate]
    refused: list[str]
    progress: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)   # operator guidance typed mid-run (D388)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scored": [s.to_dict() for s in self.scored],
            "front": [s.pair_name for s in self.front],
            "certificates": [
                {"solution": c.solution, "mode": c.mode, "tile": list(c.tile),
                 "pitch": c.pitch, "holds": c.holds,
                 "checked_origins": c.checked_origins,
                 "counterexample": c.counterexample}
                for c in self.certificates],
            "refused": self.refused,
            "notes": self.notes,
        }


def run_study(*, seed: int = 0, mem: Memory | None = None, ops: int = 8,
              climb_rounds: int = 40, llm_rounds: int = 0,
              proposer: Any | None = None,
              certify_tiles: tuple[tuple[int, int, int], ...] = ((8, 4, 1), (4, 16, 1)),
              track: Callable[[Scored], None] | None = None,
              vu_probability: float = 0.7,
              dma_probability: float = 0.6,
              coordination_rounds: int = 2,
              db_path: str | None = None,
              feedback: Any | None = None) -> ConflictStudy:
    mem = mem or Memory()
    # The flywheel (D397): every scored pair, every refusal and this run's conclusion
    # land in a campaign record; a resumed run's proposer starts from what the record
    # already knows instead of from priors. No db, no record -- the study still runs.
    records = None
    if db_path:
        from flux_records import Records

        records = Records(db_path, objective={
            "study": "interconnect_mapping", "seed": seed, "ops": ops,
            "vu": vu_probability, "dma": dma_probability}, log=print)
    train, holdout = train_holdout(seed, ops=ops, vu_probability=vu_probability,
                                   dma_probability=dma_probability)
    refused: list[str] = []
    # Operator guidance (D388, wired here in D397 phase 2): notes typed into the TUI
    # drain at the proposer's round boundaries, persist as campaign events, and land
    # on the study so the report can echo them. Advisory only -- every proposal still
    # passes the injectivity gate and the same holdout scoring. A resumed campaign's
    # earlier notes rejoin the prompt under their honest stamp (D403).
    from flux_feedback import reload_notes

    human_notes: list[Any] = reload_notes(records, say=print)

    def _on_note(n: Any) -> None:
        if records is not None:
            records.note(n.text)
        print(f'  [human] guidance noted: "{n.text}"', flush=True)

    from flux_profile import mark

    field_ = catalog(mem)

    # STAGE A (D392): find interconnects first -- the interconnect little-loop
    # generates ~30 parameterized candidates and keeps the screen-front under a
    # reference mapping. The old fixed dozen remains available as generate_fabrics'
    # anchors; what the study evaluates is what the loop FOUND.
    ref = next(s_ for s_ in field_ if s_.name == "S1-xor-global")
    fabrics = interconnect_loop(train, holdout, mem, ref, track=track)

    # STAGE B: mapping functions -- the climb, bankmap's z3 route (a PROVEN fold for
    # the traffic's own strides), and optional model proposals, all judged later on
    # the same holdout cross-product as everything else.
    mark("mapping little-loop: build the policy field")
    if climb_rounds > 0:
        from flux_profile import phase as _tphase0

        with _tphase0("search: xor hill-climb", why="ideal fabric",
                      rounds=climb_rounds):
            searched = climb_xor(train, holdout, mem, ref, rounds=climb_rounds,
                                 seed=seed)
        if searched is not None:
            field_.append(searched.solution)

    z3_policy = z3_mapping_policy(train, mem)
    if z3_policy is not None:
        field_.append(z3_policy)

    if llm_rounds > 0 and proposer is not None:
        field_ += _llm_rounds(proposer, llm_rounds, field_, train, holdout, mem,
                              refused, track=None, records=records,
                              feedback=feedback, human_notes=human_notes,
                              on_note=_on_note)

    mark("combined evaluation: policies x found fabrics")

    # The cross product IS the design space: every report line is a pair, because a
    # hash is only as good as the fabric that carries it and vice versa.
    from flux_profile import phase as _tphase

    scored = []
    for sol in field_:
        with _tphase("score: policy x 12 fabrics", why=sol.name):
            for fabric in fabrics:
                s = score(sol, fabric, train, holdout, mem)
                scored.append(s)
                if records is not None:
                    records.trial(
                        {"policy": sol.name, "fabric": fabric.name,
                         "schedule": sol.schedule,
                         "pipe_latency": fabric.pipe_latency,
                         "area_units": fabric.gate_units},
                        s.pair_name, rung="analytic", strategy="cross",
                        metrics={"holdout_latency": s.holdout.avg_latency,
                                 "holdout_throughput": s.holdout.throughput,
                                 "area_units": float(s.area_score),
                                 "pad_fraction": s.pad_fraction})
                if track:
                    track(s)

    # THE BIG LOOP (D386): alternate the two little loops -- mapping tuned per
    # fabric, fabrics fitted per mapping -- until the front stops moving or the
    # round budget ends. Every new pair goes through the same scorer and the same
    # holdout judgment as the curated field; nothing coordinated gets a discount.
    for round_i in range(coordination_rounds):
        before = {s_.pair_name for s_ in pareto_front(scored)}
        with _tphase("coordinate: little loops", why=f"round {round_i + 1}"):
            new = coordinate(scored, train, holdout, mem,
                             climb_rounds=climb_rounds or 30,
                             seed=seed + 100 + round_i, track=track)
        existing = {s_.pair_name for s_ in scored}
        scored += [s_ for s_ in new if s_.pair_name not in existing]
        if {s_.pair_name for s_ in pareto_front(scored)} == before:
            break

    if records is not None:
        for r in refused:
            records.trial({"refused": r}, f"refused:{hash(r) & 0xffff:04x}",
                          rung="gate", strategy="llm", metrics=None, error=r)
        records.conclude({"conclusion": conclude_dict_safe(scored)})

    front = pareto_front(scored)
    certs: list[Certificate] = []
    with _tphase("prove: certificates by exhaustion",
                 why=f"{len(front)} frontier pairs x 3 modes x 2 tiles"):
        for s in front:
            for mode in (Mode.Loop_Row_Col, Mode.Loop_Col_Row, Mode.Loop_4x4_H):
                for tile in certify_tiles:
                    certs.append(certify(s.solution, mem, mode=mode, rt=tile[0],
                                         ct=tile[1], lt=tile[2], dim=64,
                                         fabric=s.fabric, label=s.pair_name))
    # Final drain, unconditionally: on a model-free run (or a note typed after the
    # last proposer round) the line is still persisted and reported -- it just
    # honestly reached no prompt.
    from flux_feedback import drain_guidance

    drain_guidance(feedback, human_notes, on_note=_on_note)
    return ConflictStudy(scored=scored, front=front, certificates=certs,
                         refused=refused,
                         notes=[n.text for n in human_notes])


def conclude_dict_safe(scored: list[Scored]) -> dict[str, Any]:
    """The conclusion over what is scored so far, never raising -- it feeds the
    record, and the record must not fail the run."""
    try:
        front = pareto_front(scored)
        study = ConflictStudy(scored=scored, front=front, certificates=[],
                              refused=[])
        return conclude(study)
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


def _record_context(records) -> str:
    """What earlier runs of this campaign concluded, for the proposer's prompt --
    the flywheel's read-back half (D397)."""
    if records is None or not records.resumed:
        return ""
    lines = []
    for c in records.conclusions(limit=2):
        bal = (c.get("conclusion") or {}).get("balanced_pick") or {}
        if bal.get("pair"):
            lines.append(f"an earlier run's balanced pick: {bal['pair']} "
                         f"({bal.get('latency', 0):.2f} cy)")
    known = records.known(rung="analytic", metric="holdout_throughput")
    for cand, v in known[:3]:
        lines.append(f"measured earlier: {cand.get('policy')} + {cand.get('fabric')} "
                     f"reached {v:.2f} rows/cy")
    # Head-to-head verdicts (D400): the pair's two REAL knobs are the policy and the
    # fabric's name -- its pipeline depth and area are consequences of the name, so
    # they are dropped before pairing or no pair would ever count as controlled.
    from flux_extract import head_to_head

    duels = head_to_head([({"policy": c.get("policy"), "fabric": c.get("fabric")}, v)
                          for c, v in known if c.get("policy")],
                         metric="rows/cy on holdout", top=4)
    lines += [d.describe() for d in duels]
    if not lines:
        return ""
    return ("WHAT THE RECORD SHOWS (this campaign's earlier runs; directions, not "
            "instructions):\n" + "\n".join(f"  * {l}" for l in lines) + "\n")


def _llm_rounds(proposer, rounds: int, field_: list[Solution],
                train: list[Workload], holdout: list[Workload], mem: Memory,
                refused: list[str], track=None, records=None,
                feedback=None, human_notes=None, on_note=None) -> list[Solution]:
    """The model proposes XOR tap sets as JSON; every proposal passes the injectivity
    gate and a train-side evaluation on the ideal fabric, or is refused with the
    reason -- proposals are hypotheses, measurements are verdicts (D297). Accepted
    policies join the field and are scored across every fabric like everything else."""
    from flux_llm import strip_markdown_fence

    fabric = xbar_full(mem.banks)
    history: list[tuple[str, float]] = []
    for sol in field_:
        history.append((sol.name, score(sol, fabric, train, holdout, mem)
                        .train.avg_latency))
    out: list[Solution] = []
    from flux_feedback import drain_guidance

    for k in range(rounds):
        best = min(h[1] for h in history)
        lines = "\n".join(f"{n}: train avg latency {v:.3f}" for n, v in history)
        # Round boundary: whatever the operator typed since the last round joins THIS
        # prompt, labelled (D388) -- advisory; the gate below is unchanged.
        human = drain_guidance(feedback, human_notes if human_notes is not None
                               else [], on_note=on_note)
        prompt = (
            (human + "\n" if human else "") +
            _record_context(records) +
            "Bank-hash design: 32 banks, bank bit i = XOR of address bits taps[i].\n"
            "Propose taps as JSON {\"taps\": [[..5 lists of address-bit indices..]]}, "
            "address bits 0..15, at most 4 bits per bank bit. The low 5x5 submatrix "
            "must be invertible over GF(2) or the hash corrupts data and is refused.\n"
            f"Best train avg latency so far: {best:.3f} cycles.\n"
            f"Measured so far:\n{lines}\nJSON only.")
        try:
            reply = json.loads(strip_markdown_fence(proposer.propose(prompt)))
            taps = tuple(tuple(int(b) for b in t) for t in reply["taps"])
            mapping = XorFold(taps=taps, name=f"xor-llm-{k}")
        except Exception as exc:  # noqa: BLE001 -- refusal, not crash
            refused.append(f"round {k}: unparseable proposal ({exc})")
            continue
        if not injective(mapping, mem.m):
            refused.append(f"round {k}: taps {taps} not injective on low {mem.m} bits")
            continue
        h = BankHash(mapping=mapping, bank_bits=mem.m)
        sol = Solution(name=f"S8-xor-llm-{k}", hash_of=lambda layout, _h=h: _h,
                       targets=("intra-operand",),
                       metadata=(), assumptions=("model-proposed taps",))
        out.append(sol)
        history.append((sol.name,
                        score(sol, fabric, train, holdout, mem).train.avg_latency))
    return out
