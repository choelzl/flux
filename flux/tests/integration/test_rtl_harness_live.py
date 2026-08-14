"""Runs real Verilator compilation through flux_codegen_rtl_harness (docs/decisions.md D43): a
correct hand-written module (real compile, real run, real VCD trace), a functionally wrong one
(real failure detection with accurate diagnostics), and a syntactically broken one (real
CompileError with real Verilator stderr) — proving the harness itself before any LLM-generated
source is ever trusted to it (see flows/chia_nodes/generate_rtl.py, D44).

Requires real Verilator (`nix develop .#default`), the same toolchain `evaluators/rtl`'s own
integration tests already build against.
"""

from __future__ import annotations

import pytest
from flux_codegen_rtl_harness import CompileError, compile_and_run, design_spec_from_dict

_ADDER_SPEC = design_spec_from_dict({
    "module_name": "Adder2",
    "ports": [
        {"name": "a", "dir": "in", "dtype": "int"},
        {"name": "b", "dir": "in", "dtype": "int"},
        {"name": "sum", "dir": "out", "dtype": "int"},
    ],
    "behavior": "combinational: sum = a + b",
    "test_vectors": [
        {"inputs": {"a": 3, "b": 4}, "expected": {"sum": 7}},
        {"inputs": {"a": -1, "b": 1}, "expected": {"sum": 0}},
        {"inputs": {"a": 0, "b": 0}, "expected": {"sum": 0}},
    ],
})

_CORRECT_ADDER = """
module Adder2 (
    input  logic signed [31:0] a,
    input  logic signed [31:0] b,
    output logic signed [31:0] sum
);
    assign sum = a + b;
endmodule
"""


def test_correct_module_compiles_runs_and_passes_all_vectors():
    result = compile_and_run(_CORRECT_ADDER, _ADDER_SPEC, keep_workdir=True)
    assert result.compiled
    assert result.ran
    assert result.total_vectors == 3
    assert result.passed_vectors == 3
    assert result.all_passed
    assert result.failing_vector_lines == ()


def test_correct_module_produces_a_real_nonempty_vcd_trace():
    result = compile_and_run(_CORRECT_ADDER, _ADDER_SPEC, keep_workdir=True)
    assert result.vcd_nonempty
    assert result.vcd_path is not None
    assert result.vcd_path.exists()
    assert result.vcd_path.read_text().startswith("$")  # real VCD files open with a $-header


def test_vcd_path_is_none_when_workdir_not_kept():
    result = compile_and_run(_CORRECT_ADDER, _ADDER_SPEC, keep_workdir=False)
    assert result.vcd_nonempty
    assert result.vcd_path is None


def test_functionally_wrong_module_is_caught_with_accurate_diagnostics():
    wrong_module = _CORRECT_ADDER.replace("a + b", "a - b")
    result = compile_and_run(wrong_module, _ADDER_SPEC)
    assert result.compiled
    assert result.ran
    assert not result.all_passed
    assert result.passed_vectors == 1
    assert result.total_vectors == 3
    assert "VECTOR 0 FAIL sum=-1" in result.failing_vector_lines[0]
    assert "VECTOR 1 FAIL sum=-2" in result.failing_vector_lines[1]


def test_syntactically_broken_module_raises_compile_error_with_real_stderr():
    broken_module = _CORRECT_ADDER.replace("assign sum = a + b;", "assign sum = a +++ b")
    with pytest.raises(CompileError) as exc_info:
        compile_and_run(broken_module, _ADDER_SPEC)
    assert exc_info.value.returncode != 0
    assert "%Error" in exc_info.value.stderr


def test_bool_dtype_port_works_end_to_end():
    spec = design_spec_from_dict({
        "module_name": "Inverter",
        "ports": [
            {"name": "x", "dir": "in", "dtype": "bool"},
            {"name": "y", "dir": "out", "dtype": "bool"},
        ],
        "behavior": "combinational: y = !x",
        "test_vectors": [
            {"inputs": {"x": True}, "expected": {"y": False}},
            {"inputs": {"x": False}, "expected": {"y": True}},
        ],
    })
    module = """
    module Inverter (
        input  logic x,
        output logic y
    );
        assign y = !x;
    endmodule
    """
    result = compile_and_run(module, spec)
    assert result.all_passed


_REG_SPEC = design_spec_from_dict({
    "module_name": "Reg",
    "ports": [
        {"name": "d", "dir": "in", "dtype": "bool"},
        {"name": "q", "dir": "out", "dtype": "bool"},
    ],
    "behavior": "clocked D flip-flop: q <= d on each rising clock edge, active-low async reset to 0",
    "test_vectors": [
        {"inputs": {"d": True}, "expected": {"q": True}},
        {"inputs": {"d": False}, "expected": {"q": False}},
        {"inputs": {"d": True}, "expected": {"q": True}},
    ],
    "is_clocked": True,
})

_CORRECT_REG = """
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


def test_clocked_dff_compiles_runs_and_passes_all_vectors():
    """docs/decisions.md D49: real sequential-design verification, not the "not built yet"
    rejection D43 originally shipped."""
    result = compile_and_run(_CORRECT_REG, _REG_SPEC, keep_workdir=True)
    assert result.compiled
    assert result.ran
    assert result.all_passed
    assert result.total_vectors == 3
    assert result.passed_vectors == 3
    assert result.vcd_nonempty


def test_inverted_clocked_dff_is_caught_with_accurate_diagnostics():
    wrong = _CORRECT_REG.replace("q <= d;", "q <= ~d;")
    result = compile_and_run(wrong, _REG_SPEC)
    assert result.compiled
    assert result.ran
    assert not result.all_passed
    assert result.passed_vectors == 0
    assert "VECTOR 0 FAIL q=0" in result.failing_vector_lines[0]


def test_clocked_counter_with_enable_accumulates_state_correctly_across_cycles():
    """A real, checked property a pure D flip-flop can't demonstrate: state that depends on its
    own previous value across multiple real clock cycles, not just the current input."""
    spec = design_spec_from_dict({
        "module_name": "Counter",
        "ports": [
            {"name": "en", "dir": "in", "dtype": "bool"},
            {"name": "count", "dir": "out", "dtype": "int"},
        ],
        "behavior": "clocked up-counter with enable, active-low async reset to 0",
        "test_vectors": [
            {"inputs": {"en": True}, "expected": {"count": 1}},
            {"inputs": {"en": True}, "expected": {"count": 2}},
            {"inputs": {"en": False}, "expected": {"count": 2}},  # holds when disabled
            {"inputs": {"en": True}, "expected": {"count": 3}},
        ],
        "is_clocked": True,
    })
    module = """
    module Counter (
        input logic clk,
        input logic rst_n,
        input logic en,
        output logic signed [31:0] count
    );
        always_ff @(posedge clk or negedge rst_n) begin
            if (!rst_n) count <= 0;
            else if (en) count <= count + 1;
        end
    endmodule
    """
    result = compile_and_run(module, spec)
    assert result.all_passed
    assert result.passed_vectors == 4
