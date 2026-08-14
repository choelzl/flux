"""Runs real Yosys synthesis through flux_codegen_rtl_harness.synth (docs/decisions.md D47) —
the first use of Yosys anywhere in this repo (a real, cherry-picked `.#default` nix package that
sat unused since `evaluators/rtl` first needed Verilator). Real cell counts, a real comparative
check that a more complex module synthesizes to more cells (not a hardcoded/fabricated result),
and real error handling for invalid Verilog.
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
    """A real, checked comparative property — not asserting a specific magic number, since exact
    cell counts can shift with Yosys version, but a genuinely more complex design (three
    arithmetic ops + a 2-level mux vs. one add) must synthesize to strictly more logic."""
    simple = synthesize_and_measure(_ADDER, "Adder2")
    complex_ = synthesize_and_measure(_MORE_COMPLEX, "Adder2")
    assert complex_.total_cells > simple.total_cells


def test_invalid_verilog_raises_synthesis_error():
    with pytest.raises(SynthesisError) as exc_info:
        synthesize_and_measure("module Broken(); not valid syntax here endmodule", "Broken")
    assert exc_info.value.returncode != 0


def test_wrong_top_module_name_raises_synthesis_error():
    """A real, checked failure mode distinct from a syntax error: valid Verilog, but `-top`
    names a module that doesn't exist in the source."""
    with pytest.raises(SynthesisError):
        synthesize_and_measure(_ADDER, "ThisModuleDoesNotExist")


def test_trailing_semicolon_after_endmodule_is_tolerated():
    """docs/decisions.md D61: a real LLM-generated module (found composing the toy CPU demo's
    `PcUnit` leaf) used `endmodule;` — Verilator silently tolerates this (the same source compiled
    and ran correctly through the real RTL harness's own Verilator path), but Yosys's own, stricter
    frontend rejected it outright with a raw syntax error before this fix. A real, previously-
    undiscovered tool-compatibility gap, only surfaced once real Yosys synthesis was tried against
    a design containing it — fixed by normalizing it away defensively, the same class of fix as
    the existing EOFNEWLINE handling."""
    source_with_trailing_semicolon = _ADDER.rstrip() + ";\n"
    assert "endmodule;" in source_with_trailing_semicolon  # sanity: the fixture actually has it
    result = synthesize_and_measure(source_with_trailing_semicolon, "Adder2")
    assert result.total_cells > 0


def test_real_cache_hit_skips_a_real_yosys_rerun(tmp_path, monkeypatch):
    """Real, content-hash-keyed caching (docs/decisions.md D89) — counted directly against the
    real `subprocess.run` call Yosys itself goes through, the same discipline D79/D86 used for
    real CACTI/ZigZag, not inferred from wall-clock time."""
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
    """Not over-broad: a genuinely different module must still force a real second Yosys run."""
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
    """A transient/real failure must not poison future calls with the same inputs — only a real
    success is ever stored."""
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

    assert len(calls) == 2  # both real calls happened — a failure was never served from cache


def test_real_cache_persists_across_separate_cache_instances(tmp_path, monkeypatch):
    """The real point of a disk-backed cache, not just an in-process one: a fresh ToolResultCache
    opened on the same db_path in a later call still serves the real, previously-computed
    result."""
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
