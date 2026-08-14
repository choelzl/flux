"""Real end-to-end sparsity-aware evaluation via Timeloop's own real `sparse_optimizations`/
`densities` mechanism (docs/decisions.md D78). Runs on either Timeloop runner (docs/decisions.md D206):
the `timeloopaccelergy/accelergy-timeloop-infrastructure` image `test_timeloop_adapter_live.py`
already pulls, or the hermetic build under `nix develop .#timeloop`. Slow (a real mapper search,
twice) — integration, not unit.
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
    """The exact real numbers flux/README.md's own Phase 1 write-up already cites for this exact
    (translated-architecture) pair — a real cross-check, not a newly-invented pin, confirming
    this decision's own sparsity changes didn't perturb the unrelated dense path at all."""
    workload = flux_ir.load_document(DENSE_WORKLOAD)
    arch = flux_ir.load_document(DENSE_ARCH)
    result = evaluator.evaluate(
        Candidate(workload=workload, arch=arch, mapping=None), Budget(), frozenset(),
    )
    assert result.metrics["latency_cycles"].value == pytest.approx(512.0)
    assert result.metrics["energy_pj"].value == pytest.approx(620000.0, rel=1e-6)


def test_real_sparsity_gives_a_real_physically_correct_reduction(evaluator):
    """A real, declared 0.25 hypergeometric density on the input activation tensor, gated at
    gbuf — verified by hand (docs/decisions.md D78) before this translator was trusted: a real
    4x cycle count reduction and ~2.5x energy reduction, the physically correct direction, not
    assumed. Pinned exactly, not just "lower than dense", since the real numbers are already
    known and reproducible.
    """
    workload = flux_ir.load_document(SPARSE_WORKLOAD)
    arch = flux_ir.load_document(SPARSE_ARCH)
    result = evaluator.evaluate(
        Candidate(workload=workload, arch=arch, mapping=None), Budget(), frozenset(),
    )
    assert result.metrics["latency_cycles"].value == pytest.approx(128.0)
    assert result.metrics["energy_pj"].value == pytest.approx(250000.0, rel=1e-6)

    # The real, physically correct direction relative to the dense baseline above, checked
    # directly rather than only pinned in isolation.
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
    """Verified by hand before this translator was trusted: a real, declared density with no
    corresponding architecture-level sparse_optimizations block produces byte-identical results
    to the fully-dense baseline — Timeloop genuinely treats unconsumed density metadata as inert,
    not an error and not a silent (wrong) cost reduction.
    """
    workload = flux_ir.load_document(SPARSE_WORKLOAD)
    arch = flux_ir.load_document(DENSE_ARCH)  # no sparse_optimizations declared anywhere
    result = evaluator.evaluate(
        Candidate(workload=workload, arch=arch, mapping=None), Budget(), frozenset(),
    )
    assert result.metrics["latency_cycles"].value == pytest.approx(512.0)
    assert result.metrics["energy_pj"].value == pytest.approx(620000.0, rel=1e-6)
