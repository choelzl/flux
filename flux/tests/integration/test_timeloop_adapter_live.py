"""Runs the real Timeloop+Accelergy Docker image end to end through the Flux Evaluator ABI.
Requires a working `docker` daemon; pulls timeloopaccelergy/accelergy-timeloop-infrastructure on
first run. Slow (real LOMA-equivalent mapper search inside a container) — integration, not unit.
"""

from __future__ import annotations

from pathlib import Path

import flux_ir
import pytest
from flux_evaluator_abi import Budget, Candidate, Method
from flux_evaluator_timeloop import NotExpressibleError, TimeloopEvaluator

FLUX_ROOT = Path(__file__).resolve().parents[2]
GEMM_WORKLOAD = FLUX_ROOT / "ir/workload/examples/mlp-gemm0.yaml"


@pytest.fixture(scope="module")
def evaluator() -> TimeloopEvaluator:
    return TimeloopEvaluator()


def test_gemm_workload_evaluates_through_real_timeloop(evaluator):
    workload = flux_ir.load_document(GEMM_WORKLOAD)
    candidate = Candidate(workload=workload, arch=None, mapping=None)

    result = evaluator.evaluate(
        candidate, Budget(), frozenset({"latency_cycles", "energy_pj", "area_mm2"})
    )

    # Real numbers for this exact (workload, vendored reference/ bundle) pair — pinned so a
    # future Docker image update or an accidental adapter/reference change is caught.
    assert result.metrics["latency_cycles"].value == pytest.approx(512.0)
    assert result.metrics["energy_pj"].value == pytest.approx(100000.0, rel=1e-6)
    for estimate in result.metrics.values():
        assert estimate.method == Method.ANALYTIC
        assert estimate.ci_low == estimate.ci_high == estimate.value  # v0.1: point estimate only

    assert result.provenance.evaluator.startswith("timeloop-docker@")
    assert result.provenance.inputs["workload_hash"] == flux_ir.content_hash(workload)
    assert result.bottleneck.per_level_utilisation["pe_array"] == pytest.approx(1.0)


def test_non_einsum_workload_raises_not_expressible_before_touching_docker(evaluator):
    dma_workload = flux_ir.load_document(FLUX_ROOT / "ir/workload/examples/soc-dma-desc-fetch.yaml")
    candidate = Candidate(workload=dma_workload, arch=None, mapping=None)

    with pytest.raises(NotExpressibleError):
        evaluator.evaluate(candidate, Budget(), frozenset({"latency_cycles"}))


def test_unrecognised_string_arch_is_rejected_rather_than_silently_ignored(evaluator):
    """A dict Candidate.arch is now legitimately routed through architecture_translator.py (see
    tests/integration/test_timeloop_architecture_translation_live.py) — this covers the
    remaining case, an arch reference that's neither None nor a translatable dict."""
    workload = flux_ir.load_document(GEMM_WORKLOAD)
    candidate = Candidate(workload=workload, arch="/some/other/accelerator.yaml", mapping=None)

    with pytest.raises(NotExpressibleError, match="only accepts Candidate.arch"):
        evaluator.evaluate(candidate, Budget(), frozenset({"latency_cycles"}))
