"""Runs Timeloop+Accelergy end to end through the Flux Evaluator ABI.

Either runner works (D206): a `docker` daemon, or the hermetic build from `nix develop` (linux)
with `FLUX_TIMELOOP_LOCAL=1`. The pinned numbers hold on both. Slow (a full mapper search).
"""

from __future__ import annotations

from pathlib import Path

import flux_ir
import pytest
from flux_evaluator_abi import Budget, Candidate, Method
from flux_evaluator_timeloop import NotExpressibleError, TimeloopEvaluator

FLUX_ROOT = Path(__file__).resolve().parents[2]
GEMM_WORKLOAD = FLUX_ROOT / "core/ir/workload/examples/mlp-gemm0.yaml"


@pytest.fixture(scope="module")
def evaluator() -> TimeloopEvaluator:
    return TimeloopEvaluator()


def test_gemm_workload_evaluates_through_real_timeloop(evaluator):
    workload = flux_ir.load_document(GEMM_WORKLOAD)
    candidate = Candidate(workload=workload, arch=None, mapping=None)

    result = evaluator.evaluate(
        candidate, Budget(), frozenset({"latency_cycles", "energy_pj", "area_mm2"})
    )

    # pinned for this (workload, vendored reference/ bundle) pair, to catch image or adapter changes
    assert result.metrics["latency_cycles"].value == pytest.approx(512.0)
    assert result.metrics["energy_pj"].value == pytest.approx(100000.0, rel=1e-6)
    for estimate in result.metrics.values():
        assert estimate.method == Method.ANALYTIC
        assert estimate.ci_low == estimate.ci_high == estimate.value  # v0.1: point estimate only

    # either runner is legitimate (D206), but provenance must name one by prefix so a replay can
    # tell them apart
    assert result.provenance.evaluator.startswith(("timeloop-docker@", "timeloop-nix@"))
    assert result.provenance.inputs["workload_hash"] == flux_ir.content_hash(workload)
    assert result.bottleneck.per_level_utilisation["pe_array"] == pytest.approx(1.0)


def test_non_einsum_workload_raises_not_expressible_before_touching_docker(evaluator):
    dma_workload = flux_ir.load_document(FLUX_ROOT / "core/ir/workload/examples/soc-dma-desc-fetch.yaml")
    candidate = Candidate(workload=dma_workload, arch=None, mapping=None)

    with pytest.raises(NotExpressibleError):
        evaluator.evaluate(candidate, Budget(), frozenset({"latency_cycles"}))


def test_unrecognised_string_arch_is_rejected_rather_than_silently_ignored(evaluator):
    """An arch reference that is neither None nor a translatable dict is rejected."""
    workload = flux_ir.load_document(GEMM_WORKLOAD)
    candidate = Candidate(workload=workload, arch="/some/other/accelerator.yaml", mapping=None)

    with pytest.raises(NotExpressibleError, match="only accepts Candidate.arch"):
        evaluator.evaluate(candidate, Budget(), frozenset({"latency_cycles"}))


FFN_WORKLOAD = FLUX_ROOT / "core/ir/workload/examples/mlp-ffn0.yaml"
SIMPLE_NPU_1D = FLUX_ROOT / "core/ir/architecture/examples/simple-npu-1d-v1.yaml"


def test_multi_op_workload_aggregates_across_real_separate_timeloop_runs(evaluator):
    """A multi-op workload runs one Timeloop invocation per op and sums them, matching each
    layer's separately run result (D62)."""
    workload = flux_ir.load_document(FFN_WORKLOAD)
    arch = flux_ir.load_document(SIMPLE_NPU_1D)
    candidate = Candidate(workload=workload, arch=arch, mapping=None)

    result = evaluator.evaluate(
        candidate, Budget(), frozenset({"latency_cycles", "energy_pj", "area_mm2"})
    )
    assert result.metrics["latency_cycles"].value == pytest.approx(512.0)
    assert result.metrics["energy_pj"].value == pytest.approx(630000.0, rel=1e-6)
    assert result.metrics["area_mm2"].value == pytest.approx(0.0)

    # re-derive the total from each layer's separately run result
    total_cycles = 0.0
    total_energy = 0.0
    for op in workload["ops"]:
        single_workload = dict(workload)
        single_workload["ops"] = [op]
        single_result = evaluator.evaluate(
            Candidate(workload=single_workload, arch=arch, mapping=None),
            Budget(), frozenset({"latency_cycles", "energy_pj"}),
        )
        total_cycles += single_result.metrics["latency_cycles"].value
        total_energy += single_result.metrics["energy_pj"].value
    assert total_cycles == pytest.approx(result.metrics["latency_cycles"].value)
    assert total_energy == pytest.approx(result.metrics["energy_pj"].value)


def test_multi_op_workload_with_explicit_mapping_is_rejected(evaluator):
    """An explicit mapping with a multi-op workload is ambiguous and rejected before running (D62)."""
    workload = flux_ir.load_document(FFN_WORKLOAD)
    arch = flux_ir.load_document(SIMPLE_NPU_1D)
    mapping = flux_ir.load_document(FLUX_ROOT / "core/ir/mapping/examples/mlp-gemm0-simple-npu-1d-map0.yaml")
    candidate = Candidate(workload=workload, arch=arch, mapping=mapping)

    with pytest.raises(NotExpressibleError, match="ambiguous"):
        evaluator.evaluate(candidate, Budget(), frozenset({"latency_cycles"}))
