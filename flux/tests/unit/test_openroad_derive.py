"""Unit tests for the architecture->RTL bridge (D100): `flux_evaluator_openroad.derive.derive_design_spec`,
deterministic derivation, no LLM.
"""

from __future__ import annotations

import pytest
from flux_evaluator_openroad.derive import (DerivationError, derive_design_spec, derive_gemm_design,
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
    assert a.spec == b.spec  # reproducible
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


# --- The derived sequential design (D118) ---


def _arch_with(lanes: int) -> dict:
    return {**_ARCH, "id": f"test/arch{lanes}",
            "hierarchy": [_ARCH["hierarchy"][0],
                          {"level": "pe_array", "class": "compute", "attrs": {"dims": {"X": lanes}}}]}


@pytest.mark.parametrize("lanes,steps", [(1, 32), (4, 8), (8, 4), (16, 2), (32, 1), (64, 1)])
def test_the_cycle_count_is_derived_from_both_documents(lanes, steps):
    """Latency is `ceil(C / lanes)`: C from the workload, lanes from the architecture (D118)."""
    d = derive_sequential_design(_WORKLOAD, _arch_with(lanes))
    assert d.reduction_length == 32
    assert (d.lanes, d.steps, d.expected_cycles) == (lanes, steps, steps)


@pytest.mark.parametrize("lanes,steps,padded", [(5, 7, 35), (7, 5, 35), (48, 1, 48)])
def test_a_reduction_that_does_not_divide_is_zero_padded_not_truncated(lanes, steps, padded):
    """Padding is zeros, so the golden `acc` is the dot product of the real operands."""
    d = derive_sequential_design(_WORKLOAD, _arch_with(lanes))
    assert (d.steps, d.padded_length) == (steps, padded)
    v = d.top_spec["test_vectors"][0]
    for i in range(d.reduction_length, padded):
        assert v["inputs"][f"a{i}"] == 0 and v["inputs"][f"w{i}"] == 0
    assert v["expected"]["acc"] == sum(
        v["inputs"][f"a{i}"] * v["inputs"][f"w{i}"] for i in range(padded)
    )


def test_the_leaf_the_llm_sees_has_no_clock_and_no_handshake():
    """The generated leaf's spec carries no protocol; the composed top does (D117/D118)."""
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
    # With array ports the limit is the testbench, which drives every operand as a literal (D120).
    huge_reduction = {**_WORKLOAD,
                      "ops": [{**_WORKLOAD["ops"][0], "bounds": {"B": 4, "C": 40_000, "K": 32}}]}
    with pytest.raises(DerivationError, match="testbench"):
        derive_sequential_design(huge_reduction, _arch_with(8))


# --- Operand representation (D120) ---


@pytest.mark.parametrize("C,lanes,expect_arrays", [
    (32, 8, False),     # 32 operands — flat, the shape D117/D118 measured
    (64, 8, False),     # exactly at the threshold — still flat
    (65, 8, True),      # 72 padded operands — arrays
    (1024, 8, True),
])
def test_the_operand_representation_switches_at_a_documented_threshold(C, lanes, expect_arrays):
    """Past the threshold operands become arrays, and the derived design reports which shape."""
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
    """The representation lives in the wrapper; the generated leaf's spec is the same either way."""
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


# --- The dataflow-matched GEMM design (D121) ---


def test_the_gemm_schedule_predicts_the_reference_evaluators_own_cycle_count():
    """The derived schedule predicts the 529 cycles `mac_array.sv` measures on mlp-gemm0 at 8 lanes."""
    d = derive_gemm_design(_WORKLOAD, _arch_with(8))

    assert d.shape == {"B": 4, "C": 32, "K": 32} and d.lanes == 8
    assert d.expected_cycles == 529          # 4*32*4 (run) + 4*4 (drain) + 1 (done)


@pytest.mark.parametrize("lanes,cycles", [(4, 4 * 32 * 8 + 4 * 8 + 1), (8, 529), (16, 4 * 32 * 2 + 4 * 2 + 1)])
def test_the_gemm_cycle_count_follows_the_architectures_width(lanes, cycles):
    assert derive_gemm_design(_WORKLOAD, _arch_with(lanes)).expected_cycles == cycles


def test_the_gemm_leaf_sees_no_schedule_and_no_memories():
    """The generated leaf is one broadcast MAC; the loop nest over the memories is the wrapper's."""
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
    """A ragged final K-group is masked, not floored (D130): candidates `evaluator/rtl` refuses."""
    d = derive_gemm_design(_WORKLOAD, _arch_with(lanes))

    assert d.shape["K"] == 32 and d.lanes == lanes
    assert d.expected_cycles == 4 * 32 * kg + 4 * kg + 1   # KG = ceil(K / lanes)
    # Masked w_mem reads and a guarded drain; the marker names o_mem because `if (__flux_dkg ...`
    # also matches the loop-counter test every wrapper emits.
    assert "< 32) ?" in d.wrapper_source
    assert "< 32) o_mem" in d.wrapper_source


def test_a_whole_k_group_design_is_unchanged_by_the_masking_support():
    """An even K keeps 529 cycles and emits no mask."""
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
    """The accumulator is sized to what the declared precision can produce (D202), so a wide
    dot product does not overflow.
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
    """Widths come from the declared worst case, not the drawn values, so the same shape always
    gets the same width."""
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
    # No 32-bit floor (D228): int8 gets exactly 8+8 product + 6 lane bits.
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
    """Control: every workload example declares I=8/W=8, in range even at 64 lanes."""
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
    """The empty salt reproduces the original seed; a salt varies only the vectors, with no
    overlap with the shown set (D223)."""
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

    # different salts, different vectors
    other = derive_design_spec(_WORKLOAD, _ARCH, n_vectors=8, vector_seed_salt="other")
    assert {str(v["inputs"]) for v in other.spec["test_vectors"]} != held_inputs
