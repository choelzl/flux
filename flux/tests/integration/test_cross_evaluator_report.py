"""The same Flux Workload IR document through two independent, real backends. The backends target
different reference architectures, so the numbers are not a controlled comparison (see
test_cross_evaluator_same_architecture_report.py for that)."""

from __future__ import annotations

from pathlib import Path

import flux_ir
from flux_evaluator_abi import Budget, Candidate
from flux_evaluator_timeloop import TimeloopEvaluator
from flux_evaluator_zigzag import ZigZagEvaluator

FLUX_ROOT = Path(__file__).resolve().parents[2]
GEMM_WORKLOAD = FLUX_ROOT / "core/ir/workload/examples/mlp-gemm0.yaml"


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

    # Both backends saw the exact same content-hashed document, per their own provenance.
    assert zigzag_result.provenance.inputs["workload_hash"] == workload_hash
    assert timeloop_result.provenance.inputs["workload_hash"] == workload_hash

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
            "Architecture IR translation not yet implemented) — do not read these numbers as a "
            "controlled cost-model disagreement."
        )
