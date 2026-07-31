"""Real architecture design-space exploration (docs/00-decisions.md D5): sweep
mlp-gemm0.yaml across array widths 4/8/16 through real ZigZag, then escalate the winner through
real SystemC and real RTL — the analytic→coarse-sim→RTL cascade Phase 4 calls for, minus the
synthesis rung (still blocked on tooling, see evaluators/hammer/README.md).

The interesting, honest result (found by actually running this, not assumed): ZigZag's screening
picks the right *winner* (width=16 is fastest by every rung), but its absolute number doesn't
match real hardware — a further data point in this repo's own well-documented ZigZag
overestimation bias (docs/calibration-report.md, docs/phase1-exit-criterion-report.md). SystemC
and RTL, on the other hand, agree with each other *exactly* — that agreement, not agreement with
the analytic screening, is the actual point of a coarse-grain pre-check rung.
"""

from __future__ import annotations

import logging
from pathlib import Path

import flux_ir
import pytest
from flux_evaluator_rtl import RTLEvaluator
from flux_evaluator_systemc import SystemCEvaluator
from flux_evaluator_zigzag import ZigZagEvaluator
from flux_search_architecture import run_architecture_dse

logging.getLogger("zigzag").setLevel(logging.WARNING)

FLUX_ROOT = Path(__file__).resolve().parents[2]
GEMM_WORKLOAD = FLUX_ROOT / "ir/workload/examples/mlp-gemm0.yaml"
SIMPLE_NPU_1D_V1 = FLUX_ROOT / "ir/architecture/examples/simple-npu-1d-v1.yaml"


@pytest.fixture(scope="module")
def report():
    workload = flux_ir.load_document(GEMM_WORKLOAD)
    base_arch = flux_ir.load_document(SIMPLE_NPU_1D_V1)
    return run_architecture_dse(
        workload, base_arch, ZigZagEvaluator(),
        widths=[4, 8, 16], metric="latency_cycles", minimize=True,
        escalation_evaluators=[("systemc", SystemCEvaluator()), ("rtl", RTLEvaluator())],
    )


def test_every_width_is_evaluated_successfully(report):
    assert len(report.swept) == 3
    assert all(p.error is None for p in report.swept)


def test_zigzag_screening_ranks_wider_as_strictly_faster(report):
    """Real ZigZag numbers: width halves latency each step for this workload (3106 -> 1554 ->
    778) — a monotonic ranking, which is what makes width=16 the unambiguous winner."""
    by_width = {p.candidate.width: p.result.metrics["latency_cycles"].value for p in report.swept}
    assert by_width[4] > by_width[8] > by_width[16]


def test_winner_is_the_widest_architecture(report):
    assert report.winner is not None
    assert report.winner.width == 16


def test_escalation_ran_both_rungs_on_only_the_winner(report):
    assert [step.rung for step in report.escalation] == ["systemc", "rtl"]
    assert all(step.result is not None for step in report.escalation)


def test_systemc_and_rtl_agree_with_each_other_exactly(report):
    """The actual point of the coarse-grain rung: independent confirmation from a completely
    different simulator, not agreement with the analytic screening (see next test)."""
    systemc_value = report.escalation[0].result.metrics["latency_cycles"].value
    rtl_value = report.escalation[1].result.metrics["latency_cycles"].value
    assert systemc_value == rtl_value == 265.0


def test_zigzag_screening_does_not_match_real_hardware_a_known_gap_not_a_bug(report):
    """Documents, rather than hides, a real finding: ZigZag's screening estimate (778 cycles) is
    ~2.9x the real measured value (265) — consistent with this repo's already-established ZigZag
    overestimation bias, not a defect introduced by this DSE loop. `winner` selection is still
    correct (width=16 wins by every rung); only the *absolute* screening number is miscalibrated
    — precisely why `calibration/` and this escalation cascade both exist."""
    assert report.escalation_agrees_with_screening(tolerance=50.0) is False
    screening_value = report.winner_screening_result.metrics["latency_cycles"].value
    real_value = report.escalation[-1].result.metrics["latency_cycles"].value
    assert screening_value / real_value == pytest.approx(2.94, rel=0.02)
