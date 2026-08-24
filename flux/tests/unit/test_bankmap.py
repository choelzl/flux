"""Conflict-free bank mapping: the checker is the authority, the solver must agree with it.

The properties that matter: the checker walks EVERY start address (a sample is not a guarantee);
a mapping z3 returns is one the checker accepts; z3's "unsat" is a proof, so no XOR-fold the
checker accepts may exist when it says so; and the open family's expression evaluator refuses
anything that is not arithmetic over the address.
"""

from __future__ import annotations

from pathlib import Path

import pytest

FLUX_ROOT = Path(__file__).resolve().parents[2]

from flux_bankmap import (  # noqa: E402
    Expr, InvalidExpression, InvalidRequest, MappingRequest, Modulo, XorFold, check,
    modulo_baseline,
)
from flux_bankmap.propose import parse_proposals  # noqa: E402
from flux_bankmap.solve_z3 import solve  # noqa: E402


def _req(strides, n, banks=8, bits=12, **kw):
    kw.setdefault("db", "")   # no campaign record in unit tests unless a test asks (D402)
    return MappingRequest(strides=tuple(strides), concurrent=n, banks=banks, address_bits=bits,
                          z3_seconds=20, **kw)


def test_modulo_fails_exactly_the_strides_that_are_multiples_of_the_bank_count():
    v = check(Modulo(0), _req([1, 3, 8, 16, 17], 8))
    verdicts = {s.stride: s.conflict_free for s in v.per_stride}
    assert verdicts == {1: True, 3: True, 8: False, 16: False, 17: True}
    assert v.worst.worst_distinct == 1, "every access of a stride-8 window lands in ONE bank"


def test_the_checker_walks_every_start_address():
    """A mapping that fails on one start in the whole space is not conflict-free."""
    v = check(Modulo(0), _req([8], 8, bits=10))
    assert v.per_stride[0].total_starts == 1 << 10
    assert v.per_stride[0].conflicting_starts == 1 << 10


def test_the_fold_family_contains_the_baseline():
    r = _req([1, 8], 8)
    a, b = check(Modulo(0), r), check(modulo_baseline(3), r)
    assert [s.worst_distinct for s in a.per_stride] == [s.worst_distinct for s in b.per_stride]


def test_z3s_answer_is_one_the_checker_accepts():
    r = _req([1, 8, 16], 4)
    m, trace = solve(r)
    assert m is not None, trace.outcome
    assert check(m, r).conflict_free
    assert all(len(t) >= 1 for t in m.taps)


def test_z3_finds_the_cheapest_fold():
    """A single stride of 1 needs no XOR at all: three wires from the low bits."""
    m, _ = solve(_req([1], 8))
    assert m is not None and m.hardware_cost() == 0


def test_z3_proves_the_linear_family_insufficient_where_it_is():
    """Strides 1 and 8 with eight concurrent accesses across eight banks: no XOR-fold exists.

    Stride 1 makes the low three bits the only thing that varies inside a window, so the fold
    must read them; stride 8 holds those bits constant across its window, so the fold must not
    depend on them alone -- and with unaligned starts the carry couples the two in a way no
    linear map over GF(2) reconciles at N = B. The solver says unsat, and it is right.
    """
    m, trace = solve(_req([1, 8], 8))
    assert m is None
    assert "unsat" in trace.outcome


def test_a_smaller_concurrency_is_feasible_where_the_full_one_is_not():
    m, _ = solve(_req([1, 8], 4))
    assert m is not None and check(m, _req([1, 8], 4)).conflict_free


def test_a_constant_bank_bit_is_refused_by_construction():
    with pytest.raises(ValueError):
        XorFold(taps=((0,), (), (2,)))


def test_expressions_are_arithmetic_over_the_address_and_nothing_else():
    Expr("a ^ (a >> 3)")
    Expr("(a + (a >> 4)) % 8")
    for bad in ("import os", "a.bit_length()", "b + 1", "a ** 2", "open('x')", "a if a else 0"):
        with pytest.raises(InvalidExpression):
            Expr(bad)


def test_expression_cost_prices_the_divider_and_the_multiplier():
    assert Expr("a ^ (a >> 3)").hardware_cost() == 1
    assert Expr("a % 8").hardware_cost() == 0, "a modulo by a power of two is a mask"
    assert Expr("a % 7").hardware_cost() >= 100, "a real divider on the address path"
    assert Expr("a * 4").hardware_cost() == 0, "a multiply by a power of two is a shift"
    assert Expr("a * 3").hardware_cost() >= 60


def test_an_expression_is_checked_exactly_like_a_fold():
    r = _req([1, 8, 16], 4)
    fold, _ = solve(r)
    as_expr = Expr("(a >> 1 ^ a >> 4) & 1 | ((a >> 3) & 1) << 1 | ((a ^ a >> 5) & 1) << 2")
    # The fold z3 found is {a1^a4, a3, a0^a5}; written as an expression it must verify too.
    if fold and fold.taps == ((1, 4), (3,), (0, 5)):
        assert check(as_expr, r).conflict_free


def test_proposals_parse_both_forms_and_drop_the_unbuildable():
    reply = '''Here you go:
    [{"kind": "xor-fold", "taps": [[0, 3], [1, 4], [2, 5]], "why": "skew"},
     {"kind": "expr", "text": "a ^ (a >> 3)", "why": "fold the row"},
     {"kind": "expr", "text": "import os", "why": "nope"},
     {"kind": "magic", "why": "unknown"}]'''
    got = parse_proposals(reply)
    assert [type(m).__name__ for m, _ in got] == ["XorFold", "Expr"]
    assert got[0][1] == "skew"


def test_invalid_requests_are_refused_up_front():
    with pytest.raises(InvalidRequest):
        MappingRequest(strides=(1,), concurrent=9, banks=8)
    with pytest.raises(InvalidRequest):
        MappingRequest(strides=(1,), concurrent=4, banks=6)
    with pytest.raises(InvalidRequest):
        MappingRequest(strides=(), concurrent=4, banks=8)


def test_a_pigeonhole_witness_proves_the_request_impossible_for_any_mapping():
    """Strides 1 and 8 at N = B = 8: nine addresses must pairwise differ in bank."""
    from flux_bankmap import find_impossibility, max_feasible_concurrency

    w = find_impossibility(_req([1, 8], 8))
    assert w is not None
    assert len(w.addresses) == 9
    # every pairwise difference really is k*stride for some k < N
    d = {k * s for s in (1, 8) for k in range(1, 8)}
    for i, a in enumerate(w.addresses):
        for b in w.addresses[i + 1:]:
            assert abs(a - b) in d
    assert max_feasible_concurrency(_req([1, 8], 8)) < 8


def test_no_witness_where_a_mapping_exists():
    from flux_bankmap import find_impossibility

    assert find_impossibility(_req([1, 8, 16], 4)) is None
    assert find_impossibility(_req([1], 8)) is None


def test_the_study_refuses_an_impossible_request_without_a_solver_round():
    from flux_bankmap.flow import run_study

    said = []
    r = run_study(_req([1, 8, 16, 17], 8), log=said.append)
    assert r.conflict_free is False
    assert any("impossible for ANY mapping" in l for l in r.lessons)
    assert r.provenance.get("impossible") is True
    assert not any("z3: searching" in s for s in said), "no solver time on a proved impossibility"


def test_a_fold_with_the_wrong_number_of_bank_bits_is_dropped():
    reply = '[{"kind": "xor-fold", "taps": [[0],[1],[2],[3],[4],[5],[6],[7]], "why": "8 bits"}]'
    assert parse_proposals(reply, bank_bits=3) == []
    assert len(parse_proposals(reply)) == 1


# ---- crossbar stages: resources keyed by bank-index bits, with capacities -------------------
def test_a_crossbar_layout_becomes_stages_on_the_top_bank_bits():
    from flux_bankmap import crossbar_stages

    st = crossbar_stages(3, "4x2")
    assert len(st) == 1 and st[0].bits == (1, 2) and st[0].capacity == 1
    st = crossbar_stages(4, "2x2x4", (1, 2, 1))
    assert [s.bits for s in st] == [(3,), (2,)] and [s.capacity for s in st] == [1, 2]
    with pytest.raises(InvalidRequest):
        crossbar_stages(3, "4x4")                 # 16 banks, not 8


def test_a_stage_that_cannot_carry_the_concurrency_is_refused_up_front():
    from flux_bankmap import crossbar_stages

    with pytest.raises(InvalidRequest):
        _req([1], 4, stages=crossbar_stages(3, "2x4"))      # 2 groups x capacity 1 < 4


def test_a_bank_level_solution_can_still_conflict_at_a_stage():
    """The fold z3 finds for the bank alone puts two accesses in one group."""
    from flux_bankmap import crossbar_stages

    plain = _req([1, 8, 16], 4)
    fold, _ = solve(plain)
    assert fold is not None and check(fold, plain).conflict_free
    staged = _req([1, 8, 16], 4, stages=crossbar_stages(3, "4x2"))
    v = check(fold, staged)
    assert not v.conflict_free
    assert v.worst.stage != "bank" and v.worst.worst_load > v.worst.capacity


def test_z3_honours_a_stage_with_capacity_above_one():
    """4 groups with two parallel links each: a stage-aware fold exists and is exhaustively true."""
    from flux_bankmap import crossbar_stages

    r = _req([1, 8, 16], 4, stages=crossbar_stages(3, "4x2", (2, 1)))
    m, trace = solve(r)
    assert m is not None, trace.outcome
    v = check(m, r)
    assert v.conflict_free
    assert any(s.stage != "bank" for s in v.per_stride), "the stage was actually checked"


def test_a_capacity_one_stage_tightens_the_pigeonhole():
    """Four groups of single links, strides 8 and 16 at N=4: five addresses must differ."""
    from flux_bankmap import crossbar_stages, find_impossibility

    r = _req([1, 8, 16], 4, stages=crossbar_stages(3, "4x2"))
    w = find_impossibility(r)
    assert w is not None and len(w.addresses) == 5 and w.banks == 4
    assert find_impossibility(_req([1, 8, 16], 4)) is None, "no proof at the bank level alone"


def test_stage_verdicts_name_the_stage_and_the_load():
    from flux_bankmap import crossbar_stages

    r = _req([8], 4, stages=crossbar_stages(3, "4x2"))
    v = check(Modulo(0), r)
    stage = [s for s in v.per_stride if s.stage != "bank"][0]
    assert stage.worst_load == 4 and stage.capacity == 1
    assert "stage 1" in v.summary(4) or "bank" in v.summary(4)


# ---- laned stages: a first stage built from several small crossbars (D363) -----------------
def test_lanes_describe_the_first_stage_of_a_split_crossbar():
    """7 4x4s feeding 4 7x8s over 32 banks: stage 1 routes on the top 2 bank bits, and its
    capacity binds within each 4x4's four lanes, not across the window."""
    from flux_bankmap import crossbar_stages

    st = crossbar_stages(5, "4x8", lanes=4)
    assert len(st) == 1 and st[0].bits == (3, 4) and st[0].lanes == 4
    assert st[0].chunks(16) == [(0, 1, 2, 3), (4, 5, 6, 7), (8, 9, 10, 11), (12, 13, 14, 15)]
    assert st[0].chunks(6) == [(0, 1, 2, 3), (4, 5)]
    assert "group of 4 consecutive lanes" in st[0].describe()


def test_a_laned_stage_is_validated_per_chunk_not_per_window():
    """Sixteen accesses through four groups is impossible for one crossbar seeing them all, and
    fine for four crossbars seeing four each."""
    from flux_bankmap import crossbar_stages

    with pytest.raises(InvalidRequest):
        _req([1], 16, banks=32, stages=crossbar_stages(5, "4x8"))
    _req([1], 16, banks=32, stages=crossbar_stages(5, "4x8", lanes=4))


def test_the_checker_counts_a_laned_stage_within_each_chunk():
    """Modulo over 32 banks at stride 8, N=8: banks 0,8,16,24,0,8,16,24 -> groups 0,1,2,3,0,1,2,3.
    One crossbar seeing all eight loads every group twice; two 4x4s seeing four each never do."""
    from flux_bankmap import crossbar_stages

    whole = _req([8], 8, banks=32, stages=crossbar_stages(5, "4x8", (2, 1)))
    laned = _req([8], 8, banks=32, stages=crossbar_stages(5, "4x8", lanes=4))
    v_whole = check(Modulo(0), whole)
    v_laned = check(Modulo(0), laned)
    st_whole = [s for s in v_whole.per_stride if s.stage != "bank"][0]
    st_laned = [s for s in v_laned.per_stride if s.stage != "bank"][0]
    assert st_whole.worst_load == 2 and st_whole.conflict_free
    assert st_laned.worst_load == 1 and st_laned.conflict_free


def test_z3_and_the_checker_agree_on_a_laned_stage():
    """Strides 1 and 3: a stride-1 chunk forces the group to be a 4-periodic function of the
    address, and a stride-3 chunk then visits residues a, a+3, a+2, a+1 -- distinct."""
    from flux_bankmap import crossbar_stages

    r = _req([1, 3], 8, banks=32, stages=crossbar_stages(5, "4x8", lanes=4))
    m, trace = solve(r)
    assert m is not None, trace.outcome
    assert check(m, r).conflict_free


def test_a_laned_stage_has_its_own_must_differ_set_and_its_own_pigeonhole():
    """Strides 1 and 2 through 4x4s into 4 groups: a stride-1 chunk makes the group 4-periodic,
    a stride-2 chunk then puts a and a+4 in one 4x4. As a clique: differences {1,2,3} u {2,4,6},
    and 0..4 are five addresses that must pairwise differ in group, with four groups."""
    from flux_bankmap import crossbar_stages, find_impossibility

    r = _req([1, 2], 8, banks=32, stages=crossbar_stages(5, "4x8", lanes=4))
    w = find_impossibility(r)
    assert w is not None and w.banks == 4 and w.proof == "clique"
    assert list(w.addresses) == [0, 1, 2, 3, 4] and "stage 1" in w.resource
    assert find_impossibility(_req([1, 2], 8, banks=32)) is None, "the bank alone is fine"
    assert find_impossibility(_req([1, 3], 8, banks=32,
                                   stages=crossbar_stages(5, "4x8", lanes=4))) is None


def test_the_colouring_proof_agrees_with_the_clique_proof():
    from flux_bankmap.impossible import uncolourable_window

    assert uncolourable_window({1, 2, 3, 4, 6}, 4, 64) is not None
    assert uncolourable_window({1, 3}, 2, 64) is None, "odd differences: two colours suffice"


def test_two_identical_bank_bits_are_not_a_fold():
    """At N=1 anything is conflict-free, and the first partial answer for a proved-impossible
    request came back with b1 = b2 = a10: sixteen banks wearing thirty-two names."""
    r = _req([1, 2], 1, banks=32)
    m, trace = solve(r)
    assert m is not None, trace.outcome
    assert len(set(m.taps)) == len(m.taps)


def test_pairwise_stride_compatibility_is_proved_not_inferred():
    """Powers of two through 4-lane crossbars into 4 groups: each alone, never two."""
    from flux_bankmap import crossbar_stages
    from flux_bankmap.flow import _stride_compatibility

    r = _req([1, 2, 16, 64], 4, banks=32, stages=crossbar_stages(5, "4x8", lanes=4))
    lines = _stride_compatibility(r, lambda _m: None)
    assert lines and "NO two of them together" in lines[0]
    assert _stride_compatibility(_req([1, 3], 4, banks=32,
                                      stages=crossbar_stages(5, "4x8", lanes=4)),
                                 lambda _m: None) == []


# ---- topologies: every interconnect reduces to stages (D364) --------------------------------
def test_named_topologies_reduce_to_stages():
    from flux_bankmap.topology import parse

    assert parse("crossbar", 32).stages == ()
    st = parse("staged:4x8", 32, lanes=4).stages
    assert len(st) == 1 and st[0].bits == (3, 4) and st[0].lanes == 4
    om = parse("omega", 8).stages
    assert [s.bits for s in om] == [(2,), (1, 2)]
    assert [(s.lanes, s.lane_key) for s in om] == [(4, "mod"), (2, "mod")]
    bf = parse("butterfly", 8).stages
    assert [s.bits for s in bf] == [(0,), (0, 1)]
    assert [(s.lanes, s.lane_key) for s in bf] == [(2, "chunk"), (4, "chunk")]
    cl = parse("clos:4,4,8", 32)
    assert [s.bits for s in cl.stages] == [(), (2, 3, 4)] and "non-blocking" in cl.notes[0]
    assert "blocking:" in parse("clos:4,2,8", 32).notes[0]
    assert parse("benes", 32).stages == () and "non-blocking" in parse("benes", 32).notes[0]
    with pytest.raises(InvalidRequest):
        parse("clos:4,4,4", 32)                       # 16 outputs, not 32
    with pytest.raises(InvalidRequest):
        parse("hypercube", 32)


def test_omega_lanes_group_by_residue_and_pair_offsets_follow():
    """After stage j of an 8-input omega, sources that agree modulo 2^(3-j) share a link."""
    from flux_bankmap import Stage
    from flux_bankmap.topology import omega

    st = omega(8).stages[0]                            # j = 1: lanes agree mod 4
    assert st.groups(8) == [(0, 4), (1, 5), (2, 6), (3, 7)]
    assert st.pair_offsets(8) == {4}
    chunk = Stage(bits=(3, 4), lanes=4)
    assert chunk.pair_offsets(16) == {1, 2, 3}


def test_a_stage_with_no_bank_bits_is_a_pure_load_bound():
    """A Clos ingress switch passes m of its n lanes whatever the mapping says."""
    from flux_bankmap.topology import clos

    blocking = clos(32, 4, 2, 8)
    with pytest.raises(InvalidRequest):
        _req([1], 4, banks=32, stages=blocking.stages)          # 4 lanes through 2 links
    fine = _req([1], 8, banks=32, stages=clos(32, 4, 4, 8).stages)
    assert check(Modulo(0), fine).conflict_free


def test_omega_blocks_a_permutation_the_bank_level_allows():
    """Bit-reversal over 8 banks at stride 1, N=8 is a bank-level permutation; through an omega
    it collides at stage 1 (sources 0 and 4 both carry a bank whose top bit is 0 on links keyed
    by the same low source bits). The identity passes: an omega routes it link-disjointly."""
    from flux_bankmap import XorFold
    from flux_bankmap.topology import omega

    net = _req([1], 8, banks=8, stages=omega(8).stages)
    reversal = XorFold(taps=((2,), (1,), (0,)))
    assert check(reversal, _req([1], 8, banks=8)).conflict_free, "a permutation at the banks"
    v = check(reversal, net)
    assert not v.conflict_free and v.worst.stage.startswith("omega stage 1")
    assert check(Modulo(0), net).conflict_free
    m, trace = solve(net)
    assert m is not None, trace.outcome
    assert check(m, net).conflict_free


def test_explicit_stages_do_not_inherit_the_crossbar_note(tmp_path):
    """`--stage` alone once printed "a full crossbar adds no conflict point" beside the stage
    that is one. The node, exercised end to end with the model and solver off."""
    pytest.importorskip("chia.base.ChiaFunction")
    from flux_chia_nodes.bankmap_dse_loop import flux_bankmap_dse_loop

    out = flux_bankmap_dse_loop([1], 2, banks=8, z3_seconds=2, llm_round=0,
                                db_path=str(tmp_path / "b.db"),
                                stages=[{"bits": [1, 2], "capacity": 1, "lanes": 2,
                                         "lane_key": "mod"}])
    assert out["request"]["topology"] == "explicit stages"
    assert not any("no conflict point" in n for n in out["request"]["notes"])
    assert out["request"]["stages"] and "modulo 2" in out["request"]["stages"][0]


# ---- free lane assignment: the wiring is a decision variable (D372) -------------------------
def test_a_free_stage_is_unconstrained_until_solved_then_concrete():
    from flux_bankmap import Stage

    free = Stage(bits=(3, 4), capacity=1, lanes=4, lane_key="free", blocks=7)
    assert free.groups(8) == [(k,) for k in range(8)], "no pair constrained before solving"
    assert "FREE" in free.describe()
    wired = Stage(bits=(3, 4), capacity=1, lanes=4, lane_key="free", blocks=7,
                  partition=((0, 4), (1, 5), (2, 6), (3, 7)))
    assert wired.groups(8) == [(0, 4), (1, 5), (2, 6), (3, 7)]
    assert wired.pair_offsets(8) == {4}
    with pytest.raises(InvalidRequest):
        _req([1], 8, banks=32, stages=(Stage(bits=(3, 4), lanes=4, lane_key="free"),))
    with pytest.raises(InvalidRequest):
        _req([1], 30, banks=32,
             stages=(Stage(bits=(3, 4), lanes=4, lane_key="free", blocks=7),))


def test_the_solver_chooses_a_wiring_the_chunk_proof_forbids():
    """Strides {1, 2} through 4-input crossbars into 4 groups: consecutive wiring is proved
    impossible for ANY mapping (D363); with the wiring free, the solver finds an assignment
    AND a fold together, and the exhaustive checker confirms the pair on the wiring it chose."""
    from dataclasses import replace

    from flux_bankmap import Stage, check, crossbar_stages, find_impossibility

    chunked = _req([1, 2], 8, banks=32, stages=crossbar_stages(5, "4x8", lanes=4))
    assert find_impossibility(chunked) is not None

    free = _req([1, 2], 8, banks=32,
                stages=(Stage(bits=(3, 4), capacity=1, lanes=4, lane_key="free", blocks=7),))
    m, trace = solve(free)
    assert m is not None, trace.outcome
    assert trace.partition is not None
    sizes = sorted(len(b) for b in trace.partition)
    assert sum(sizes) == 8 and max(sizes) <= 4 and len(sizes) <= 7
    wired = replace(free, stages=tuple(
        replace(st, partition=trace.partition) for st in free.stages))
    assert check(m, wired).conflict_free


def test_joint_unsat_fixes_the_interleave_so_the_model_still_runs(monkeypatch):
    """D372: "no wiring rescues the linear family" must not silence the non-linear round.
    The flow fixes the interleaved wiring by rule, says so, and judges proposals against it."""
    from flux_bankmap import Stage, flow
    from flux_bankmap.solve_z3 import SolveTrace

    monkeypatch.setattr(flow, "solve",
                        lambda req, **kw: (None, SolveTrace(outcome="unsat (stub)")))
    seen = {}

    def propose(req, **kw):
        seen["partition"] = req.stages[0].partition
        return []

    r = _req([1, 3], 8, banks=32, llm_round=2,
             stages=(Stage(bits=(3, 4), capacity=1, lanes=4, lane_key="free", blocks=7),))
    result = flow.run_study(r, propose=propose, log=lambda _m: None)
    assert seen["partition"] == ((0, 7), (1,), (2,), (3,), (4,), (5,), (6,))
    assert any("fixed by rule" in l for l in result.lessons)
    assert not result.conflict_free


# ---------- the rim: --db is real, feedback reaches the prompt (D402) ----------

def test_db_records_trials_and_reseeds_the_tried_list(tmp_path):
    from flux_bankmap import flow

    db = str(tmp_path / "bm.db")
    r = _req([1, 8, 16], 4, db=db, llm_round=2)

    def propose(req, **kw):
        # an always-refused non-answer, so the run records a model refusal
        return [(Modulo(1), "shifted modulo")]

    first = flow.run_study(r, propose=propose, log=lambda _m: None)
    assert first.refused                       # the proposal was checked and refused
    from flux_records import Records

    rec = Records(db, objective={
        "study": "bankmap", "strides": [1, 8, 16], "concurrent": 4,
        "banks": 8, "address_bits": 12, "topology": "", "stages": []})
    assert rec.resumed
    refusals = rec.refusals(rung="exhaustive")
    assert any("mod B" in c.get("describe", "") for c, _ in refusals)
    assert rec.conclusions(limit=1)            # the decision landed as INFERENCE

    # the resumed run's model round is told what already failed
    prompts = {}

    def propose2(req, *, tried, guidance=None, **kw):
        prompts["tried"] = list(tried)
        prompts["guidance"] = guidance
        return []

    from flux_feedback import scripted_channel

    flow.run_study(r, propose=propose2, log=lambda _m: None,
                   feedback=scripted_channel("avoid plain shifts"))
    assert any("mod B" in d for d, _ in prompts["tried"])    # past refusals seeded
    assert prompts["guidance"] and "HUMAN GUIDANCE" in prompts["guidance"]
    assert "avoid plain shifts" in prompts["guidance"]
