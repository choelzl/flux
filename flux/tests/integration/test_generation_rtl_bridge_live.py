"""Live integration test for the architecture→RTL bridge (docs/decisions.md D100): real local
Ollama implements the derived spec, real Verilator verifies it against the golden vectors the
bridge computed — from an accepted Architecture IR to verified RTL with no caller-authored spec.
Requires the full dev shell (Verilator) and a running local Ollama server, same gating as every
other generation live test.
"""

from __future__ import annotations

import shutil
import urllib.request

import pytest

import _helpers

pytestmark = pytest.mark.skipif(
    shutil.which("verilator") is None, reason="verilator not on PATH (needs .#default dev shell)"
)




_WORKLOAD = {
    "schema_version": "0.1.0",
    "id": "test/gemm0",
    "ops": [
        {"id": "gemm0", "kind": "einsum", "expr": "B C, C K -> B K",
         "bounds": {"B": 4, "C": 32, "K": 32}, "precision": {"I": 8, "W": 8, "O": 16, "O_final": 8}},
    ],
}

# Width 4 keeps the prompt small and the module simple — the point is the real end-to-end path,
# not stressing the LLM with a wide interface.
_ARCH = {
    "schema_version": "0.1.0",
    "id": "test/arch4",
    "hierarchy": [
        {"level": "gbuf", "class": "memory", "attrs": {"size_kb": 256}},
        {"level": "pe_array", "class": "compute", "attrs": {"dims": {"X": 4}}},
    ],
}


@_helpers.requires_ollama
def test_architecture_to_verified_rtl_end_to_end():
    from flux_chia_nodes import flux_generate_rtl_for_architecture

    report = flux_generate_rtl_for_architecture(_WORKLOAD, _ARCH, n_vectors=4)

    # The deterministic half: the derived spec matches the candidate's own width.
    assert report.derived.lanes == 4
    assert report.derived.spec["module_name"] == "DerivedMac4"

    # The real LLM+Verilator half: a genuinely generated, genuinely verified module.
    assert report.success, f"generation failed after {report.generation.attempts} attempts:\n" + "\n".join(
        report.generation.transcript[-2:]
    )
    hr = report.generation.harness_result
    assert hr is not None and hr.all_passed
    assert hr.total_vectors == 4 and hr.passed_vectors == 4
    assert "module" in report.generation.final_source  # real Verilog source came back


@_helpers.requires_ollama
def test_architecture_to_verified_sequential_rtl_end_to_end():
    """The D117/D118 node end to end: only the combinational tile is LLM-written, the schedule is
    derived from the candidate pair, and the composed design's measured cycle count is checked
    against the prediction made before anything was built."""
    from flux_chia_nodes import flux_generate_sequential_rtl_for_architecture

    report = flux_generate_sequential_rtl_for_architecture(_WORKLOAD, _ARCH)

    # Derived, deterministically, from both documents: 32-long reduction at 4 lanes.
    assert (report.derived.lanes, report.derived.steps) == (4, 8)
    assert report.predicted_cycles == 8

    # What the LLM was asked for carries no protocol at all — the whole point of the split.
    leaf_ports = {p["name"] for p in report.derived.leaf_spec["ports"]}
    assert leaf_ports.isdisjoint({"clk", "rst_n", "start", "done"})

    assert report.success, (
        f"compose_error={report.compose_error}; "
        f"generation attempts={report.generation.attempts}\n"
        + "\n".join(report.generation.transcript[-2:])
    )
    assert report.measured_cycles == report.predicted_cycles
    assert report.harness is not None and report.harness.all_passed
