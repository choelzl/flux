"""flux_imapping (docs/decisions.md D378): the layout model is checked against
hand-computed addresses (the correctness heart -- everything downstream is arithmetic
over these), injectivity against brute force, conflicts against a literal simulation,
and the study loop end to end with no model."""

from __future__ import annotations

import numpy as np
import pytest

from flux_imapping import (
    Memory, Mode, TensorLayout, TileAccess, butterfly, catalog, catalog_fabrics,
    certify, generate, injective, intra_operand, pareto_front, pigeonhole_floor,
    run_study, score, shared_cycle, swizzle_for, train_holdout, xbar_full,
)
from flux_bankmap.mapping import Modulo, XorFold

MEM = Memory()  # m=5, e=2: 32 banks, 4-element rows


# ---------------------------------------------------------------- layout model

def test_plain_mode_linearization_matches_hand_computation():
    # 4x8x2 Loop_Row_Col: addr = (l*4 + r)*8 + c
    t = TensorLayout(r=4, c=8, l=2, mode=Mode.Loop_Row_Col, base=0)
    assert t.element_addrs(0, 0, 0) == 0
    assert t.element_addrs(0, 5, 0) == 5
    assert t.element_addrs(1, 0, 0) == 8
    assert t.element_addrs(0, 0, 1) == 32
    assert t.element_addrs(3, 7, 1) == (1 * 4 + 3) * 8 + 7
    # Col_Row_Loop (inner Loop): addr = (c*R + r)*L + l
    t2 = TensorLayout(r=3, c=5, l=4, mode=Mode.Col_Row_Loop, base=0)
    assert t2.element_addrs(2, 4, 3) == (4 * 3 + 2) * 4 + 3


def test_block_mode_linearization_2x2_h_and_v():
    t = TensorLayout(r=4, c=4, l=1, mode=Mode.Loop_2x2_H, base=0)
    # H: within block columns first, blocks horizontally first
    assert t.element_addrs(0, 0, 0) == 0
    assert t.element_addrs(0, 1, 0) == 1   # within-block col
    assert t.element_addrs(1, 0, 0) == 2   # within-block row
    assert t.element_addrs(0, 2, 0) == 4   # next block to the right
    assert t.element_addrs(2, 0, 0) == 8   # next block row
    tv = TensorLayout(r=4, c=4, l=1, mode=Mode.Loop_2x2_V, base=0)
    assert tv.element_addrs(1, 0, 0) == 1  # V: within-block rows first
    assert tv.element_addrs(0, 1, 0) == 2
    assert tv.element_addrs(2, 0, 0) == 4  # next block DOWN comes first
    assert tv.element_addrs(0, 2, 0) == 8


def test_every_mode_linearization_is_a_bijection():
    """The storage contract: distinct coordinates -> distinct element addresses,
    covering 0..N-1 exactly. Checked for all 12 modes on a legal small tensor."""
    for mode in Mode:
        if mode in (Mode.Loop_Row, Mode.Row_Loop):
            t = TensorLayout(r=8, c=1, l=4 if mode is Mode.Loop_Row else 8,
                             mode=mode, base=0)
            rr, cc, ll = np.meshgrid(np.arange(t.r), np.arange(1), np.arange(t.l),
                                     indexing="ij")
        else:
            t = TensorLayout(r=4, c=8, l=4, mode=mode, base=0)
            rr, cc, ll = np.meshgrid(np.arange(t.r), np.arange(t.c), np.arange(t.l),
                                     indexing="ij")
        addrs = t.element_addrs(rr, cc, ll).ravel()
        assert sorted(addrs) == list(range(t.true_elems)), mode.name


def test_vector_modes_and_pad_inner_to_change_pitch_and_cost():
    t = TensorLayout(r=8, c=1, l=3, mode=Mode.Loop_Row, base=0)
    assert t.element_addrs(2, 0, 1) == 8 + 2
    padded = TensorLayout(r=8, c=8, l=1, mode=Mode.Loop_Row_Col, base=0,
                          pad_inner_to=12)
    assert padded.element_addrs(1, 0, 0) == 12
    assert padded.stored_elems == 8 * 12 and padded.true_elems == 64


def test_tile_rows_clip_at_tensor_edges_and_dedupe():
    t = TensorLayout(r=4, c=8, l=1, mode=Mode.Loop_Row_Col, base=0)
    a = TileAccess(layout=t, r0=2, c0=4, l0=0, rt=4, ct=8, lt=1, ports=16)
    rows = a.rows(MEM)
    # rows 2..3, cols 4..7 -> element addrs {20..23, 28..31} -> bank-rows {5, 7}
    assert rows.tolist() == [5, 7]


# ---------------------------------------------------------------- injectivity

def test_injectivity_matches_brute_force():
    good = XorFold(taps=tuple((i, i + 5) for i in range(5)))
    bad = XorFold(taps=((0, 1), (0, 1), (2,), (3,), (4,)))  # two equal bank bits
    assert injective(good, 5) and not injective(bad, 5)
    # brute force: (bank, line) distinct over a window
    rows = np.arange(1 << 12, dtype=np.uint64)
    for mapping, expect in ((good, True), (bad, False)):
        banks = mapping.banks_of(rows, 5)
        keys = set(zip((rows >> 5).tolist(), banks.tolist()))
        assert (len(keys) == rows.size) is expect


def test_swizzle_is_metadata_only_and_injective():
    """The read/write-consistency rule: the hash depends on the LAYOUT, never the tile.
    Same tensor, different tile shapes -> byte-identical bank mapping."""
    t = TensorLayout(r=16, c=32, l=1, mode=Mode.Loop_Row_Col, base=0)
    h = swizzle_for(t, MEM)
    assert injective(h, MEM.m)
    rows = np.arange(4096, dtype=np.uint64)
    b1 = h.banks_of(rows, MEM.m)
    h2 = swizzle_for(t, MEM)  # a "different access" recomputes from the same metadata
    assert np.array_equal(b1, h2.banks_of(rows, MEM.m))


# ---------------------------------------------------------------- conflicts

def test_intra_operand_conflict_counts_match_literal_simulation():
    # 8 rows of a 8x16 row-major tensor, one column-tile: rows hit addr strides of
    # pitch 16/4 = 4 rows -> under modulo-32, banks {0,4,8,...} distinct -> port bound.
    t = TensorLayout(r=8, c=16, l=1, mode=Mode.Loop_Row_Col, base=0)
    from flux_imapping import BankHash
    h = BankHash(mapping=Modulo(0), bank_bits=MEM.m)
    a = TileAccess(layout=t, r0=0, c0=0, l0=0, rt=8, ct=4, lt=1, ports=4)
    rep = intra_operand(a, MEM, lambda layout: h)
    assert rep.rows == 8 and rep.port_bound == 2
    assert rep.conflict_bound == 1 and rep.cycles == 2
    # pitch 32 columns = 8 rows: stride 8 under modulo-32 -> only 4 distinct banks
    t2 = TensorLayout(r=8, c=32, l=1, mode=Mode.Loop_Row_Col, base=0)
    a2 = TileAccess(layout=t2, r0=0, c0=0, l0=0, rt=8, ct=4, lt=1, ports=8)
    rep2 = intra_operand(a2, MEM, lambda layout: h)
    assert rep2.conflict_bound == 2 and rep2.conflict_limited


def test_shared_cycle_adds_bank_load_across_operands():
    t = TensorLayout(r=4, c=16, l=1, mode=Mode.Loop_Row_Col, base=0)
    from flux_imapping import BankHash
    h = BankHash(mapping=Modulo(0), bank_bits=MEM.m)
    a = TileAccess(layout=t, r0=0, c0=0, l0=0, rt=1, ct=16, lt=1, ports=4)
    rep1 = intra_operand(a, MEM, lambda layout: h)
    rep2 = shared_cycle([a, a], MEM, lambda layout: h)  # same rows twice: doubled load
    assert rep2.conflict_bound == 2 * rep1.conflict_bound
    assert pigeonhole_floor(rows=64, ports=16, mem=MEM) == 4


# ---------------------------------------------------------------- workloads & study

def test_workloads_are_deterministic_and_split_is_disjoint():
    w1, w2 = generate(7), generate(7)
    assert len(w1.steps) == len(w2.steps) and w1.tensors[0].describe() == w2.tensors[0].describe()
    train, holdout = train_holdout(0, n_train=2, n_holdout=2)
    assert {w.seed for w in train}.isdisjoint({w.seed for w in holdout})


def test_catalog_pairs_all_score_and_costs_are_pairwise():
    mem = MEM
    train, holdout = train_holdout(3, n_train=1, n_holdout=1, ops=3)
    fabric = xbar_full(mem.banks)
    scored = [score(s, fabric, train, holdout, mem) for s in catalog(mem)]
    assert all(s.holdout.throughput > 0 for s in scored)
    front = pareto_front(scored)
    assert front, "empty Pareto front"
    by_name = {s.solution.name: s for s in scored}
    # the padded-skew policy really pays Cost B and nothing else does
    assert by_name["S5-pad-skew"].pad_fraction > 0
    assert by_name["S0-modulo-xbar"].pad_fraction == 0
    assert " + xbar-full" in scored[0].pair_name


def test_fabric_blocking_model_bounds_and_prices():
    mem = MEM
    fabrics = {f.name: f for f in catalog_fabrics(mem.m)}
    assert {"xbar-full", "benes", "fly-r2", "fly-r2-slim4", "fly-r4", "cx-16",
            "hier-4x8", "unit-split", "ring-8"} <= set(fabrics)
    # cheaper by construction, blocking by construction
    assert fabrics["fly-r2-slim2"].gate_units < fabrics["fly-r2"].gate_units < \
        fabrics["xbar-full"].gate_units
    counts = np.zeros(mem.banks, dtype=np.int64)
    counts[:4] = 4  # 16 requests crammed into banks 0..3 (one subtree)
    assert fabrics["xbar-full"].load(counts, mem.m) == 1
    assert fabrics["fly-r2-slim2"].load(counts, mem.m) > 1  # bisection paid for area
    spread = np.ones(mem.banks, dtype=np.int64)  # 32 requests, perfectly spread
    assert fabrics["fly-r2"].load(spread, mem.m) == 1
    # benes: crossbar permutation capability, a fraction of the gates, deepest pipe
    assert fabrics["benes"].load(counts, mem.m) == 1
    assert fabrics["benes"].gate_units < fabrics["xbar-full"].gate_units
    assert fabrics["benes"].pipe_latency > fabrics["xbar-full"].pipe_latency
    # ring: near-free gates, brutal root capacity even on perfectly spread traffic
    assert fabrics["ring-8"].load(spread, mem.m) == 4  # 32 requests / 8 slots
    assert fabrics["ring-8"].gate_units < fabrics["cx-16"].gate_units


def test_fabric_bound_reaches_the_cycle_law():
    """A request the HASH spreads perfectly can still be serialized by a blocking
    FABRIC -- the reason topology is a first-class axis of this study."""
    mem = MEM
    t = TensorLayout(r=8, c=16, l=1, mode=Mode.Loop_Row_Col, base=0)
    from flux_imapping import BankHash
    h = BankHash(mapping=Modulo(0), bank_bits=mem.m)
    a = TileAccess(layout=t, r0=0, c0=0, l0=0, rt=4, ct=16, lt=1, ports=16)
    ideal = intra_operand(a, mem, lambda layout: h, xbar_full(mem.banks))
    slim = intra_operand(a, mem, lambda layout: h, butterfly(mem.banks, mem.m, slim=4))
    assert ideal.conflict_bound == 1 and ideal.fabric_bound == 1
    assert slim.fabric_bound > 1 and slim.cycles > ideal.cycles


def test_certify_proves_and_refutes():
    mem = MEM
    sols = {s.name: s for s in catalog(mem)}
    # The resonant family: dim=64 makes the row pitch 16 bank-rows, so an 8-row
    # column tile walks banks {0,16} under plain modulo -- refuted by counterexample;
    # the metadata swizzle folds the pitch bit and is PROVED clean on the same family.
    cert = certify(sols["S2-swizzle-meta"], mem, mode=Mode.Loop_Row_Col,
                   rt=8, ct=4, lt=1, dim=64)
    assert cert.holds and cert.checked_origins > 0
    cert0 = certify(sols["S0-modulo-xbar"], mem, mode=Mode.Loop_Row_Col,
                    rt=8, ct=4, lt=1, dim=64)
    assert not cert0.holds and cert0.counterexample is not None
    # a blocking fabric can flip a PROVED family to REFUTED for the same hash
    certf = certify(sols["S2-swizzle-meta"], mem, mode=Mode.Loop_Row_Col,
                    rt=8, ct=4, lt=1, dim=64,
                    fabric=butterfly(mem.banks, mem.m, slim=8))
    assert not certf.holds


def test_mapping_loop_tunes_against_the_given_fabric():
    """The little mapping loop's contract: whatever it returns was judged on TRAIN
    against the fabric it was given, and only improvements are accepted -- so its
    train latency is never worse than the base policy's on that same fabric."""
    from flux_imapping import butterfly, mapping_loop
    mem = MEM
    train, holdout = train_holdout(5, n_train=1, n_holdout=1, ops=3)
    base = next(s for s in catalog(mem) if s.name == "S1-xor-global")
    fabric = butterfly(mem.banks, mem.m, slim=4)
    base_scored = score(base, fabric, train, holdout, mem)
    tuned = mapping_loop(fabric, base, train, holdout, mem, rounds=15, seed=3)
    if tuned is not None:  # None = nothing beat the base, also a legal outcome
        assert tuned.train.avg_latency <= base_scored.train.avg_latency
        assert tuned.pair_name.endswith(fabric.name)


def test_fit_fabric_shrinks_area_and_never_blocks_train_traffic():
    """The little interconnect loop's contract: fitted capacities equal the measured
    per-level peaks, so on the SAME train traffic the fitted fabric adds no blocking,
    and the links removed shrink the structural area."""
    from flux_imapping import Workload, butterfly, fit_fabric, xbar_full
    from flux_imapping.conflict import run_traffic
    mem = MEM
    # Light residual traffic BY CONSTRUCTION -- fitting only trims a fabric that is
    # over-provisioned for what the mapping leaves it (under peak traffic fit_fabric
    # honestly returns None, because nothing is over-provisioned; a separate check
    # below pins that).
    t_small = TensorLayout(r=8, c=16, l=1, mode=Mode.Loop_Row_Col, base=0)
    a = TileAccess(layout=t_small, r0=0, c0=0, l0=0, rt=2, ct=8, lt=1, ports=16)
    train = [Workload(steps=[[a]], tensors=[t_small], seed=0)]
    policy = next(s for s in catalog(mem) if s.name == "S1-xor-global")
    fabric = butterfly(mem.banks, mem.m, slim=1)   # generously provisioned
    fitted = fit_fabric(policy, fabric, train, mem)
    assert fitted is not None and fitted.name.endswith("-fit")
    assert fitted.gate_units < fabric.gate_units
    assert all(nc <= oc for (_, nc), (_, oc) in zip(fitted.levels, fabric.levels))
    for w in train:
        loose = run_traffic(w.steps, mem, policy.hash_of, fabric=fabric)
        tight = run_traffic(w.steps, mem, policy.hash_of, fabric=fitted)
        assert tight.cycles == loose.cycles  # zero blocking added on train
    assert fit_fabric(policy, xbar_full(mem.banks), train, mem) is None
    # under saturating traffic nothing is over-provisioned: None is the honest answer
    heavy, _ = train_holdout(5, n_train=1, n_holdout=1, ops=3)
    assert fit_fabric(policy, butterfly(mem.banks, mem.m, slim=4), heavy, mem) is None


def test_big_loop_coordination_never_worsens_the_front():
    study0 = run_study(seed=2, ops=2, climb_rounds=5, coordination_rounds=0)
    study2 = run_study(seed=2, ops=2, climb_rounds=5, coordination_rounds=2)
    best0 = min(s.holdout.avg_latency for s in study0.front)
    best2 = min(s.holdout.avg_latency for s in study2.front)
    assert best2 <= best0 + 1e-9
    # coordinated pairs are marked by their names, so the report can attribute them
    coordinated = [s.pair_name for s in study2.scored
                   if "@" in s.pair_name or "-fit" in s.pair_name]
    assert coordinated, "coordination rounds produced no new pairs"


def test_simulator_agrees_exactly_where_a_scheduler_cannot_help():
    """The confidence ladder's middle rung (D394): on traffic with no scheduling
    freedom (one access, distinct banks), the discrete-event simulator and the
    analytic law must agree EXACTLY -- agreement here is evidence about the model."""
    from flux_imapping import BankHash, simulate_traffic, xbar_full
    from flux_imapping.conflict import run_traffic
    mem = MEM
    t = TensorLayout(r=8, c=16, l=1, mode=Mode.Loop_Row_Col, base=0)
    h = BankHash(mapping=Modulo(0), bank_bits=mem.m)
    a = TileAccess(layout=t, r0=0, c0=0, l0=0, rt=8, ct=4, lt=1, ports=4)
    steps = [[a]]
    analytic = run_traffic(steps, mem, lambda l: h, fabric=xbar_full(mem.banks))
    sim = simulate_traffic(steps, mem, lambda l: h, fabric=xbar_full(mem.banks))
    assert sim.cycles == analytic.cycles == 2      # 8 rows through 4 ports
    assert sim.rows == analytic.rows == 8


def test_simulator_is_never_more_optimistic_than_the_analytic_bound():
    """sim >= analytic on real traffic: the greedy arbiter can only lose to the
    perfect scheduler the analytic law assumes -- and the measured gap is the
    number the report prints."""
    from flux_imapping import cross_check
    mem = MEM
    train, holdout = train_holdout(3, n_train=1, n_holdout=1, ops=3)
    fabric = next(f for f in catalog_fabrics(mem.m) if f.name == "hier-4x8")
    for sol in catalog(mem):
        if sol.name not in ("S1-xor-global", "S9-ab-stagger"):
            continue
        sc = score(sol, fabric, train, holdout, mem)
        cc = cross_check(sc, train, holdout, mem)
        assert cc["sim_latency"] >= cc["analytic_latency"] - 1e-9, sol.name
        assert cc["sim_throughput"] <= cc["analytic_throughput"] + 1e-9, sol.name
        assert cc["latency_gap_pct"] < 100.0, "greedy should not be catastrophically off"


def test_run_study_end_to_end_without_model():
    study = run_study(seed=1, ops=2, climb_rounds=5, llm_rounds=0)
    # >= 6 policies x 4 fabrics: every design point is a pair, named as one
    assert len(study.scored) >= 24
    assert study.front and study.certificates
    assert all(" + " in s.pair_name for s in study.scored)
    fabrics_on_front = {s.fabric.name for s in study.front}
    assert fabrics_on_front, "front lost its fabric axis"
    d = study.to_dict()
    assert d["front"] and " + " in d["front"][0]
    assert isinstance(d["certificates"][0]["tile"], list)
