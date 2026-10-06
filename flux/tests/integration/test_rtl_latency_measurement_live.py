"""Real Verilator validation of the harness's latency-measuring mode (D115).

The instrument is validated against hand-written designs whose cycle counts are known by
construction, before any generator is measured with it.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("verilator") is None, reason="verilator not on PATH (needs the `nix develop` shell)"
)


def _spec(module_name: str, lanes: int, vectors: list[dict]) -> dict:
    return {
        "schema_version": "0.1.0",
        "id": f"latency/{module_name}",
        "module_name": module_name,
        "is_clocked": True,
        "measures_latency": True,
        "ports": (
            [{"name": f"a{i}", "dir": "in", "dtype": "int"} for i in range(lanes)]
            + [{"name": f"w{i}", "dir": "in", "dtype": "int"} for i in range(lanes)]
            + [{"name": "acc", "dir": "out", "dtype": "int"}]
        ),
        "behavior": f"{lanes}-lane sequential MAC: one lane per cycle, then assert done",
        "test_vectors": vectors,
    }


def _sequential_mac(module_name: str, lanes: int) -> str:
    """One lane per clock, so total latency is exactly `lanes` cycles by construction."""
    a_ports = ", ".join(f"input logic signed [31:0] a{i}" for i in range(lanes))
    w_ports = ", ".join(f"input logic signed [31:0] w{i}" for i in range(lanes))
    muxes = "\n".join(
        f"      {'else ' if i else ''}if (idx == {i}) acc <= acc + a{i} * w{i};"
        for i in range(lanes)
    )
    return f"""
module {module_name} (
  input logic clk,
  input logic rst_n,
  input logic start,
  output logic done,
  {a_ports},
  {w_ports},
  output logic signed [31:0] acc
);
  logic [7:0] idx;
  logic busy;
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      acc <= 0; idx <= 0; busy <= 0; done <= 0;
    end else if (start) begin
      acc <= 0; idx <= 0; busy <= 1; done <= 0;
    end else if (busy) begin
{muxes}
      idx <= idx + 1;
      if (idx == {lanes - 1}) begin busy <= 0; done <= 1; end
    end else begin
      done <= 0;
    end
  end
endmodule
"""


@pytest.mark.parametrize("lanes", [2, 4, 8])
def test_measured_cycles_match_a_known_by_construction_design(lanes):
    """A one-lane-per-cycle design measures exactly `lanes` cycles (no off-by-one, start pulse not counted)."""
    from flux_codegen_rtl_harness import compile_and_run, design_spec_from_dict

    a = list(range(1, lanes + 1))
    w = [2] * lanes
    inputs = {f"a{i}": a[i] for i in range(lanes)} | {f"w{i}": w[i] for i in range(lanes)}
    expected = {"acc": sum(x * y for x, y in zip(a, w))}
    spec = design_spec_from_dict(_spec(f"SeqMac{lanes}", lanes, [{"inputs": inputs, "expected": expected}]))

    result = compile_and_run(_sequential_mac(f"SeqMac{lanes}", lanes), spec)

    assert result.compiled, result.compile_stderr
    assert result.all_passed, f"{result.failing_vector_lines}\n{result.stdout}"
    assert result.cycles_per_vector == (lanes,), (
        f"expected exactly {lanes} cycles (one lane per clock, known from the source); "
        f"measured {result.cycles_per_vector}"
    )
    assert result.total_cycles == lanes


def test_a_slower_design_measures_more_cycles_than_a_faster_one():
    """Two designs computing the same result at different rates measure different latencies."""
    from flux_codegen_rtl_harness import compile_and_run, design_spec_from_dict

    inputs = {"a0": 3, "a1": 4, "w0": 2, "w1": 5}
    expected = {"acc": 3 * 2 + 4 * 5}
    vectors = [{"inputs": inputs, "expected": expected}]

    fast = compile_and_run(_sequential_mac("SeqMac2", 2), design_spec_from_dict(_spec("SeqMac2", 2, vectors)))

    # same arithmetic, two cycles per lane
    slow_src = """
module SlowMac2 (
  input logic clk, input logic rst_n, input logic start, output logic done,
  input logic signed [31:0] a0, input logic signed [31:0] a1,
  input logic signed [31:0] w0, input logic signed [31:0] w1,
  output logic signed [31:0] acc
);
  logic [7:0] step;
  logic busy;
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin acc <= 0; step <= 0; busy <= 0; done <= 0; end
    else if (start) begin acc <= 0; step <= 0; busy <= 1; done <= 0; end
    else if (busy) begin
      if (step == 1) acc <= acc + a0 * w0;
      else if (step == 3) acc <= acc + a1 * w1;
      step <= step + 1;
      if (step == 3) begin busy <= 0; done <= 1; end
    end else done <= 0;
  end
endmodule
"""
    slow = compile_and_run(slow_src, design_spec_from_dict(_spec("SlowMac2", 2, vectors)))

    assert fast.all_passed and slow.all_passed
    assert fast.cycles_per_vector == (2,)
    assert slow.cycles_per_vector == (4,)
    assert slow.total_cycles > fast.total_cycles


def test_a_design_that_never_finishes_fails_instead_of_hanging():
    """A DUT that never raises `done` is a bounded, reported failure, not an endless simulation."""
    from flux_codegen_rtl_harness import compile_and_run, design_spec_from_dict

    never_done = """
module NeverDone (
  input logic clk, input logic rst_n, input logic start, output logic done,
  input logic signed [31:0] a0, input logic signed [31:0] w0,
  output logic signed [31:0] acc
);
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin acc <= 0; done <= 0; end
    else begin acc <= a0 * w0; done <= 0; end  // computes, but never claims completion
  end
endmodule
"""
    spec = design_spec_from_dict({
        "schema_version": "0.1.0", "id": "latency/never", "module_name": "NeverDone",
        "is_clocked": True, "measures_latency": True,
        "ports": [{"name": "a0", "dir": "in", "dtype": "int"},
                  {"name": "w0", "dir": "in", "dtype": "int"},
                  {"name": "acc", "dir": "out", "dtype": "int"}],
        "behavior": "never asserts done",
        "test_vectors": [{"inputs": {"a0": 3, "w0": 4}, "expected": {"acc": 12}}],
    })
    result = compile_and_run(never_done, spec)

    assert result.compiled and result.ran          # it terminated
    assert not result.all_passed                   # and it failed
    assert any("never asserted done" in line for line in result.failing_vector_lines)


# --- the deterministic-wrapper split (D117) ---


def test_the_wrapper_alone_is_correct_against_a_hand_written_leaf():
    """With a known-correct leaf, a `lanes`-step schedule measures exactly `lanes` cycles and the right sum."""
    from flux_codegen_rtl_harness import (compile_and_run, design_spec_from_dict,
                                          generate_sequential_wrapper, sequential_spec)
    lanes, a, w = 4, [1, 2, 3, 4], [2, 2, 2, 2]
    hand_leaf = """
module MacStep (
  input logic signed [31:0] a, input logic signed [31:0] w,
  input logic signed [31:0] acc_in, output logic signed [31:0] acc_out
);
  assign acc_out = acc_in + a * w;
endmodule
"""
    res = compile_and_run(
        generate_sequential_wrapper("SeqMacTop", "MacStep", lanes),
        design_spec_from_dict(sequential_spec("SeqMacTop", lanes, a, w)),
        extra_sources={"MacStep": hand_leaf},
    )
    assert res.all_passed, f"{res.failing_vector_lines}\n{res.compile_stderr or ''}"
    assert res.cycles_per_vector == (lanes,)


@pytest.mark.parametrize("lanes", [1, 2, 8])
def test_measured_latency_equals_the_generated_schedule(lanes):
    """Latency is a property of the wrapper's schedule, not the leaf, so it is predictable from the schedule."""
    from flux_codegen_rtl_harness import (compile_and_run, design_spec_from_dict,
                                          generate_sequential_wrapper, sequential_spec)
    a = list(range(1, lanes + 1))
    w = [3] * lanes
    hand_leaf = """
module MacStep (
  input logic signed [31:0] a, input logic signed [31:0] w,
  input logic signed [31:0] acc_in, output logic signed [31:0] acc_out
);
  assign acc_out = acc_in + a * w;
endmodule
"""
    res = compile_and_run(
        generate_sequential_wrapper("SeqTop", "MacStep", lanes),
        design_spec_from_dict(sequential_spec("SeqTop", lanes, a, w)),
        extra_sources={"MacStep": hand_leaf},
    )
    assert res.all_passed
    assert res.cycles_per_vector == (lanes,)


def test_wrapper_rejects_degenerate_inputs():
    from flux_codegen_rtl_harness import InvalidSpecError, generate_sequential_wrapper
    with pytest.raises(InvalidSpecError, match="at least one step"):
        generate_sequential_wrapper("Top", "Leaf", 0)
    with pytest.raises(InvalidSpecError, match="duplicate module definition"):
        generate_sequential_wrapper("Same", "Same", 4)


# --- the schedule derived from an IR candidate pair (D118) ---


_D118_WORKLOAD = {
    "schema_version": "0.1.0", "id": "test/gemm0",
    "ops": [{"id": "gemm0", "kind": "einsum", "expr": "B C, C K -> B K",
             "bounds": {"B": 4, "C": 32, "K": 32},
             "precision": {"I": 8, "W": 8, "O": 16, "O_final": 8}}],
}


def _d118_arch(lanes: int) -> dict:
    return {"schema_version": "0.1.0", "id": f"test/arch{lanes}",
            "hierarchy": [{"level": "gbuf", "class": "memory", "attrs": {"size_kb": 512}},
                          {"level": "pe", "class": "compute", "attrs": {"dims": {"X": lanes}}}]}


def _hand_written_tile(module_name: str, lane_width: int) -> str:
    """A known-correct leaf whose interface comes from `leaf_operand_names`, the same source the
    wrapper and generation prompt use, so a failure is the derivation's or the wrapper's."""
    from flux_codegen_rtl_harness import leaf_operand_names

    a, w = leaf_operand_names(lane_width)
    ports = ", ".join(f"input logic signed [31:0] {n}" for n in a + w)
    terms = " + ".join(f"{x}*{y}" for x, y in zip(a, w))
    return f"""
module {module_name} ({ports},
  input logic signed [31:0] acc_in, output logic signed [31:0] acc_out);
  assign acc_out = acc_in + {terms};
endmodule
"""


@pytest.mark.parametrize("lanes,expected_cycles", [(4, 8), (8, 4), (16, 2), (5, 7)])
def test_a_derived_design_measures_the_latency_its_candidate_predicts(lanes, expected_cycles):
    """The composed design computes the right dot product in exactly the predicted cycles (D118);
    lanes=5 is the non-dividing case (32 operands, 7 zero-padded tiles)."""
    from flux_codegen_rtl_harness import compile_and_run, design_spec_from_dict
    from flux_evaluator_openroad.derive import derive_sequential_design

    d = derive_sequential_design(_D118_WORKLOAD, _d118_arch(lanes))
    assert d.expected_cycles == expected_cycles  # predicted before anything is built

    res = compile_and_run(
        d.wrapper_source,
        design_spec_from_dict(d.top_spec),
        extra_sources={d.leaf_module_name: _hand_written_tile(d.leaf_module_name, d.lanes)},
    )

    assert res.all_passed, f"{res.failing_vector_lines}\n{res.compile_stderr or ''}"
    assert res.cycles_per_vector == (expected_cycles,), (
        f"predicted {expected_cycles} cycles from (C={d.reduction_length}, lanes={lanes}); "
        f"measured {res.cycles_per_vector}"
    )


def test_a_wider_architecture_really_does_run_the_same_workload_in_fewer_cycles():
    """Latency responds to the candidate: same workload, two widths, the measured ratio matches."""
    from flux_codegen_rtl_harness import compile_and_run, design_spec_from_dict
    from flux_evaluator_openroad.derive import derive_sequential_design

    measured = {}
    for lanes in (4, 16):
        d = derive_sequential_design(_D118_WORKLOAD, _d118_arch(lanes))
        res = compile_and_run(
            d.wrapper_source, design_spec_from_dict(d.top_spec),
            extra_sources={d.leaf_module_name: _hand_written_tile(d.leaf_module_name, d.lanes)},
        )
        assert res.all_passed, res.failing_vector_lines
        measured[lanes] = res.total_cycles

    assert measured[4] == 8 and measured[16] == 2
    assert measured[4] == 4 * measured[16]   # 4x the lanes, a quarter of the cycles


# --- array-valued operand ports (D120) ---


@pytest.mark.parametrize("lane_width,steps", [(1, 4), (4, 8), (8, 16)])
def test_array_operands_compile_and_measure_exactly_like_flat_ones(lane_width, steps):
    """Array ports change only the top-level interface: same schedule, arithmetic and latency."""
    from flux_codegen_rtl_harness import (compile_and_run, design_spec_from_dict,
                                          generate_tiled_wrapper, sequential_spec)

    n = lane_width * steps
    a = [(i % 7) - 3 for i in range(n)]
    w = [(i % 5) - 2 for i in range(n)]
    res = compile_and_run(
        generate_tiled_wrapper("ArrTop", "ArrLeaf", lane_width=lane_width, steps=steps,
                               array_operands=True),
        design_spec_from_dict(sequential_spec("ArrTop", n, a, w, array_operands=True)),
        extra_sources={"ArrLeaf": _hand_written_tile("ArrLeaf", lane_width)},
    )

    assert res.all_passed, f"{res.failing_vector_lines}\n{res.compile_stderr or ''}"
    assert res.cycles_per_vector == (steps,)


def test_a_reduction_too_long_for_flat_ports_is_now_expressible_end_to_end():
    """A 512-long reduction is three array ports instead of 1029 flat ones, correct in its scheduled 64 cycles (D120)."""
    from flux_codegen_rtl_harness import compile_and_run, design_spec_from_dict
    from flux_evaluator_openroad.derive import derive_sequential_design

    wl = {**_D118_WORKLOAD,
          "ops": [{**_D118_WORKLOAD["ops"][0], "bounds": {"B": 4, "C": 512, "K": 32}}]}
    d = derive_sequential_design(wl, _d118_arch(8))

    assert d.array_operands and (d.steps, d.padded_length) == (64, 512)
    assert len(d.top_spec["ports"]) == 3  # a, w, acc — not 1029

    res = compile_and_run(
        d.wrapper_source, design_spec_from_dict(d.top_spec),
        extra_sources={d.leaf_module_name: _hand_written_tile(d.leaf_module_name, d.lanes)},
        timeout_s=300,
    )

    assert res.all_passed, f"{res.failing_vector_lines}\n{res.compile_stderr or ''}"
    assert res.cycles_per_vector == (d.expected_cycles,) == (64,)


# --- the dataflow-matched GEMM design (D121) ---


def _gemm_hand_leaf(name: str, lanes: int) -> str:
    ins = ", ".join(["input logic signed [31:0] a"]
                    + [f"input logic signed [31:0] w{j}" for j in range(lanes)]
                    + [f"input logic signed [31:0] acc_in{j}" for j in range(lanes)])
    outs = ", ".join(f"output logic signed [31:0] acc_out{j}" for j in range(lanes))
    body = "\n".join(f"  assign acc_out{j} = acc_in{j} + a * w{j};" for j in range(lanes))
    return f"module {name} ({ins}, {outs});\n{body}\nendmodule\n"


@pytest.mark.parametrize("B,C,K,lanes", [(2, 4, 4, 2), (4, 32, 32, 8), (4, 32, 32, 16)])
def test_the_gemm_wrapper_computes_the_right_matrix_at_its_predicted_latency(B, C, K, lanes):
    from flux_codegen_rtl_harness import (compile_and_run, design_spec_from_dict, gemm_cycles,
                                          gemm_spec, generate_gemm_wrapper)

    i_mem = [[(b * C + c) % 9 - 4 for c in range(C)] for b in range(B)]
    w_mem = [[(c * K + k) % 7 - 3 for k in range(K)] for c in range(C)]
    predicted = gemm_cycles(B=B, C=C, K=K, lanes=lanes)

    res = compile_and_run(
        generate_gemm_wrapper("GemmTop", "GemmStep", B=B, C=C, K=K, lanes=lanes),
        design_spec_from_dict(gemm_spec("GemmTop", B=B, C=C, K=K, lanes=lanes,
                                        i_mem=i_mem, w_mem=w_mem)),
        extra_sources={"GemmStep": _gemm_hand_leaf("GemmStep", lanes)}, timeout_s=300,
    )

    assert res.all_passed, f"{res.failing_vector_lines}\n{res.compile_stderr or ''}"
    assert res.cycles_per_vector == (predicted,)


@pytest.mark.skipif(shutil.which("verilator") is None, reason="needs verilator")
def test_the_generated_design_measures_what_the_reference_evaluator_measures():
    """A generated design's cycle count equals the reference evaluator's for the same pair,
    both from real Verilator runs, because the schedule is the same."""
    import yaml
    from flux_evaluator_abi import Budget, Candidate
    from flux_evaluator_abi import make_evaluator
    from flux_codegen_rtl_harness import compile_and_run, design_spec_from_dict
    from flux_evaluator_openroad.derive import derive_gemm_design

    root = Path(__file__).resolve().parents[2]
    wl = yaml.safe_load((root / "core/ir/workload/examples/mlp-gemm0.yaml").read_text())
    arch = yaml.safe_load((root / "core/ir/architecture/examples/simple-npu-1d-v1.yaml").read_text())

    reference = make_evaluator("rtl").evaluate(
        Candidate(workload=wl, arch=arch, mapping=None), Budget(), frozenset({"latency_cycles"})
    ).metrics["latency_cycles"].value

    d = derive_gemm_design(wl, arch)
    res = compile_and_run(
        d.wrapper_source, design_spec_from_dict(d.top_spec),
        extra_sources={d.leaf_module_name: _gemm_hand_leaf(d.leaf_module_name, d.lanes)},
        timeout_s=300,
    )

    assert res.all_passed, res.failing_vector_lines
    assert d.expected_cycles == reference == 529.0
    assert res.total_cycles == reference


@pytest.mark.parametrize("wrapper", ["gemm", "tiled"])
def test_a_second_vector_measures_its_own_latency_not_zero(wrapper):
    """Both wrappers restart on a second `start` instead of reporting 0 cycles and stale outputs (D124)."""
    from flux_codegen_rtl_harness import (compile_and_run, design_spec_from_dict, gemm_cycles,
                                          gemm_spec, generate_gemm_wrapper, generate_tiled_wrapper,
                                          sequential_spec)

    if wrapper == "gemm":
        B, C, K, lanes = 2, 4, 4, 2
        predicted = gemm_cycles(B=B, C=C, K=K, lanes=lanes)
        specs = [
            gemm_spec("ReArmG", B=B, C=C, K=K, lanes=lanes,
                      i_mem=[[(b * C + c + s) % 5 - 2 for c in range(C)] for b in range(B)],
                      w_mem=[[(c * K + k + s) % 3 - 1 for k in range(K)] for c in range(C)])
            for s in (0, 1)
        ]
        source = generate_gemm_wrapper("ReArmG", "ReArmGS", B=B, C=C, K=K, lanes=lanes)
        leaf = {"ReArmGS": _gemm_hand_leaf("ReArmGS", lanes)}
    else:
        lane_width, steps = 2, 4
        n = lane_width * steps
        predicted = steps
        specs = [
            sequential_spec("ReArmT", n, [i + s for i in range(n)], [2] * n) for s in (0, 5)
        ]
        source = generate_tiled_wrapper("ReArmT", "ReArmTS", lane_width=lane_width, steps=steps)
        leaf = {"ReArmTS": _hand_written_tile("ReArmTS", lane_width)}

    spec = dict(specs[0])
    spec["test_vectors"] = [specs[0]["test_vectors"][0], specs[1]["test_vectors"][0]]

    res = compile_and_run(source, design_spec_from_dict(spec), extra_sources=leaf, timeout_s=300)

    assert res.all_passed, f"{res.failing_vector_lines}\n{res.compile_stderr or ''}"
    assert res.cycles_per_vector == (predicted, predicted), (
        "the second vector must measure its own latency; 0 means the design never re-armed"
    )


# --- a ragged final K-group, beyond what the reference can express (D130) ---


@pytest.mark.parametrize("B,C,K,lanes", [(2, 4, 5, 2), (2, 3, 7, 4), (2, 4, 3, 8), (2, 4, 4, 2)])
def test_a_masked_ragged_k_group_computes_the_right_matrix_at_its_predicted_latency(B, C, K, lanes):
    """With `K % lanes != 0` the masked final group is correct: out-of-range weights are zeroed
    and their drain skipped; the last parameter set is a whole-group control."""
    from flux_codegen_rtl_harness import (compile_and_run, design_spec_from_dict, gemm_cycles,
                                          gemm_spec, generate_gemm_wrapper)

    i_mem = [[(b * C + c) % 9 - 4 for c in range(C)] for b in range(B)]
    w_mem = [[(c * K + k) % 7 - 3 for k in range(K)] for c in range(C)]
    predicted = gemm_cycles(B=B, C=C, K=K, lanes=lanes)
    assert predicted == B * C * -(-K // lanes) + B * -(-K // lanes) + 1

    res = compile_and_run(
        generate_gemm_wrapper("RagTop", "RagStep", B=B, C=C, K=K, lanes=lanes),
        design_spec_from_dict(gemm_spec("RagTop", B=B, C=C, K=K, lanes=lanes,
                                        i_mem=i_mem, w_mem=w_mem)),
        extra_sources={"RagStep": _gemm_hand_leaf("RagStep", lanes)}, timeout_s=300,
    )

    assert res.all_passed, f"{res.failing_vector_lines}\n{res.compile_stderr or ''}"
    assert res.cycles_per_vector == (predicted,)


def test_the_ragged_case_is_one_the_reference_evaluator_genuinely_refuses():
    """`evaluator/rtl` rejects the ragged candidate the generated design handles."""
    import yaml
    from flux_evaluator_abi import Budget, Candidate
    from flux_evaluator_abi import make_evaluator
    from flux_evaluator_rtl import NotExpressibleError
    from flux_evaluator_openroad.derive import derive_gemm_design

    root = Path(__file__).resolve().parents[2]
    wl = yaml.safe_load((root / "core/ir/workload/examples/mlp-gemm0.yaml").read_text())
    arch = yaml.safe_load((root / "core/ir/architecture/examples/simple-npu-1d-v1.yaml").read_text())
    for node in arch["hierarchy"]:
        if node.get("class") == "compute":
            node["attrs"]["dims"][next(iter(node["attrs"]["dims"]))] = 12   # 32 % 12 != 0

    with pytest.raises(NotExpressibleError, match="multiple of LANES"):
        make_evaluator("rtl").evaluate(
            Candidate(workload=wl, arch=arch, mapping=None), Budget(),
            frozenset({"latency_cycles"}),
        )

    # ...while the generated design derives a real schedule for it
    d = derive_gemm_design(wl, arch)
    assert d.lanes == 12 and d.expected_cycles == 4 * 32 * 3 + 4 * 3 + 1


def test_a_generated_design_measures_a_candidate_the_reference_cannot_express():
    """lanes=12 against K=32 has no RTL ground truth, and a real generator measures it (D134).

    The refusal and the measurement are asserted together: if the reference gains ragged
    support, this test should fail and be re-thought.
    """
    import yaml
    from flux_evaluator_abi import Budget, Candidate
    from flux_evaluator_abi import make_evaluator
    from flux_codegen_rtl_harness import compile_and_run, design_spec_from_dict
    from flux_evaluator_rtl import NotExpressibleError
    from flux_evaluator_openroad.derive import derive_gemm_design

    root = Path(__file__).resolve().parents[2]
    wl = yaml.safe_load((root / "core/ir/workload/examples/mlp-gemm0.yaml").read_text())
    arch = yaml.safe_load((root / "core/ir/architecture/examples/simple-npu-1d-v1.yaml").read_text())
    for node in arch["hierarchy"]:
        if node.get("class") == "compute":
            node["attrs"]["dims"][next(iter(node["attrs"]["dims"]))] = 12

    with pytest.raises(NotExpressibleError, match="multiple of LANES"):
        make_evaluator("rtl").evaluate(
            Candidate(workload=wl, arch=arch, mapping=None), Budget(),
            frozenset({"latency_cycles"}),
        )

    d = derive_gemm_design(wl, arch)
    assert d.expected_cycles == 4 * 32 * 3 + 4 * 3 + 1 == 397   # KG = ceil(32/12) = 3

    res = compile_and_run(
        d.wrapper_source, design_spec_from_dict(d.top_spec),
        extra_sources={d.leaf_module_name: _gemm_hand_leaf(d.leaf_module_name, d.lanes)},
        timeout_s=300,
    )

    assert res.all_passed, f"{res.failing_vector_lines}\n{res.compile_stderr or ''}"
    assert res.total_cycles == 397


def test_the_ragged_example_workload_is_measurable_and_not_degenerate():
    """K=100 at 8 lanes: 13 K-groups with 4 masked lanes in the last, which the reference refuses
    (D137); the refusal and the measurement are asserted together."""
    import yaml
    from flux_evaluator_abi import Budget, Candidate
    from flux_evaluator_abi import make_evaluator
    from flux_codegen_rtl_harness import compile_and_run, design_spec_from_dict
    from flux_evaluator_rtl import NotExpressibleError
    from flux_evaluator_openroad.derive import derive_gemm_design

    root = Path(__file__).resolve().parents[2]
    wl = yaml.safe_load((root / "core/ir/workload/examples/mlp-gemm-ragged-v1.yaml").read_text())
    arch = yaml.safe_load((root / "core/ir/architecture/examples/simple-npu-1d-v1.yaml").read_text())

    with pytest.raises(NotExpressibleError, match="multiple of LANES"):
        make_evaluator("rtl").evaluate(
            Candidate(workload=wl, arch=arch, mapping=None), Budget(),
            frozenset({"latency_cycles"}),
        )

    d = derive_gemm_design(wl, arch)
    assert d.lanes == 8 and d.shape["K"] == 100
    assert d.expected_cycles == 4 * 32 * 13 + 4 * 13 + 1 == 1717   # KG = ceil(100/8) = 13
    # 4 masked lanes of 8 in the final group
    assert 13 * 8 - 100 == 4

    res = compile_and_run(
        d.wrapper_source, design_spec_from_dict(d.top_spec),
        extra_sources={d.leaf_module_name: _gemm_hand_leaf(d.leaf_module_name, d.lanes)},
        timeout_s=300,
    )
    assert res.all_passed, f"{res.failing_vector_lines}\n{res.compile_stderr or ''}"
    assert res.total_cycles == 1717
