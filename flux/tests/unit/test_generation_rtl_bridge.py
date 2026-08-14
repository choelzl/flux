"""Unit tests for the architecture→RTL bridge (docs/decisions.md D100):
`flux_generation.derive_design_spec` — pure, deterministic derivation logic, no LLM anywhere in
this file. The LLM-implementation half is covered by the live integration test.
"""

from __future__ import annotations

import pytest
from flux_generation import (DerivationError, derive_design_spec, derive_gemm_design,
                             derive_sequential_design)

_WORKLOAD = {
    "schema_version": "0.1.0",
    "id": "test/gemm0",
    "ops": [
        {"id": "gemm0", "kind": "einsum", "expr": "B C, C K -> B K",
         "bounds": {"B": 4, "C": 32, "K": 32}, "precision": {"I": 8, "W": 8, "O": 16, "O_final": 8}},
    ],
}

_ARCH = {
    "schema_version": "0.1.0",
    "id": "test/arch8",
    "hierarchy": [
        {"level": "gbuf", "class": "memory", "attrs": {"size_kb": 512}},
        {"level": "pe_array", "class": "compute", "attrs": {"dims": {"X": 8}}},
    ],
}


def test_ports_come_from_the_architectures_own_compute_width():
    derived = derive_design_spec(_WORKLOAD, _ARCH)
    assert derived.lanes == 8
    names = [p["name"] for p in derived.spec["ports"]]
    assert names == [f"a{i}" for i in range(8)] + [f"w{i}" for i in range(8)] + ["acc"]
    assert derived.spec["module_name"] == "DerivedMac8"


def test_golden_vectors_are_a_real_dot_product():
    derived = derive_design_spec(_WORKLOAD, _ARCH, n_vectors=3)
    assert len(derived.spec["test_vectors"]) == 3
    for v in derived.spec["test_vectors"]:
        expected = sum(v["inputs"][f"a{i}"] * v["inputs"][f"w{i}"] for i in range(8))
        assert v["expected"]["acc"] == expected
        # int8 precision honored on every input
        assert all(-128 <= v["inputs"][f"a{i}"] <= 127 for i in range(8))
        assert all(-128 <= v["inputs"][f"w{i}"] <= 127 for i in range(8))


def test_derivation_is_deterministic_per_candidate_pair():
    a = derive_design_spec(_WORKLOAD, _ARCH)
    b = derive_design_spec(_WORKLOAD, _ARCH)
    assert a.spec == b.spec  # same candidate pair, identical golden vectors — reproducible
    wider = {**_ARCH, "id": "test/arch16",
             "hierarchy": [_ARCH["hierarchy"][0],
                           {"level": "pe_array", "class": "compute", "attrs": {"dims": {"X": 16}}}]}
    c = derive_design_spec(_WORKLOAD, wider)
    assert c.lanes == 16
    assert c.spec != a.spec  # different candidate, different spec


def test_out_of_scope_pairs_fail_before_any_llm_spend():
    with pytest.raises(DerivationError, match="not bridgeable"):
        derive_design_spec(_WORKLOAD, {"id": "no-compute", "hierarchy": []})
    two_ops = {**_WORKLOAD, "ops": _WORKLOAD["ops"] * 2}
    with pytest.raises(DerivationError, match="2 einsum ops"):
        derive_design_spec(two_ops, _ARCH)
    huge = {**_ARCH, "hierarchy": [{"level": "pe", "class": "compute", "attrs": {"dims": {"X": 1024}}}]}
    with pytest.raises(DerivationError, match="sanity cap"):
        derive_design_spec(_WORKLOAD, huge)
    with pytest.raises(DerivationError, match="n_vectors"):
        derive_design_spec(_WORKLOAD, _ARCH, n_vectors=0)


# --- The derived *sequential* design (docs/decisions.md D118) ---


def _arch_with(lanes: int) -> dict:
    return {**_ARCH, "id": f"test/arch{lanes}",
            "hierarchy": [_ARCH["hierarchy"][0],
                          {"level": "pe_array", "class": "compute", "attrs": {"dims": {"X": lanes}}}]}


@pytest.mark.parametrize("lanes,steps", [(1, 32), (4, 8), (8, 4), (16, 2), (32, 1), (64, 1)])
def test_the_cycle_count_is_derived_from_both_documents(lanes, steps):
    """The point of D118: latency is `ceil(C / lanes)` — the workload supplies C, the architecture
    supplies the width. Neither document alone determines it, and no caller passes it in."""
    d = derive_sequential_design(_WORKLOAD, _arch_with(lanes))
    assert d.reduction_length == 32
    assert (d.lanes, d.steps, d.expected_cycles) == (lanes, steps, steps)


@pytest.mark.parametrize("lanes,steps,padded", [(5, 7, 35), (7, 5, 35), (48, 1, 48)])
def test_a_reduction_that_does_not_divide_is_zero_padded_not_truncated(lanes, steps, padded):
    """Zero padding keeps the leaf one fixed-width module. The golden `acc` must still be the dot
    product of the *real* operands — so the padding has to actually be zeros, not leftover data."""
    d = derive_sequential_design(_WORKLOAD, _arch_with(lanes))
    assert (d.steps, d.padded_length) == (steps, padded)
    v = d.top_spec["test_vectors"][0]
    for i in range(d.reduction_length, padded):
        assert v["inputs"][f"a{i}"] == 0 and v["inputs"][f"w{i}"] == 0
    assert v["expected"]["acc"] == sum(
        v["inputs"][f"a{i}"] * v["inputs"][f"w{i}"] for i in range(padded)
    )


def test_the_leaf_the_llm_sees_has_no_clock_and_no_handshake():
    """The whole D117/D118 split in one assertion: the generated half cannot get the protocol
    wrong because the protocol never appears in its spec."""
    d = derive_sequential_design(_WORKLOAD, _arch_with(8))
    names = {p["name"] for p in d.leaf_spec["ports"]}
    assert names.isdisjoint({"clk", "rst_n", "start", "done"})
    assert names == {f"a{j}" for j in range(8)} | {f"w{j}" for j in range(8)} | {"acc_in", "acc_out"}
    assert d.leaf_spec.get("is_clocked") is None
    # ...while the composed top *is* the clocked, latency-measuring half.
    assert d.top_spec["is_clocked"] is True and d.top_spec["measures_latency"] is True


def test_the_wrapper_is_emitted_here_not_generated():
    d = derive_sequential_design(_WORKLOAD, _arch_with(8))
    assert d.wrapper_source.startswith(f"module {d.top_module_name}")
    assert f"{d.leaf_module_name} __flux_leaf" in d.wrapper_source
    assert "always_ff @(posedge clk or negedge rst_n)" in d.wrapper_source


def test_sequential_derivation_is_deterministic_per_candidate_pair():
    a = derive_sequential_design(_WORKLOAD, _ARCH)
    assert a.to_dict() == derive_sequential_design(_WORKLOAD, _ARCH).to_dict()
    assert derive_sequential_design(_WORKLOAD, _arch_with(16)).top_spec != a.top_spec


def test_out_of_scope_sequential_pairs_fail_before_any_llm_spend():
    with pytest.raises(DerivationError, match="not bridgeable"):
        derive_sequential_design(_WORKLOAD, {"id": "no-compute", "hierarchy": []})
    with pytest.raises(DerivationError, match="2 einsum ops"):
        derive_sequential_design({**_WORKLOAD, "ops": _WORKLOAD["ops"] * 2}, _ARCH)
    dyn = {**_WORKLOAD, "ops": [{**_WORKLOAD["ops"][0], "bounds": {"B": 4, "C": {"dyn": [1, 32]}, "K": 32}}]}
    with pytest.raises(DerivationError, match="not bridgeable"):
        derive_sequential_design(dyn, _ARCH)
    # UPDATED CONTRACT (docs/decisions.md D120). This previously asserted that a 4096-long
    # reduction was out of scope, because 4096 flat operand ports is not a module interface. With
    # array-valued ports that size is expressible, and the real limit moved to the *testbench*,
    # which still drives every operand as a literal. So the rejection is still here — it just
    # fires an order of magnitude later, and for a different, honestly-named reason.
    huge_reduction = {**_WORKLOAD,
                      "ops": [{**_WORKLOAD["ops"][0], "bounds": {"B": 4, "C": 40_000, "K": 32}}]}
    with pytest.raises(DerivationError, match="testbench"):
        derive_sequential_design(huge_reduction, _arch_with(8))


# --- Operand representation (docs/decisions.md D120) ---


@pytest.mark.parametrize("C,lanes,expect_arrays", [
    (32, 8, False),     # 32 operands — flat, the shape D117/D118 measured
    (64, 8, False),     # exactly at the threshold — still flat
    (65, 8, True),      # 72 padded operands — arrays
    (1024, 8, True),
])
def test_the_operand_representation_switches_at_a_documented_threshold(C, lanes, expect_arrays):
    """A reduction long enough to be realistic cannot be 2N+5 flat ports. The switch is reported,
    not inferred — a caller compiling the wrapper needs to know which shape it got."""
    wl = {**_WORKLOAD, "ops": [{**_WORKLOAD["ops"][0], "bounds": {"B": 4, "C": C, "K": 32}}]}
    d = derive_sequential_design(wl, _arch_with(lanes))

    assert d.array_operands is expect_arrays
    assert d.to_dict()["array_operands"] is expect_arrays
    if expect_arrays:
        assert [p["name"] for p in d.top_spec["ports"]] == ["a", "w", "acc"]
        assert d.top_spec["ports"][0]["depth"] == d.padded_length
        assert f"a [0:{d.padded_length - 1}]" in d.wrapper_source
    else:
        assert len(d.top_spec["ports"]) == 2 * d.padded_length + 1


def test_the_leaf_is_identical_whichever_representation_the_top_uses():
    """The point of doing this in the wrapper: the generated half cannot tell the difference, so
    switching representation can never invalidate a generation result."""
    short = {**_WORKLOAD, "ops": [{**_WORKLOAD["ops"][0], "bounds": {"B": 4, "C": 32, "K": 32}}]}
    long = {**_WORKLOAD, "ops": [{**_WORKLOAD["ops"][0], "bounds": {"B": 4, "C": 1024, "K": 32}}]}

    flat, arrays = derive_sequential_design(short, _ARCH), derive_sequential_design(long, _ARCH)

    assert flat.array_operands is False and arrays.array_operands is True
    assert flat.leaf_spec == arrays.leaf_spec


def test_an_array_vector_carries_one_list_per_operand_not_thousands_of_keys():
    wl = {**_WORKLOAD, "ops": [{**_WORKLOAD["ops"][0], "bounds": {"B": 4, "C": 1024, "K": 32}}]}
    d = derive_sequential_design(wl, _arch_with(8))
    v = d.top_spec["test_vectors"][0]

    assert set(v["inputs"]) == {"a", "w"}
    assert len(v["inputs"]["a"]) == d.padded_length == 1024
    assert v["expected"]["acc"] == sum(x * y for x, y in zip(v["inputs"]["a"], v["inputs"]["w"]))


# --- The dataflow-matched GEMM design (docs/decisions.md D121) ---


def test_the_gemm_schedule_predicts_the_reference_evaluators_own_cycle_count():
    """The number that makes this worth building: `mac_array.sv` measured on mlp-gemm0 at 8 lanes
    is 529 cycles (real Verilator, `evaluators/rtl`). The derived schedule has to predict exactly
    that, or the two are still not comparable."""
    d = derive_gemm_design(_WORKLOAD, _arch_with(8))

    assert d.shape == {"B": 4, "C": 32, "K": 32} and d.lanes == 8
    assert d.expected_cycles == 529          # 4*32*4 (run) + 4*4 (drain) + 1 (done)


@pytest.mark.parametrize("lanes,cycles", [(4, 4 * 32 * 8 + 4 * 8 + 1), (8, 529), (16, 4 * 32 * 2 + 4 * 2 + 1)])
def test_the_gemm_cycle_count_follows_the_architectures_width(lanes, cycles):
    assert derive_gemm_design(_WORKLOAD, _arch_with(lanes)).expected_cycles == cycles


def test_the_gemm_leaf_sees_no_schedule_and_no_memories():
    """Same split as D117/D118, at a bigger scale: the LLM writes one broadcast MAC, not a loop
    nest over three memories."""
    d = derive_gemm_design(_WORKLOAD, _arch_with(8))
    names = {p["name"] for p in d.leaf_spec["ports"]}

    assert names.isdisjoint({"clk", "rst_n", "start", "done", "i_mem", "w_mem", "o_mem"})
    assert names == {"a"} | {f"w{j}" for j in range(8)} | {
        f"acc_in{j}" for j in range(8)} | {f"acc_out{j}" for j in range(8)}
    # ...while the composed top is the clocked, latency-measuring, memory-shaped half.
    assert [p["name"] for p in d.top_spec["ports"]] == ["i_mem", "w_mem", "o_mem"]
    assert d.top_spec["ports"][0]["dims"] == [4, 32]
    assert d.top_spec["ports"][2]["dims"] == [4, 32]


def test_the_gemm_golden_output_is_the_real_matrix_product():
    d = derive_gemm_design(_WORKLOAD, _arch_with(8))
    v = d.top_spec["test_vectors"][0]
    i_mem, w_mem, expected = v["inputs"]["i_mem"], v["inputs"]["w_mem"], v["expected"]["o_mem"]

    assert expected == [[sum(i_mem[b][c] * w_mem[c][k] for c in range(32)) for k in range(32)]
                        for b in range(4)]


@pytest.mark.parametrize("lanes,kg", [(7, 5), (12, 3), (48, 1)])
def test_a_partial_k_group_is_supported_by_masking(lanes, kg):
    """UPDATED CONTRACT (docs/decisions.md D130). This asserted that a ragged final K-group was
    rejected, because `mac_array.sv`'s schedule is whole K-groups and silently flooring it would
    produce a cycle count for a design nobody described. Flooring is still wrong — but *masking*
    the final group is a real design, and it is the case worth supporting: `evaluators/rtl`
    refuses these candidates outright, so they have no RTL ground truth at all. Extending the
    reference frontier is the one thing a generated design can contribute that the hand-written
    reference cannot.
    """
    d = derive_gemm_design(_WORKLOAD, _arch_with(lanes))

    assert d.shape["K"] == 32 and d.lanes == lanes
    assert d.expected_cycles == 4 * 32 * kg + 4 * kg + 1   # KG = ceil(K / lanes)
    # Masked rather than reading past the end of w_mem, with the drain guarded to match. The
    # drain marker has to name o_mem: `if (__flux_dkg ...` also matches the loop-counter test
    # every wrapper emits, ragged or not — a first version of this assertion did exactly that.
    assert "< 32) ?" in d.wrapper_source
    assert "< 32) o_mem" in d.wrapper_source


def test_a_whole_k_group_design_is_unchanged_by_the_masking_support():
    """The 529 that makes D121 meaningful must not move because ragged support was added."""
    d = derive_gemm_design(_WORKLOAD, _arch_with(8))

    assert d.expected_cycles == 529
    assert "< 32) ?" not in d.wrapper_source       # no operand mask emitted when none is needed
    assert "< 32) o_mem" not in d.wrapper_source   # ...and no guarded drain


def test_gemm_derivation_is_deterministic_and_out_of_scope_pairs_fail_early():
    assert derive_gemm_design(_WORKLOAD, _ARCH).to_dict() == derive_gemm_design(_WORKLOAD, _ARCH).to_dict()
    with pytest.raises(DerivationError, match="not bridgeable"):
        derive_gemm_design(_WORKLOAD, {"id": "no-compute", "hierarchy": []})
    big = {**_WORKLOAD, "ops": [{**_WORKLOAD["ops"][0], "bounds": {"B": 64, "C": 256, "K": 256}}]}
    with pytest.raises(DerivationError, match="testbench"):
        derive_gemm_design(big, _arch_with(8))


def test_a_sixteen_bit_workload_gets_a_wider_accumulator_instead_of_a_refusal():
    """docs/decisions.md D193 refused these outright: the ports were fixed at 32 bits, a 64-lane
    16-bit dot product overflows that, the RTL wrapped where the Python golden reference did not,
    and a *correct* MAC failed its own verification. Ports carry a width now (D202), so the
    accumulator is sized to what the declared precision can actually produce.
    """
    workload = {
        "schema_version": "0.1.0", "id": "w",
        "ops": [{"id": "op0", "kind": "einsum", "expr": "B K, K C -> B C",
                 "bounds": {"B": 1, "K": 32, "C": 1}, "precision": {"I": 16, "W": 16}}],
    }
    arch = {
        "schema_version": "0.1.0", "id": "a",
        "hierarchy": [{"level": "pe_array", "class": "compute", "attrs": {"dims": {"X": 32}}}],
    }

    derived = derive_design_spec(workload, arch)

    acc = next(p for p in derived.spec["ports"] if p["name"] == "acc")
    assert acc["bits"] == 37, "16x16 products over 32 lanes need 32 + 5 bits"
    low, high = -(1 << (acc["bits"] - 1)), (1 << (acc["bits"] - 1)) - 1
    for vector in derived.spec["test_vectors"]:
        assert low <= vector["expected"]["acc"] <= high


def test_the_accumulator_is_sized_from_the_worst_case_not_the_drawn_vectors():
    """The golden data is randomly seeded from the candidate hashes, so a width computed from the
    values actually drawn would differ between two derivations of the same shape."""
    def _derive(lanes, bits):
        workload = {
            "schema_version": "0.1.0", "id": f"w{lanes}",
            "ops": [{"id": "op0", "kind": "einsum", "expr": "B K, K C -> B C",
                     "bounds": {"B": 1, "K": lanes, "C": 1}, "precision": {"I": bits, "W": bits}}],
        }
        arch = {
            "schema_version": "0.1.0", "id": f"a{lanes}",
            "hierarchy": [{"level": "pe_array", "class": "compute", "attrs": {"dims": {"X": lanes}}}],
        }
        acc = next(p for p in derive_design_spec(workload, arch).spec["ports"] if p["name"] == "acc")
        return acc["bits"]

    assert _derive(4, 16) == 34 and _derive(32, 16) == 37 and _derive(64, 16) == 38
    # docs/decisions.md D228: the 32-bit floor is gone — an int8 workload gets the exact
    # accumulator its worst case needs (8+8 product + 6 lane bits), so the physical rung places
    # int8-sized arithmetic instead of a 32-bit carrier's.
    assert _derive(64, 8) == 22


def test_a_precision_past_the_port_limit_is_still_refused():
    """Widths are bounded at 64 by `Port.bits`; 32-bit operands over 64 lanes need 70 and must be
    refused rather than silently truncated."""
    workload = {
        "schema_version": "0.1.0", "id": "w",
        "ops": [{"id": "op0", "kind": "einsum", "expr": "B K, K C -> B C",
                 "bounds": {"B": 1, "K": 64, "C": 1}, "precision": {"I": 32, "W": 32}}],
    }
    arch = {
        "schema_version": "0.1.0", "id": "a",
        "hierarchy": [{"level": "pe_array", "class": "compute", "attrs": {"dims": {"X": 64}}}],
    }

    with pytest.raises(DerivationError, match="70-bit accumulator"):
        derive_design_spec(workload, arch)


def test_the_eight_bit_configurations_this_repo_actually_uses_still_derive():
    """Control, and the reason the guard checks the worst case rather than the drawn vectors:
    every workload example here declares I=8/W=8, where even 64 lanes stays far inside range. A
    data-dependent check would accept a spec for one candidate pair and reject it for the next.
    """
    for lanes in (4, 8, 64):
        workload = {
            "schema_version": "0.1.0", "id": "w",
            "ops": [{"id": "op0", "kind": "einsum", "expr": "B K, K C -> B C",
                     "bounds": {"B": 1, "K": lanes, "C": 1}, "precision": {"I": 8, "W": 8}}],
        }
        arch = {
            "schema_version": "0.1.0", "id": "a",
            "hierarchy": [{"level": "pe_array", "class": "compute", "attrs": {"dims": {"X": lanes}}}],
        }

        derived = derive_design_spec(workload, arch)

        assert derived.lanes == lanes
        for vector in derived.spec["test_vectors"]:
            assert -(2**31) <= vector["expected"]["acc"] <= 2**31 - 1


def test_holdout_salt_changes_vectors_only_and_the_default_stays_byte_identical():
    """docs/decisions.md D223: the empty salt must reproduce the historical seed exactly (every
    pre-existing derived spec unchanged), and a salt must vary ONLY the vectors — same ports,
    same module, same behavior text — with zero overlap against the shown set."""
    base = derive_design_spec(_WORKLOAD, _ARCH)
    again = derive_design_spec(_WORKLOAD, _ARCH)
    assert base.spec == again.spec

    holdout = derive_design_spec(_WORKLOAD, _ARCH, n_vectors=8, vector_seed_salt="holdout")
    assert holdout.spec["ports"] == base.spec["ports"]
    assert holdout.spec["module_name"] == base.spec["module_name"]
    assert holdout.spec["behavior"] == base.spec["behavior"]
    assert holdout.spec["id"] != base.spec["id"]

    shown_inputs = {str(v["inputs"]) for v in base.spec["test_vectors"]}
    held_inputs = {str(v["inputs"]) for v in holdout.spec["test_vectors"]}
    assert len(held_inputs) == 8 and not (shown_inputs & held_inputs)

    # different salts, different vectors — the salt is load-bearing, not decorative
    other = derive_design_spec(_WORKLOAD, _ARCH, n_vectors=8, vector_seed_salt="other")
    assert {str(v["inputs"]) for v in other.spec["test_vectors"]} != held_inputs
