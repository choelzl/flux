"""Runs real Yosys synthesis on composed, multi-module designs (docs/decisions.md D52) —
extending D47's single-module synthesis to composites, closing the gap D47/D51 both named
directly. Found and fixed two real parsing bugs along the way: Yosys's own "N. Printing
statistics." step number isn't fixed at "3." (composites trigger extra passes, shifting it to
"4."+), and its final stats block prints one "N cells" line *per module* plus a final aggregate
line for the whole design — taking the *first* match (the old, single-module-only-tested
behavior) silently returned one submodule's local count instead of the real, whole-design total.
"""

from __future__ import annotations

import subprocess

from flux_codegen_systemc_harness import design_spec_from_dict
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
    """The real, checked property the two parsing bugs broke: Adder3 (two real Adder2 instances,
    no logic of its own beyond wiring) must synthesize to exactly 2x a single Adder2's cell
    count, not 1x (one submodule's local count, the old silent-bug behavior)."""
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
    """docs/decisions.md D61: the real failure path this bug was actually found on — a leaf
    module passed via `extra_sources` (not the top-level composite source itself) using
    `endmodule;`. Found composing a real, LLM-generated toy-CPU leaf (`PcUnit`); reproduced here
    with a hand-written fixture so it doesn't depend on live LLM non-determinism to catch a
    regression."""
    leaf_with_trailing_semicolon = _ADDER_SOURCE.rstrip() + ";\n"
    assert "endmodule;" in leaf_with_trailing_semicolon
    comp_spec = composition_spec_from_dict(_ADDER3_DOC, leaf_specs={"Adder2": _ADDER_SPEC})
    result = synthesize_composite({"Adder2": leaf_with_trailing_semicolon}, comp_spec)
    assert result.total_cells > 0


def test_real_composite_cache_hit_skips_a_real_yosys_rerun(tmp_path, monkeypatch):
    """docs/decisions.md D89: `synthesize_composite` passes `cache` straight through to
    `synthesize_and_measure` with no separate key derivation — a real cache hit here proves that
    pass-through actually works end to end, not just in isolation."""
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

    assert len(calls) == 1  # the real Yosys binary only ran once for the whole composite
    assert r1.total_cells == r2.total_cells > 0
