"""ASIC synthesis against ASAP7's vendored liberty library (D92): PDK-derived cell areas, a
sequential/combinational split, and hierarchical designs read from Yosys's "Chip area for top
module" line (a different string from the single-module "Chip area for module").
"""

from __future__ import annotations

import subprocess

import pytest
from flux_codegen_rtl_harness import Asap7SynthesisResult, SynthesisError, ToolResultCache
from flux_codegen_rtl_harness.asap7 import synthesize_with_asap7
from flux_codegen_rtl_harness.compose import composition_spec_from_dict, generate_composite_module_sv
from flux_codegen_harness_spec import design_spec_from_dict

_ADDER_SOURCE = """
module Adder2 (
    input  logic signed [31:0] a,
    input  logic signed [31:0] b,
    output logic signed [31:0] sum
);
    assign sum = a + b;
endmodule
"""

_REG_SOURCE = """
module Reg32 (
    input logic clk,
    input logic rst_n,
    input  logic signed [31:0] d,
    output logic signed [31:0] q
);
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) q <= 32'sd0;
        else q <= d;
    end
endmodule
"""

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


def test_real_combinational_synthesis_reports_a_real_pdk_area():
    """A 32-bit adder's ASAP7 area, pinned from a hand-verified Yosys run."""
    result = synthesize_with_asap7(_ADDER_SOURCE, "Adder2")
    assert result.area_um2 == pytest.approx(12.655440)
    assert result.sequential_area_um2 == pytest.approx(0.0)
    assert sum(count for count, _area in result.cells_by_type.values()) == 123
    assert all("ASAP7" in name for name in result.cells_by_type)


def test_real_clocked_synthesis_reports_a_real_sequential_split():
    """The sequential fraction of a design's area, meaningful only with real cell areas."""
    result = synthesize_with_asap7(_REG_SOURCE, "Reg32")
    assert result.area_um2 == pytest.approx(13.530240)
    assert result.sequential_area_um2 == pytest.approx(12.130560)
    assert result.sequential_fraction == pytest.approx(12.130560 / 13.530240)
    assert result.sequential_fraction > 0.85  # a register is overwhelmingly sequential area


def test_real_composite_synthesis_matches_the_real_whole_design_aggregate():
    """A composite's total comes from the "Chip area for top module" line, and the per-cell breakdown excludes the "N submodules" summary line."""
    comp_spec = composition_spec_from_dict(_ADDER3_DOC, leaf_specs={"Adder2": _ADDER_SPEC})
    composite_source = generate_composite_module_sv(comp_spec)
    single = synthesize_with_asap7(_ADDER_SOURCE, "Adder2")
    composite = synthesize_with_asap7(
        composite_source, "Adder3", extra_sources={"Adder2": _ADDER_SOURCE},
    )
    assert composite.area_um2 == pytest.approx(25.310880)
    assert composite.area_um2 == pytest.approx(single.area_um2 * 2)
    total_cells = sum(count for count, _area in composite.cells_by_type.values())
    assert total_cells == 246  # not inflated by a leaked "submodules"/"Adder2" summary line
    assert "Adder2" not in composite.cells_by_type  # a submodule name, not a cell type


def test_invalid_verilog_raises_synthesis_error_not_a_pdk_specific_one():
    with pytest.raises(SynthesisError):
        synthesize_with_asap7("module Broken(); not valid syntax here endmodule", "Broken")


def test_real_cache_hit_skips_a_real_yosys_rerun(tmp_path, monkeypatch):
    """The content-hash cache (D89) serves ASAP7 results, counted against `subprocess.run` calls."""
    import flux_codegen_rtl_harness.asap7 as asap7_module

    real_run = subprocess.run
    calls: list[int] = []

    def _counting_run(*args, **kwargs):
        calls.append(1)
        return real_run(*args, **kwargs)

    monkeypatch.setattr(asap7_module.subprocess, "run", _counting_run)

    with ToolResultCache(tmp_path / "cache.db") as cache:
        r1 = synthesize_with_asap7(_ADDER_SOURCE, "Adder2", cache=cache)
        r2 = synthesize_with_asap7(_ADDER_SOURCE, "Adder2", cache=cache)

    assert len(calls) == 1
    assert r1.area_um2 == r2.area_um2 == pytest.approx(12.655440)


def test_asap7_and_generic_caches_are_real_and_independent(tmp_path):
    """The ASAP7 cache key's "asap7" prefix keeps generic and PDK results for the same source apart."""
    from flux_codegen_rtl_harness import synthesize_and_measure

    with ToolResultCache(tmp_path / "cache.db") as cache:
        generic = synthesize_and_measure(_ADDER_SOURCE, "Adder2", cache=cache)
        pdk = synthesize_with_asap7(_ADDER_SOURCE, "Adder2", cache=cache)

    assert isinstance(pdk, Asap7SynthesisResult)
    assert pdk.area_um2 == pytest.approx(12.655440)
    assert generic.total_cells > 0  # a different number -- the generic internal-cell count


