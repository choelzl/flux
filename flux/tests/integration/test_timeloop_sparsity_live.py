"""Sparsity-aware evaluation via Timeloop's `sparse_optimizations`/`densities` (D78). Runs on
either Timeloop runner (D206). Slow (a mapper search, twice).
"""

from __future__ import annotations

from pathlib import Path

import flux_ir
import pytest
from flux_evaluator_abi import Budget, Candidate
from flux_evaluator_timeloop import NotExpressibleError, TimeloopEvaluator

FLUX_ROOT = Path(__file__).resolve().parents[2]
DENSE_WORKLOAD = FLUX_ROOT / "core/ir/workload/examples/mlp-gemm0.yaml"
DENSE_ARCH = FLUX_ROOT / "core/ir/architecture/examples/simple-npu-1d-v1.yaml"
SPARSE_WORKLOAD = FLUX_ROOT / "core/ir/workload/examples/mlp-gemm0-sparse-v1.yaml"
SPARSE_ARCH = FLUX_ROOT / "core/ir/architecture/examples/simple-npu-1d-sparse-v1.yaml"


@pytest.fixture(scope="module")
def evaluator() -> TimeloopEvaluator:
    return TimeloopEvaluator()


def test_dense_baseline_matches_the_already_established_real_numbers(evaluator):
    """The dense path gives the numbers flux/README.md cites for this pair, unperturbed by sparsity support."""
    workload = flux_ir.load_document(DENSE_WORKLOAD)
    arch = flux_ir.load_document(DENSE_ARCH)
    result = evaluator.evaluate(
        Candidate(workload=workload, arch=arch, mapping=None), Budget(), frozenset(),
    )
    assert result.metrics["latency_cycles"].value == pytest.approx(512.0)
    assert result.metrics["energy_pj"].value == pytest.approx(620000.0, rel=1e-6)


def test_real_sparsity_gives_a_real_physically_correct_reduction(evaluator):
    """A declared 0.25 density on input activations, gated at gbuf, gives 4x fewer cycles and
    ~2.5x less energy (hand-verified, D78); pinned exactly.
    """
    workload = flux_ir.load_document(SPARSE_WORKLOAD)
    arch = flux_ir.load_document(SPARSE_ARCH)
    result = evaluator.evaluate(
        Candidate(workload=workload, arch=arch, mapping=None), Budget(), frozenset(),
    )
    assert result.metrics["latency_cycles"].value == pytest.approx(128.0)
    assert result.metrics["energy_pj"].value == pytest.approx(250000.0, rel=1e-6)

    # the direction relative to the dense baseline, checked directly
    assert result.metrics["latency_cycles"].value < 512.0
    assert result.metrics["energy_pj"].value < 620000.0


def test_a_multi_op_workload_declaring_sparsity_is_rejected(evaluator):
    workload = flux_ir.load_document(SPARSE_WORKLOAD)
    workload["ops"] = workload["ops"] * 2  # two ops, both declaring sparsity
    workload["ops"][1] = dict(workload["ops"][1], id="mlp.gemm0-sparse-2")
    arch = flux_ir.load_document(SPARSE_ARCH)
    with pytest.raises(NotExpressibleError, match="multi-op workloads"):
        evaluator.evaluate(Candidate(workload=workload, arch=arch, mapping=None), Budget(), frozenset())


def test_declared_sparsity_with_no_matching_hardware_optimization_is_a_real_inert_no_op(evaluator):
    """A density with no architecture-level sparse_optimizations gives results identical to dense:
    Timeloop treats unconsumed density metadata as inert.
    """
    workload = flux_ir.load_document(SPARSE_WORKLOAD)
    arch = flux_ir.load_document(DENSE_ARCH)  # no sparse_optimizations declared anywhere
    result = evaluator.evaluate(
        Candidate(workload=workload, arch=arch, mapping=None), Budget(), frozenset(),
    )
    assert result.metrics["latency_cycles"].value == pytest.approx(512.0)
    assert result.metrics["energy_pj"].value == pytest.approx(620000.0, rel=1e-6)
