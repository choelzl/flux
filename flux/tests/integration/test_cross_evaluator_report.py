"""The Phase 1 exit-criterion artifact (docs/05.md): the same Flux Workload IR document,
run through two independent, real backends. See docs/phase1-exit-criterion-report.md for the
full write-up and — importantly — what this test does *not* prove (the two backends target
different reference architectures, so the numbers below are not a controlled disagreement report
yet; see that doc's "What this does not yet prove" section).
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


def test_same_ir_document_evaluates_through_both_real_backends(capsys):
    workload = flux_ir.load_document(GEMM_WORKLOAD)
    workload_hash = flux_ir.content_hash(workload)
    candidate = Candidate(workload=workload, arch=None, mapping=None)

    zigzag_result = ZigZagEvaluator().evaluate(
        candidate, Budget(), frozenset({"latency_cycles", "energy_pj"})
    )
    timeloop_result = TimeloopEvaluator().evaluate(
        candidate, Budget(), frozenset({"latency_cycles", "energy_pj", "area_mm2"})
    )

    # The core claim of the IR/ABI contract: both backends saw the *exact* same content-hashed
    # document, not "close enough" — this is checked via each backend's own reported provenance,
    # not assumed.
    assert zigzag_result.provenance.inputs["workload_hash"] == workload_hash
    assert timeloop_result.provenance.inputs["workload_hash"] == workload_hash

    # Both results satisfy the same ABI shape despite coming from unrelated codebases.
    for result in (zigzag_result, timeloop_result):
        assert "energy_pj" in result.metrics
        assert "latency_cycles" in result.metrics
        assert result.validity.ok is True

    with capsys.disabled():
        print("\n--- Phase 1 exit-criterion cross-evaluator report ---")
        print(f"workload_hash: {workload_hash}")
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
            "NOTE: each backend targets its own fixed reference architecture (v0.1 limitation, "
            "Architecture IR translation not yet implemented) — see "
            "docs/phase1-exit-criterion-report.md before reading these numbers as a controlled "
            "cost-model disagreement."
        )
