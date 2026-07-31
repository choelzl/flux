"""The controlled version of test_cross_evaluator_report.py: same Flux Workload IR document
*and* same Flux Architecture IR document, through both real backends. See
docs/phase1-exit-criterion-report.md's "Update: a genuinely controlled comparison" section for
the full write-up, including why the resulting numbers still shouldn't be over-interpreted.
"""

from __future__ import annotations

from pathlib import Path

import flux_ir
import pytest
from flux_evaluator_abi import Budget, Candidate
from flux_evaluator_timeloop import TimeloopEvaluator
from flux_evaluator_zigzag import ZigZagEvaluator

FLUX_ROOT = Path(__file__).resolve().parents[2]
GEMM_WORKLOAD = FLUX_ROOT / "ir/workload/examples/mlp-gemm0.yaml"
SIMPLE_NPU_1D = FLUX_ROOT / "ir/architecture/examples/simple-npu-1d-v1.yaml"


def test_same_workload_and_architecture_through_both_real_backends(capsys):
    workload = flux_ir.load_document(GEMM_WORKLOAD)
    arch = flux_ir.load_document(SIMPLE_NPU_1D)
    workload_hash = flux_ir.content_hash(workload)
    arch_hash = flux_ir.content_hash(arch)
    candidate = Candidate(workload=workload, arch=arch, mapping=None)

    zigzag_result = ZigZagEvaluator().evaluate(
        candidate, Budget(), frozenset({"latency_cycles", "energy_pj"})
    )
    timeloop_result = TimeloopEvaluator().evaluate(
        candidate, Budget(), frozenset({"latency_cycles", "energy_pj", "area_mm2"})
    )

    # The controlled-comparison claim, checked, not assumed: both backends report having seen
    # the exact same workload AND the exact same architecture.
    for result in (zigzag_result, timeloop_result):
        assert result.provenance.inputs["workload_hash"] == workload_hash
        assert result.provenance.inputs["accelerator"] == f"translated:{arch_hash}"

    with capsys.disabled():
        print("\n--- Controlled cross-evaluator report (same workload + same architecture) ---")
        print(f"workload_hash: {workload_hash}")
        print(f"arch_hash:     {arch_hash}")
        print(
            f"ZigZag   ({zigzag_result.provenance.evaluator}): "
            f"energy_pj={zigzag_result.metrics['energy_pj'].value} "
            f"latency_cycles={zigzag_result.metrics['latency_cycles'].value}"
        )
        print(
            f"Timeloop ({timeloop_result.provenance.evaluator}): "
            f"energy_pj={timeloop_result.metrics['energy_pj'].value} "
            f"latency_cycles={timeloop_result.metrics['latency_cycles'].value} "
            f"area_mm2={timeloop_result.metrics['area_mm2'].value}"
        )
        print(
            "NOTE: energy_pj is now within the same order of magnitude for both backends "
            "(evaluators/zigzag's translator anchors per-memory cost to ZigZag's own bundled "
            "tpu_like.yaml reference values instead of a flat placeholder — see "
            "docs/calibration-report.md) but is still not a validated comparison: neither "
            "number is checked against silicon. latency_cycles remains the more interesting "
            "signal: the theoretical compute-bound minimum for this workload on an 8-wide array "
            f"is {4 * 32 * 32 / 8:.0f} cycles (Timeloop's mapper found exactly that; ZigZag's "
            "LOMA search found 1554, and re-running at lpf_limit=6/12/20 changed nothing, ruling "
            "out search budget as the cause) — see docs/phase1-exit-criterion-report.md for the "
            "full analysis, including the still-open leading hypothesis."
        )
