"""Runs flux_compose_and_verify_rtl_design for real (docs/decisions.md D48): real Verilator
compilation of a composed, multi-module design, called as a real CHIA node — not just the
underlying harness function directly (see tests/integration/test_rtl_compose_live.py for that).
"""

from __future__ import annotations

from flux_chia_nodes.compose_rtl import flux_compose_and_verify_rtl_design

_ADDER_SPEC_DOC = {
    "module_name": "Adder2",
    "ports": [
        {"name": "a", "dir": "in", "dtype": "int"},
        {"name": "b", "dir": "in", "dtype": "int"},
        {"name": "sum", "dir": "out", "dtype": "int"},
    ],
    "behavior": "combinational: sum = a + b",
    "test_vectors": [{"inputs": {"a": 1, "b": 1}, "expected": {"sum": 2}}],
}

_ADDER_SOURCE = """
module Adder2 (
    input  logic signed [31:0] a,
    input  logic signed [31:0] b,
    output logic signed [31:0] sum
);
    assign sum = a + b;
endmodule
"""

_ADDER3_COMPOSITION_DOC = {
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
    ],
}


def test_composes_and_verifies_a_real_multi_module_design():
    result = flux_compose_and_verify_rtl_design(
        leaf_spec_docs={"Adder2": _ADDER_SPEC_DOC},
        leaf_sources={"Adder2": _ADDER_SOURCE},
        composition_spec_doc=_ADDER3_COMPOSITION_DOC,
    )
    assert result.compiled
    assert result.ran
    assert result.all_passed
    assert result.total_vectors == 2
    assert result.passed_vectors == 2
