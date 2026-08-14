"""Unit tests for `SequentialRtlReport`'s reporting contract (docs/decisions.md D118) — the part
that decides what counts as success, exercised without an LLM or Verilator by constructing the
real result objects directly. The end-to-end path has its own live test.
"""

from __future__ import annotations

import pytest
from flux_chia_nodes import SequentialRtlReport
from flux_chia_nodes.generate_rtl import GenerationResult
from flux_codegen_rtl_harness import HarnessRunResult
from flux_generation import derive_sequential_design

_WORKLOAD = {
    "schema_version": "0.1.0", "id": "test/gemm0",
    "ops": [{"id": "gemm0", "kind": "einsum", "expr": "B C, C K -> B K",
             "bounds": {"B": 4, "C": 32, "K": 32},
             "precision": {"I": 8, "W": 8, "O": 16, "O_final": 8}}],
}
_ARCH = {
    "schema_version": "0.1.0", "id": "test/arch8",
    "hierarchy": [{"level": "gbuf", "class": "memory", "attrs": {"size_kb": 512}},
                  {"level": "pe", "class": "compute", "attrs": {"dims": {"X": 8}}}],
}


def _generation(success: bool = True) -> GenerationResult:
    return GenerationResult(
        spec_id="seq-step/DerivedMacTile8", success=success, attempts=1,
        final_source="module DerivedMacTile8; endmodule", harness_result=None,
    )


def _harness(*, passed: bool, cycles: tuple[int, ...]) -> HarnessRunResult:
    return HarnessRunResult(
        compiled=True, compile_stderr=None, ran=True,
        total_vectors=1, passed_vectors=1 if passed else 0,
        vcd_path=None, vcd_nonempty=True, stdout="", stderr="",
        failing_vector_lines=() if passed else ("VECTOR 0 FAIL",),
        cycles_per_vector=cycles,
    )


def _report(**kwargs) -> SequentialRtlReport:
    return SequentialRtlReport(derived=derive_sequential_design(_WORKLOAD, _ARCH), **kwargs)


def test_a_right_answer_at_the_wrong_latency_is_not_a_success():
    """The contract this node exists for. A design that passes its golden vectors but takes a
    different number of cycles than the schedule predicted is unusable as a reference — reporting
    it as a pass would hide exactly the discrepancy worth knowing about."""
    r = _report(generation=_generation(), harness=_harness(passed=True, cycles=(9,)))

    assert r.predicted_cycles == 4 and r.measured_cycles == 9
    assert r.harness.all_passed          # correctness alone: fine
    assert not r.latency_matches_prediction
    assert not r.success                 # ...but the report as a whole is not


def test_success_needs_both_halves():
    r = _report(generation=_generation(), harness=_harness(passed=True, cycles=(4,)))
    assert r.latency_matches_prediction and r.success

    wrong_answer = _report(generation=_generation(), harness=_harness(passed=False, cycles=(4,)))
    assert wrong_answer.latency_matches_prediction   # right latency...
    assert not wrong_answer.success                  # ...wrong result


def test_a_tile_that_never_verified_reports_no_measurement_rather_than_zero():
    """`None`, not 0 — 0 cycles is a legitimate measurement and "nothing was composed" is not,
    the same distinction `HarnessRunResult.total_cycles` already makes."""
    r = _report(generation=_generation(success=False), harness=None)

    assert r.measured_cycles is None
    assert not r.latency_matches_prediction and not r.success
    assert r.to_dict()["measured_cycles"] is None


def test_a_composition_failure_is_named_separately_from_a_generation_failure():
    """A tile that verified standalone but won't compose is an interface mismatch, not a
    behavioural error — folding the two together would point a caller at the wrong thing."""
    r = _report(generation=_generation(), harness=None, compose_error="%Error: port a9 not found")

    assert not r.success
    assert r.generation.success                      # the tile itself was fine
    assert "port a9" in r.to_dict()["compose_error"]


def test_the_report_round_trips_to_plain_data():
    d = _report(generation=_generation(), harness=_harness(passed=True, cycles=(4,))).to_dict()

    assert d["success"] is True and d["predicted_cycles"] == 4 and d["measured_cycles"] == 4
    assert d["derived"]["lanes"] == 8 and d["derived"]["steps"] == 4
    # The wrapper travels with the report, so a caller can re-verify the composition themselves.
    assert d["derived"]["wrapper_source"].startswith("module DerivedSeqMac8x4")
    assert d["harness"]["cycles_per_vector"] == [4]


@pytest.mark.parametrize("cycles", [(), (4, 4)])
def test_an_unmeasured_or_multi_vector_run_does_not_silently_pass(cycles):
    """`total_cycles` sums across vectors, so a spec with two vectors would double the count —
    guarding here rather than trusting every future caller to keep one vector."""
    r = _report(generation=_generation(), harness=_harness(passed=True, cycles=cycles))
    assert not r.success
