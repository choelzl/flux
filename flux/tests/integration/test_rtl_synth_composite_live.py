"""Yosys synthesis of composed, multi-module designs (D52).

The stats step number is not fixed at "3." for composites, and the stats block prints one "N cells"
line per module plus a final aggregate; the parser takes the aggregate.
"""

from __future__ import annotations

import subprocess

from flux_codegen_harness_spec import design_spec_from_dict
from flux_codegen_rtl_harness import ToolResultCache, synthesize_and_measure
from flux_codegen_rtl_harness.compose import composition_spec_from_dict, synthesize_composite

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
    "test_vectors": [{"inputs": {"x": 1, "y": 1, "z": 1}, "expected": {"total": 3}}],
}


def test_composite_synthesis_reflects_the_whole_design_not_just_one_submodule():
    """Adder3 (two Adder2 instances, only wiring) synthesizes to exactly 2x one Adder2's cell count."""
    single = synthesize_and_measure(_ADDER_SOURCE, "Adder2")
    comp_spec = composition_spec_from_dict(_ADDER3_DOC, leaf_specs={"Adder2": _ADDER_SPEC})
    composite = synthesize_composite({"Adder2": _ADDER_SOURCE}, comp_spec)
    assert composite.total_cells == single.total_cells * 2


def test_composite_synthesis_reports_real_nonzero_cell_breakdown():
    comp_spec = composition_spec_from_dict(_ADDER3_DOC, leaf_specs={"Adder2": _ADDER_SPEC})
    result = synthesize_composite({"Adder2": _ADDER_SOURCE}, comp_spec)
    assert result.total_cells > 0
    assert sum(result.cells_by_type.values()) == result.total_cells


def test_leaf_source_with_trailing_endmodule_semicolon_synthesizes_correctly():
    """A leaf passed via `extra_sources` using `endmodule;` synthesizes (D61), with a hand-written fixture."""
    leaf_with_trailing_semicolon = _ADDER_SOURCE.rstrip() + ";\n"
    assert "endmodule;" in leaf_with_trailing_semicolon
    comp_spec = composition_spec_from_dict(_ADDER3_DOC, leaf_specs={"Adder2": _ADDER_SPEC})
    result = synthesize_composite({"Adder2": leaf_with_trailing_semicolon}, comp_spec)
    assert result.total_cells > 0


def test_real_composite_cache_hit_skips_a_real_yosys_rerun(tmp_path, monkeypatch):
    """`synthesize_composite` passes `cache` through to `synthesize_and_measure`, hitting end to end (D89)."""
    import flux_codegen_rtl_harness.synth as synth_module

    real_run = subprocess.run
    calls: list[int] = []

    def _counting_run(*args, **kwargs):
        calls.append(1)
        return real_run(*args, **kwargs)

    monkeypatch.setattr(synth_module.subprocess, "run", _counting_run)

    comp_spec = composition_spec_from_dict(_ADDER3_DOC, leaf_specs={"Adder2": _ADDER_SPEC})
    with ToolResultCache(tmp_path / "cache.db") as cache:
        r1 = synthesize_composite({"Adder2": _ADDER_SOURCE}, comp_spec, cache=cache)
        r2 = synthesize_composite({"Adder2": _ADDER_SOURCE}, comp_spec, cache=cache)

    assert len(calls) == 1  # Yosys ran once for the whole composite
    assert r1.total_cells == r2.total_cells > 0
