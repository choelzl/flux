"""Real Verilator validation of the harness's latency-measuring mode (docs/decisions.md D115).

The instrument is validated against *hand-written* designs whose cycle counts are known by
construction, before any generator is pointed at it — measuring generated RTL with an unverified
measuring device would make every later number unfalsifiable.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("verilator") is None, reason="verilator not on PATH (needs .#default dev shell)"
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
    """One lane per clock, so total latency is exactly `lanes` cycles — a number known from the
    source, not from the harness."""
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
    """The decisive check: a design that consumes one lane per cycle must measure exactly `lanes`
    cycles. If the harness were off by one, or counted the start pulse, this would show it."""
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
    """Latency must be a real, comparable quantity — the whole point of measuring it. Two designs
    computing the same result at different rates must be distinguishable by the harness."""
    from flux_codegen_rtl_harness import compile_and_run, design_spec_from_dict

    inputs = {"a0": 3, "a1": 4, "w0": 2, "w1": 5}
    expected = {"acc": 3 * 2 + 4 * 5}
    vectors = [{"inputs": inputs, "expected": expected}]

    fast = compile_and_run(_sequential_mac("SeqMac2", 2), design_spec_from_dict(_spec("SeqMac2", 2, vectors)))

    # Same arithmetic, deliberately taking two cycles per lane instead of one.
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
    """Essential once a generator writes these: a DUT that never raises `done` must be a bounded,
    reported failure — not an infinite simulation that looks like a slow test."""
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


# --- The deterministic-wrapper split (docs/decisions.md D117) ---


def test_the_wrapper_alone_is_correct_against_a_hand_written_leaf():
    """The wrapper must be verified independently of any generator: with a known-correct leaf, a
    `lanes`-step schedule must measure exactly `lanes` cycles and compute the right sum."""
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
    """Latency is a property of the wrapper's schedule, not of the leaf — so it is predictable
    from the schedule alone, which is what makes a generated design usable as a reference."""
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


# --- The schedule derived from an IR candidate pair (docs/decisions.md D118) ---


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
    """A known-correct leaf, so a failure here is the derivation's or the wrapper's — never the
    generator's. Its interface comes from `leaf_operand_names`, the same function the wrapper and
    the generation prompt use, so this test cannot pass by agreeing with a stale convention."""
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
    """The D118 claim, end to end and in real Verilator: with the width taken from the
    architecture and the cycle count from the workload's reduction length, the composed design
    computes the right dot product and measures *exactly* the predicted number of cycles. lanes=5
    is the non-dividing case, where 32 operands become 7 zero-padded tiles."""
    from flux_codegen_rtl_harness import compile_and_run, design_spec_from_dict
    from flux_generation import derive_sequential_design

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
    """Latency has to *respond* to the candidate, not merely be reproducible — otherwise the
    derived number is a constant dressed up as a prediction. Same workload, two widths, one
    measured ratio."""
    from flux_codegen_rtl_harness import compile_and_run, design_spec_from_dict
    from flux_generation import derive_sequential_design

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


# --- Array-valued operand ports (docs/decisions.md D120) ---


@pytest.mark.parametrize("lane_width,steps", [(1, 4), (4, 8), (8, 16)])
def test_array_operands_compile_and_measure_exactly_like_flat_ones(lane_width, steps):
    """The representation change must be exactly that — a change of representation. Same schedule,
    same arithmetic, same measured latency; only the top-level interface differs."""
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
    """The point of D120, measured rather than argued: a 512-long reduction is 1029 top-level
    ports as flat operands — not a module interface anyone would call real — and two arrays plus
    an accumulator as arrays. It runs, it is right, and it takes exactly its scheduled 64 cycles."""
    from flux_codegen_rtl_harness import compile_and_run, design_spec_from_dict
    from flux_generation import derive_sequential_design

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


# --- The dataflow-matched GEMM design (docs/decisions.md D121) ---


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
    """The claim D118 declined to make and this makes: a *generated* design's cycle count and the
    reference evaluator's own measured cycle count for the same (workload, architecture) pair are
    the same number — because the schedule is now the same schedule. Both sides are real runs:
    real Verilator on `mac_array.sv` for the reference, real Verilator on the composed design.
    """
    import yaml
    from flux_evaluator_abi import Budget, Candidate
    from flux_cli.registry import make_evaluator
    from flux_codegen_rtl_harness import compile_and_run, design_spec_from_dict
    from flux_generation import derive_gemm_design

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
    """Review finding (docs/decisions.md D124). The GEMM wrapper parked in its DONE state and
    ignored the next `start`, so a second vector measured **0 cycles** and re-reported the first
    vector's outputs. It failed loudly only because that vector's golden data differed — with
    repeated inputs it would have passed at a latency of zero, which is the worst shape a
    measurement bug can take. Both wrappers are checked here because the tiled one already
    handled this and nothing compared them.
    """
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


# --- A ragged final K-group: past what the reference can express (docs/decisions.md D130) ---


@pytest.mark.parametrize("B,C,K,lanes", [(2, 4, 5, 2), (2, 3, 7, 4), (2, 4, 3, 8), (2, 4, 4, 2)])
def test_a_masked_ragged_k_group_computes_the_right_matrix_at_its_predicted_latency(B, C, K, lanes):
    """`K % lanes != 0` is the case `evaluators/rtl` refuses outright, so these candidates have no
    RTL ground truth at all. The masked final group has to be *correct*, not merely runnable: the
    guard must zero the out-of-range weights (so masked lanes accumulate nothing) and skip their
    drain (so nothing is written past the last real output column). The last parameter set is a
    whole-group control on the same code path."""
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
    """The claim that this extends the reference frontier, checked against the reference rather
    than asserted: `evaluators/rtl` must actually reject the same candidate the generated design
    handles. If it ever stops refusing, this capability stops being new information."""
    import yaml
    from flux_evaluator_abi import Budget, Candidate
    from flux_cli.registry import make_evaluator
    from flux_evaluator_rtl import NotExpressibleError
    from flux_generation import derive_gemm_design

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

    # ...while the generated design derives a real schedule for it.
    d = derive_gemm_design(wl, arch)
    assert d.lanes == 12 and d.expected_cycles == 4 * 32 * 3 + 4 * 3 + 1


def test_a_generated_design_measures_a_candidate_the_reference_cannot_express():
    """The payoff of D130, end to end and with a real generator (docs/decisions.md D134).

    `lanes=12` against `K=32` is a candidate `evaluators/rtl` refuses outright, so it has no RTL
    ground truth of any kind — which is the only situation where a generated design contributes
    information rather than reproducing a number the store already holds (on shapes both cover the
    residual is identical either way, `+1.937618`, measured in D125).

    Both halves are asserted together on purpose: the refusal is what makes the measurement worth
    having, so if the reference ever gains ragged support this test should fail and be re-thought
    rather than quietly keep passing.
    """
    import yaml
    from flux_evaluator_abi import Budget, Candidate
    from flux_cli.registry import make_evaluator
    from flux_codegen_rtl_harness import compile_and_run, design_spec_from_dict
    from flux_evaluator_rtl import NotExpressibleError
    from flux_generation import derive_gemm_design

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
    """`ir/workload/examples/mlp-gemm-ragged-v1.yaml` exists because every *other* example here has
    power-of-two dims, so `K % lanes` is almost always 0 and the reference can express nearly the
    whole frontier (docs/decisions.md D137: 1 of 16 standard candidates lacks ground truth, and
    that one leaves half the array idle).

    K=100 at 8 lanes is the non-degenerate case: 13 K-groups with 4 masked lanes in the last, a 4%
    overhead rather than 50%. Both halves asserted together — the refusal is what makes the
    measurement worth having."""
    import yaml
    from flux_evaluator_abi import Budget, Candidate
    from flux_cli.registry import make_evaluator
    from flux_codegen_rtl_harness import compile_and_run, design_spec_from_dict
    from flux_evaluator_rtl import NotExpressibleError
    from flux_generation import derive_gemm_design

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
    # 4 masked lanes of 8 in the final group — the point of choosing this shape.
    assert 13 * 8 - 100 == 4

    res = compile_and_run(
        d.wrapper_source, design_spec_from_dict(d.top_spec),
        extra_sources={d.leaf_module_name: _gemm_hand_leaf(d.leaf_module_name, d.lanes)},
        timeout_s=300,
    )
    assert res.all_passed, f"{res.failing_vector_lines}\n{res.compile_stderr or ''}"
    assert res.total_cycles == 1717
