"""Real Yosys synthesis through flux_codegen_rtl_harness.synth (D47): cell counts, a more complex
module giving more cells, error handling for invalid Verilog, and the result cache (D89).
"""

from __future__ import annotations

import subprocess

import pytest
from flux_codegen_rtl_harness import SynthesisError, ToolResultCache, synthesize_and_measure

_ADDER = """
module Adder2 (
    input  logic signed [31:0] a,
    input  logic signed [31:0] b,
    output logic signed [31:0] sum
);
    assign sum = a + b;
endmodule
"""

_MORE_COMPLEX = """
module Adder2 (
    input  logic signed [31:0] a,
    input  logic signed [31:0] b,
    output logic signed [31:0] sum
);
    logic signed [31:0] tmp1, tmp2, tmp3;
    assign tmp1 = a + b;
    assign tmp2 = a - b;
    assign tmp3 = a ^ b;
    assign sum = (a[0]) ? tmp1 : ((b[0]) ? tmp2 : tmp3);
endmodule
"""


def test_real_synthesis_reports_nonzero_cells():
    result = synthesize_and_measure(_ADDER, "Adder2")
    assert result.total_cells > 0
    assert sum(result.cells_by_type.values()) == result.total_cells
    assert all(k.startswith("$") for k in result.cells_by_type)


def test_more_complex_module_synthesizes_to_more_cells():
    """A more complex design synthesizes to strictly more cells (exact counts vary by Yosys
    version)."""
    simple = synthesize_and_measure(_ADDER, "Adder2")
    complex_ = synthesize_and_measure(_MORE_COMPLEX, "Adder2")
    assert complex_.total_cells > simple.total_cells


def test_invalid_verilog_raises_synthesis_error():
    with pytest.raises(SynthesisError) as exc_info:
        synthesize_and_measure("module Broken(); not valid syntax here endmodule", "Broken")
    assert exc_info.value.returncode != 0


def test_wrong_top_module_name_raises_synthesis_error():
    """Valid Verilog, but `-top` names a module not in the source."""
    with pytest.raises(SynthesisError):
        synthesize_and_measure(_ADDER, "ThisModuleDoesNotExist")


def test_trailing_semicolon_after_endmodule_is_tolerated():
    """`endmodule;` is normalized away: Verilator tolerates it, Yosys rejects it (D61)."""
    source_with_trailing_semicolon = _ADDER.rstrip() + ";\n"
    assert "endmodule;" in source_with_trailing_semicolon  # sanity: the fixture actually has it
    result = synthesize_and_measure(source_with_trailing_semicolon, "Adder2")
    assert result.total_cells > 0


def test_real_cache_hit_skips_a_real_yosys_rerun(tmp_path, monkeypatch):
    """Content-hash-keyed caching (D89), counted on the `subprocess.run` call Yosys goes through."""
    import flux_codegen_rtl_harness.synth as synth_module

    real_run = subprocess.run
    calls: list[int] = []

    def _counting_run(*args, **kwargs):
        calls.append(1)
        return real_run(*args, **kwargs)

    monkeypatch.setattr(synth_module.subprocess, "run", _counting_run)

    with ToolResultCache(tmp_path / "cache.db") as cache:
        r1 = synthesize_and_measure(_ADDER, "Adder2", cache=cache)
        r2 = synthesize_and_measure(_ADDER, "Adder2", cache=cache)

    assert len(calls) == 1  # the real Yosys binary only ran once
    assert r1.total_cells == r2.total_cells > 0
    assert r1.cells_by_type == r2.cells_by_type


def test_real_cache_miss_for_different_source_still_runs_real_yosys(tmp_path, monkeypatch):
    """A different module forces a second Yosys run."""
    import flux_codegen_rtl_harness.synth as synth_module

    real_run = subprocess.run
    calls: list[int] = []

    def _counting_run(*args, **kwargs):
        calls.append(1)
        return real_run(*args, **kwargs)

    monkeypatch.setattr(synth_module.subprocess, "run", _counting_run)

    with ToolResultCache(tmp_path / "cache.db") as cache:
        synthesize_and_measure(_ADDER, "Adder2", cache=cache)
        synthesize_and_measure(_MORE_COMPLEX, "Adder2", cache=cache)

    assert len(calls) == 2


def test_a_real_synthesis_error_is_never_cached(tmp_path, monkeypatch):
    """Only a success is cached; a failure is retried."""
    import flux_codegen_rtl_harness.synth as synth_module

    real_run = subprocess.run
    calls: list[int] = []

    def _counting_run(*args, **kwargs):
        calls.append(1)
        return real_run(*args, **kwargs)

    monkeypatch.setattr(synth_module.subprocess, "run", _counting_run)

    with ToolResultCache(tmp_path / "cache.db") as cache:
        for _ in range(2):
            with pytest.raises(SynthesisError):
                synthesize_and_measure("module Broken(); not valid syntax here endmodule", "Broken", cache=cache)

    assert len(calls) == 2  # a failure is never served from cache


def test_real_cache_persists_across_separate_cache_instances(tmp_path, monkeypatch):
    """A fresh ToolResultCache on the same db_path serves the earlier result."""
    import flux_codegen_rtl_harness.synth as synth_module

    real_run = subprocess.run
    calls: list[int] = []

    def _counting_run(*args, **kwargs):
        calls.append(1)
        return real_run(*args, **kwargs)

    monkeypatch.setattr(synth_module.subprocess, "run", _counting_run)

    db_path = tmp_path / "cache.db"
    with ToolResultCache(db_path) as cache:
        synthesize_and_measure(_ADDER, "Adder2", cache=cache)
    with ToolResultCache(db_path) as cache:
        synthesize_and_measure(_ADDER, "Adder2", cache=cache)

    assert len(calls) == 1
