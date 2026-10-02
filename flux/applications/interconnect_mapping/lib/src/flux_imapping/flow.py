"""The interconnect_mapping study's arithmetic: evaluation, little loops, certificates and
conclusion, used by the commands of `steps` (the phases of `applications/interconnect_mapping/problem.yaml`).

Shape (D378):
1. Curated field (`solutions.catalog`) measured on train workloads.
2. Searches extend it: a hill-climb over injective XOR tap sets, bankmap's z3 fold, and
   model rounds when the document asks; every proposal passes the injectivity gate and the
   same evaluator, or is refused with the reason.
3. Everything re-measured on holdout workloads (anti-overfitting); both numbers reported.
4. Four costs: A = area score (structural gate-units; the `phys` stage grounds finalists in
   um2), B = padding fraction (stored/true - 1, exact), C = average access latency,
   D = throughput (rows/cycle). Pareto dominance over all four.
5. Certificates: per (mode, tile, pitch) family, intra-operand conflict-freedom is proved by
   exhaustion over the finite domain (dims <= 64); impossibility floors by pigeonhole: k rows
   need >= ceil(k / min(P, B)) cycles.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Sequence

import numpy as np

from flux_bankmap.mapping import XorFold

from .conflict import BankHash, TrafficMetrics, intra_operand, run_traffic
from .fabric import FabricModel, xbar_full
from .model import BLOCK_OF, Memory, Mode, TensorLayout, TileAccess, VECTOR_MODES
from .solutions import Solution, injective, solution_to_dict
from .workloads import Workload


# ---------------------------------------------------------------- evaluation

@dataclass(frozen=True, slots=True)
class Scored:
    """One design point: a map policy paired with an interconnect topology. The pair is the
    unit of reporting, since a hash and a fabric are only as good as each other."""

    solution: Solution
    fabric: FabricModel
    train: TrafficMetrics
    holdout: TrafficMetrics
    pad_fraction: float          # Cost B, exact
    area_score: float            # Cost A, structural gate-units (the `phys` stage grounds it in um2)

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
            "pad_fraction": self.pad_fraction, "area_score": self.area_score,
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
    """4-objective dominance (A, B, C, -D), all minimized on holdout numbers -- the one
    rule in `flux_frontier` (D429), in input order."""
    from flux_frontier import pareto

    return pareto(scored, key=lambda s: tuple(s.costs))


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
    """Hill-climb over injective XOR tap sets, judged on train average latency (holdout stays
    untouched until final scoring). `fabric` is the topology tuned against: the ideal
    crossbar by default, a real blocking fabric from `mapping_loop`. None if nothing beat
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


# ---------------------------------------------------------------- certificates

@dataclass(frozen=True, slots=True)
class Certificate:
    """One proved statement: for `solution`, every origin of a `tile` sweep over every legal
    tensor with dims <= bound in `mode` at pitch `pitch` is intra-operand conflict-free
    (conflict_bound <= port_bound). Proof by exhaustion over the finite domain (dims 1..64)."""

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


def interconnect_loop(train: list[Workload], holdout: list[Workload], mem: Memory,
                      ref_policy: Solution, *, keep: int = 8,
                      say: Callable[[str], None] = print) -> list[FabricModel]:
    """Little loop A (D392): find interconnects before pairing them with mappings.

    Screens ~30 fabric candidates over parameter grids under one reference mapping and keeps
    the Pareto set over (area, latency, throughput) plus the best-latency and best-area
    corners. `say` is the loop's log."""
    from flux_profile import mark, phase as _tphase

    from .fabric import generate_fabrics

    mark("interconnect little-loop: find fabrics")
    candidates = generate_fabrics(mem.m)
    screened: list[Scored] = []
    for fabric in candidates:
        with _tphase("interconnect: screen fabric", why=fabric.name,
                     levels=len(fabric.levels), areaU=fabric.gate_units):
            screened.append(score(ref_policy, fabric, train, holdout, mem))
    front = pareto_front(screened)
    ranked = sorted(front, key=lambda x: x.holdout.avg_latency)
    chosen = [s_.fabric for s_ in ranked[:keep]]
    say(f"interconnect loop: {len(candidates)} candidates screened -> "
        f"{len(front)} on the screen front -> keeping {len(chosen)}: "
        + ", ".join(f.name for f in chosen))
    return chosen


def strides_from_workloads(workloads: list[Workload], mem: Memory,
                           limit: int = 8) -> list[int]:
    """The dominant access strides in bank-rows: each tensor's row pitch plus 1 for the
    sequential inner walk -- the stride set bankmap's solver takes."""
    strides: dict[int, int] = {}
    for w in workloads:
        for t in w.tensors:
            pitch_rows = max(1, t.inner_pitch >> mem.e)
            strides[pitch_rows] = strides.get(pitch_rows, 0) + 1
    strides[1] = strides.get(1, 0) + 1
    ranked = sorted(strides, key=lambda k: -strides[k])
    return sorted(ranked[:limit])


def z3_mapping_policy(train: list[Workload], mem: Memory, *, z3_seconds: int = 15,
                      say: Callable[[str], None] = print) -> Solution | None:
    """Little loop B's exact stage: bankmap's z3 solver on the dominant strides, returning a
    proven conflict-free XOR-fold as policy S10-z3-proven, or None with the reason."""
    from flux_bankmap.problem import MappingRequest
    from flux_bankmap.solve_z3 import solve

    strides = strides_from_workloads(train, mem)
    try:
        request = MappingRequest(strides=strides, concurrent=16,
                                 banks=mem.banks, address_bits=16,
                                 z3_seconds=z3_seconds)
        mapping, trace = solve(request)
    except Exception as exc:  # noqa: BLE001 -- an unexpressible request is a report
        say(f"z3 mapping route: refused ({type(exc).__name__}: {exc})")
        return None
    if mapping is None:
        say(f"z3 mapping route: no conflict-free fold for strides {strides} "
            f"(the solver's verdict, not a timeout excuse)")
        return None
    if not injective(mapping, mem.m):
        say("z3 mapping route: solved fold not injective on low bits; refused")
        return None
    say(f"z3 mapping route: PROVEN fold for strides {strides}: {mapping.describe()}")
    h = BankHash(mapping=mapping, bank_bits=mem.m)
    return Solution(
        name="S10-z3-proven", hash_of=lambda layout, _h=h: _h,
        targets=("intra-operand",), metadata=(),
        assumptions=(f"conflict-free PROVEN by z3 for strides {strides} "
                     "(16 concurrent); other patterns measured, not promised",))


def mapping_loop(fabric: FabricModel, base: Solution, train: list[Workload],
                 holdout: list[Workload], mem: Memory, *, rounds: int = 30,
                 seed: int = 0) -> Scored | None:
    """For one fixed interconnect, an XOR-tap climb whose cycle law includes this fabric's
    capacity tree. Returns a Scored pair named for the fabric it was tuned against."""
    return climb_xor(train, holdout, mem, base, rounds=rounds, seed=seed,
                     fabric=fabric, name=f"S6-xor@{fabric.name}")


def fit_fabric(policy: Solution, fabric: FabricModel, train: list[Workload],
               mem: Memory) -> FabricModel | None:
    """Rightsize one capacity-tree fabric to the residual traffic a policy leaves it.

    Each level's capacity becomes the measured peak subtree load over the train steps
    (never above the original), so the fit adds zero blocking on train; holdout judges it.
    Area shrinks with the links removed. Non-blocking fabrics have nothing to fit."""
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
               seed: int = 0) -> list[Scored]:
    """One step of block-coordinate descent between the two little loops: tune a mapping
    for the front's top fabrics and fit the fabric of its top pairs, scoring every new pair
    the same way. The caller iterates until the front stops moving."""
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
    for s_ in sorted(front, key=lambda x: x.holdout.avg_latency)[:top_k]:
        with _tphase("interconnect little-loop: fit fabric to mapping",
                     why=s_.pair_name):
            fitted = fit_fabric(s_.solution, s_.fabric, train, mem)
        if fitted is not None:
            out.append(score(s_.solution, fitted, train, holdout, mem))
    return out


def conclude(scored: list[Scored], front: list[Scored],
             certificates: Sequence[Certificate] = ()) -> dict[str, Any]:
    """The decision-first summary, derived from the measured field (never hard-coded).

    Names the front's three corners, the consensus fabric (most frontier rows), every policy
    and fabric with no frontier row plus its best showing, and the recommended pair's proved
    tile families."""
    if not front:
        return {"note": "empty front"}
    # flux_frontier's corners with deterministic tie-breaks (equal throughput -> lower
    # latency, equal latency -> smaller area) and the balanced pick as the knee of all four.
    from flux_frontier import corner, knee_ranked

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
    for s_ in scored:
        name = s_.solution.name
        if name not in front_policies:
            best = min((x for x in scored if x.solution.name == name),
                       key=lambda x: x.holdout.avg_latency)
            losers.setdefault(
                name, f"best showing {best.holdout.avg_latency:.2f} cy / "
                      f"{best.holdout.throughput:.2f} rows/cy ({best.fabric.name})")
    for s_ in scored:
        fname = s_.fabric.name
        if fname not in front_fabrics:
            best = min((x for x in scored if x.fabric.name == fname),
                       key=lambda x: x.holdout.avg_latency)
            losers.setdefault(
                fname, f"best showing {best.holdout.avg_latency:.2f} cy at "
                       f"{best.area_score:.0f} areaU ({best.solution.name})")

    balanced = ranked[0]
    proved = [f"{c.mode} tile {c.tile}" for c in certificates
              if c.solution == balanced.pair_name and c.holds]
    refuted = sum(1 for c in certificates
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


# ---------------------------------------------------------------- the conclusion on record

def conclude_dict_safe(scored: list[Scored]) -> dict[str, Any]:
    """The conclusion over what is scored so far, never raising -- it feeds the
    record, and the record must not fail the run."""
    try:
        return conclude(scored, pareto_front(scored))
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


def _balanced_pick(c: dict) -> str | None:
    bal = (c.get("conclusion") or {}).get("balanced_pick") or {}
    return (f"an earlier run's balanced pick: {bal['pair']} ({bal.get('latency', 0):.2f} cy)"
            if bal.get("pair") else None)


def _measured_earlier(r) -> list[str]:
    return [f"measured earlier: {cand.get('policy')} + {cand.get('fabric')} reached {v:.2f} rows/cy"
            for cand, v in r.known(stage="analytic", metric="holdout_throughput")[:3]]


def record_readback():
    """This study's read-back as a declared knowledge source. The pair's real knobs are the
    policy and the fabric's name; depth and area follow from the name, so they are dropped
    before pairing or no pair would count as controlled (D400)."""
    from flux_knowledge import RecordReadback

    return RecordReadback(stage="analytic", metric="holdout_throughput",
                          knobs=("policy", "fabric"), metric_label="rows/cy on holdout", top=4,
                          title="record: what earlier runs measured",
                          conclusion=_balanced_pick, extra=_measured_earlier)


def _record_context(records) -> str:
    """The same text, rendered straight for this study's own callers and tests."""
    from types import SimpleNamespace

    text = record_readback().render(SimpleNamespace(records=records))
    return text + "\n" if text else ""
