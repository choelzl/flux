"""Runs flux_synthesize_composite_rtl_design for real (docs/decisions.md D52): real Yosys
synthesis of a composed design, called as a real CHIA node.
"""

from __future__ import annotations

from flux_chia_nodes.synthesize_composite_rtl import flux_synthesize_composite_rtl_design

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
    "test_vectors": [{"inputs": {"x": 1, "y": 1, "z": 1}, "expected": {"total": 3}}],
}


def test_synthesizes_a_real_composite_design():
    result = flux_synthesize_composite_rtl_design(
        leaf_spec_docs={"Adder2": _ADDER_SPEC_DOC},
        leaf_sources={"Adder2": _ADDER_SOURCE},
        composition_spec_doc=_ADDER3_COMPOSITION_DOC,
    )
    assert result.total_cells > 0
    assert sum(result.cells_by_type.values()) == result.total_cells
