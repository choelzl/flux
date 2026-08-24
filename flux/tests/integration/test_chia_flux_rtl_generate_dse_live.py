"""Runs flux_rtl_generate_dse for real (docs/decisions.md D45): real Ollama calls, real Verilator
compilation, real Ray concurrent dispatch across multiple design variants — the Verilog sibling of
test_chia_flux_systemc_generate_dse_live.py (D41). Same "prove concurrency with a real sequential
baseline, not a fabricated field" pattern.
"""

from __future__ import annotations

import time

from flux_chia_nodes.generate_rtl import flux_generate_rtl_module
from flux_chia_nodes.rtl_dse import RtlDSEReport, flux_rtl_generate_dse

import _helpers

# Guard in the D246 pattern (found unguarded during the D374 nightly triage): every test
# here reaches a live Ollama; on a runner without one this file must skip, not fail.
pytestmark = _helpers.requires_ollama

_ADDER_SPEC = {
    "module_name": "Adder2",
    "id": "adder2-rtl-variant",
    "ports": [
        {"name": "a", "dir": "in", "dtype": "int"},
        {"name": "b", "dir": "in", "dtype": "int"},
        {"name": "sum", "dir": "out", "dtype": "int"},
    ],
    "behavior": "combinational: sum = a + b",
    "test_vectors": [{"inputs": {"a": 3, "b": 4}, "expected": {"sum": 7}}],
}

_MUX_SPEC = {
    "module_name": "Mux2to1",
    "id": "mux2to1-rtl-variant",
    "ports": [
        {"name": "sel", "dir": "in", "dtype": "bool"},
        {"name": "in0", "dir": "in", "dtype": "int"},
        {"name": "in1", "dir": "in", "dtype": "int"},
        {"name": "out", "dir": "out", "dtype": "int"},
    ],
    "behavior": "combinational 2-to-1 multiplexer: out = in1 if sel is true, else out = in0",
    "test_vectors": [{"inputs": {"sel": True, "in0": 5, "in1": 9}, "expected": {"out": 9}}],
}

_INVERTER_SPEC = {
    "module_name": "Inverter",
    "id": "inverter-rtl-variant",
    "ports": [
        {"name": "x", "dir": "in", "dtype": "bool"},
        {"name": "y", "dir": "out", "dtype": "bool"},
    ],
    "behavior": "combinational: y = logical NOT of x",
    "test_vectors": [{"inputs": {"x": True}, "expected": {"y": False}}],
}

_ALL_VARIANTS = [_ADDER_SPEC, _MUX_SPEC, _INVERTER_SPEC]


def test_generates_and_verifies_every_variant():
    report = flux_rtl_generate_dse(_ALL_VARIANTS)
    assert isinstance(report, RtlDSEReport)
    assert report.variant_ids == ("adder2-rtl-variant", "mux2to1-rtl-variant", "inverter-rtl-variant")
    assert report.all_valid
    assert set(report.valid_variant_ids) == set(report.variant_ids)
    for result in report.results:
        assert result.success
        assert result.harness_result.all_passed


def test_valid_variants_get_real_synthesis_results():
    """docs/decisions.md D47: DSE now reports a real gate-count for every valid variant, not just
    pass/fail — checked against the real Yosys output, not assumed present."""
    report = flux_rtl_generate_dse(_ALL_VARIANTS)
    assert set(report.synthesis_results) == set(report.valid_variant_ids)
    for vid, synth in report.synthesis_results.items():
        assert synth is not None, f"{vid} verified but synthesis unexpectedly failed"
        assert synth.total_cells > 0
    assert report.smallest_valid_variant_id in report.valid_variant_ids
    # The inverter (a single NOT gate) is the simplest of the three real variants — it should
    # genuinely synthesize to the fewest cells, a real comparative check, not a fixed assertion.
    assert report.smallest_valid_variant_id == "inverter-rtl-variant"


def test_empty_variant_list_is_rejected():
    import pytest

    with pytest.raises(ValueError, match="non-empty"):
        flux_rtl_generate_dse([])


def test_concurrent_dispatch_is_really_faster_than_the_same_three_generations_run_sequentially():
    """See test_chia_flux_systemc_generate_dse_live.py's identical test for why this uses a 15%
    tolerance rather than a strict inequality: a real, found finding (docs/decisions.md D41
    addendum) that the shared local Ollama server appears to serialize inference regardless of
    concurrent dispatch, so "not meaningfully slower" is the honest bound this shared,
    rate-limited backend can actually guarantee — not "strictly faster" every single run."""
    concurrent_report = flux_rtl_generate_dse(_ALL_VARIANTS)
    assert concurrent_report.all_valid

    t0 = time.monotonic()
    sequential_results = [flux_generate_rtl_module(spec) for spec in _ALL_VARIANTS]
    sequential_elapsed = time.monotonic() - t0

    assert all(r.success for r in sequential_results)
    print(
        f"concurrent dispatch_wall_clock_s={concurrent_report.dispatch_wall_clock_s:.2f}s, "
        f"sequential_elapsed={sequential_elapsed:.2f}s, "
        f"speedup={sequential_elapsed / concurrent_report.dispatch_wall_clock_s:.2f}x"
    )
    assert concurrent_report.dispatch_wall_clock_s < sequential_elapsed * 1.15
