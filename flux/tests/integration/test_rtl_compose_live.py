"""Runs real Verilator compilation of a composed, multi-module design through
flux_codegen_rtl_harness.compose (docs/decisions.md D48): two real, independently-verified
`Adder2` leaf instances wired into an `Adder3` composite, compiled and run together, checked
against real end-to-end test vectors — the "many different and various designs," not just
isolated single modules.
"""

from __future__ import annotations

from flux_codegen_rtl_harness import compile_and_run, design_spec_from_dict
from flux_codegen_rtl_harness.compose import (
    compile_and_run_composite,
    composition_spec_from_dict,
    generate_composite_module_sv,
)

_ADDER_SPEC = design_spec_from_dict({
    "module_name": "Adder2",
    "ports": [
        {"name": "a", "dir": "in", "dtype": "int"},
        {"name": "b", "dir": "in", "dtype": "int"},
        {"name": "sum", "dir": "out", "dtype": "int"},
    ],
    "behavior": "combinational: sum = a + b",
    "test_vectors": [{"inputs": {"a": 1, "b": 1}, "expected": {"sum": 2}}],
})

_ADDER_SOURCE = """
module Adder2 (
    input  logic signed [31:0] a,
    input  logic signed [31:0] b,
    output logic signed [31:0] sum
);
    assign sum = a + b;
endmodule
"""

_ADDER3_DOC = {
    "top_module_name": "Adder3",
    "instances": [
        {"module_name": "Adder2", "instance_name": "add1"},
        {"module_name": "Adder2", "instance_name": "add2"},
    ],
    "nets": {
        "add1": {"a": "x", "b": "y", "sum": "partial"},
        "add2": {"a": "partial", "b": "z", "sum": "total"},
    },
    "ports": [
        {"name": "x", "dir": "in", "dtype": "int"},
        {"name": "y", "dir": "in", "dtype": "int"},
        {"name": "z", "dir": "in", "dtype": "int"},
        {"name": "total", "dir": "out", "dtype": "int"},
    ],
    "test_vectors": [
        {"inputs": {"x": 3, "y": 4, "z": 5}, "expected": {"total": 12}},
        {"inputs": {"x": -1, "y": 1, "z": 0}, "expected": {"total": 0}},
        {"inputs": {"x": 10, "y": 20, "z": 30}, "expected": {"total": 60}},
    ],
}


def test_leaf_verifies_standalone_before_being_composed():
    """A sanity precondition every real composition test in this file relies on: the leaf must
    genuinely verify on its own first, the same way any real caller's flow would work."""
    result = compile_and_run(_ADDER_SOURCE, _ADDER_SPEC)
    assert result.all_passed


def test_two_adder_instances_compose_into_a_real_working_adder3():
    comp_spec = composition_spec_from_dict(_ADDER3_DOC, leaf_specs={"Adder2": _ADDER_SPEC})
    result = compile_and_run_composite({"Adder2": _ADDER_SOURCE}, comp_spec, keep_workdir=True)
    assert result.compiled
    assert result.ran
    assert result.all_passed
    assert result.total_vectors == 3
    assert result.passed_vectors == 3
    assert result.failing_vector_lines == ()


def test_composite_produces_a_real_nonempty_vcd_trace():
    comp_spec = composition_spec_from_dict(_ADDER3_DOC, leaf_specs={"Adder2": _ADDER_SPEC})
    result = compile_and_run_composite({"Adder2": _ADDER_SOURCE}, comp_spec, keep_workdir=True)
    assert result.vcd_nonempty
    assert result.vcd_path is not None
    assert result.vcd_path.read_text().startswith("$")


def test_a_wrong_leaf_implementation_makes_the_composite_fail_too():
    """Real, checked propagation: a broken leaf (subtraction instead of addition) must make the
    composed end-to-end vectors fail too, not silently pass because the top-level wiring alone
    was checked."""
    wrong_source = _ADDER_SOURCE.replace("a + b", "a - b")
    comp_spec = composition_spec_from_dict(_ADDER3_DOC, leaf_specs={"Adder2": _ADDER_SPEC})
    result = compile_and_run_composite({"Adder2": wrong_source}, comp_spec)
    assert result.compiled
    assert result.ran
    assert not result.all_passed


def test_generated_composite_source_wires_by_net_name():
    comp_spec = composition_spec_from_dict(_ADDER3_DOC, leaf_specs={"Adder2": _ADDER_SPEC})
    sv = generate_composite_module_sv(comp_spec)
    assert "Adder2 add1 (.a(x), .b(y), .sum(partial));" in sv
    assert "Adder2 add2 (.a(partial), .b(z), .sum(total));" in sv


def test_real_port_name_collision_with_harness_bookkeeping_no_longer_breaks_anything():
    """docs/decisions.md D48's real found bug: this exact composition's own top-level output is
    named "total" — the same name driver_gen.py's harness bookkeeping variable used before being
    fixed. A regression here would mean that fix broke, not that this test is contrived."""
    comp_spec = composition_spec_from_dict(_ADDER3_DOC, leaf_specs={"Adder2": _ADDER_SPEC})
    assert any(p.name == "total" for p in comp_spec.ports)
    result = compile_and_run_composite({"Adder2": _ADDER_SOURCE}, comp_spec)
    assert result.compiled
    assert result.all_passed


_REG_SPEC = design_spec_from_dict({
    "module_name": "Reg",
    "ports": [{"name": "d", "dir": "in", "dtype": "bool"}, {"name": "q", "dir": "out", "dtype": "bool"}],
    "behavior": "D flip-flop",
    "test_vectors": [{"inputs": {"d": True}, "expected": {"q": True}}],
    "is_clocked": True,
})

_REG_SOURCE = """
module Reg (
    input logic clk,
    input logic rst_n,
    input logic d,
    output logic q
);
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) q <= 1'b0;
        else q <= d;
    end
endmodule
"""

_SHIFT_REG2_DOC = {
    "top_module_name": "ShiftReg2",
    "instances": [
        {"module_name": "Reg", "instance_name": "r1"},
        {"module_name": "Reg", "instance_name": "r2"},
    ],
    "nets": {
        "r1": {"d": "din", "q": "mid"},
        "r2": {"d": "mid", "q": "dout"},
    },
    "ports": [
        {"name": "din", "dir": "in", "dtype": "bool"},
        {"name": "dout", "dir": "out", "dtype": "bool"},
    ],
    "test_vectors": [
        {"inputs": {"din": True}, "expected": {"dout": False}},   # cycle 1: bit still in r1
        {"inputs": {"din": False}, "expected": {"dout": True}},   # cycle 2: bit reaches dout
        {"inputs": {"din": False}, "expected": {"dout": False}},  # cycle 3: dout settles back
    ],
}


def test_two_clocked_registers_compose_into_a_real_working_2_stage_shift_register():
    """docs/decisions.md D50: composing clocked leaves, real support — a real gap found by
    direct empirical check, not assumed to work just because D48 and D49 each worked alone. A
    genuinely checked pipeline-latency property: a bit takes exactly 2 real clock cycles to
    travel from din to dout, not something a single register could demonstrate."""
    comp_spec = composition_spec_from_dict(_SHIFT_REG2_DOC, leaf_specs={"Reg": _REG_SPEC})
    assert comp_spec.is_clocked
    result = compile_and_run_composite({"Reg": _REG_SOURCE}, comp_spec, keep_workdir=True)
    assert result.compiled
    assert result.ran
    assert result.all_passed
    assert result.total_vectors == 3
    assert result.passed_vectors == 3
    assert result.vcd_nonempty
