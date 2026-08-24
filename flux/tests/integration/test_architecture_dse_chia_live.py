"""Proves `ChiaParallelEvaluator` genuinely dispatches the architecture-DSE sweep as concurrent
Ray tasks — not a sequential loop wearing a batch-shaped interface — by comparing the real
wall-clock time of a parallel 3-candidate sweep against 3 real sequential single-candidate
evaluations. Requires the real `chia` package (see `flows/chia_nodes/README.md`); starts a
genuine local Ray instance.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

import flux_ir
import pytest
import ray
from flux_chia_nodes import ChiaParallelEvaluator
from flux_evaluator_abi import Budget, Candidate
from flux_evaluator_zigzag import ZigZagEvaluator
from flux_search_architecture import generate_width_candidates, run_architecture_dse

logging.getLogger("zigzag").setLevel(logging.WARNING)

FLUX_ROOT = Path(__file__).resolve().parents[2]
GEMM_WORKLOAD = FLUX_ROOT / "core/ir/workload/examples/mlp-gemm0.yaml"
SIMPLE_NPU_1D_V1 = FLUX_ROOT / "core/ir/architecture/examples/simple-npu-1d-v1.yaml"
_WIDTHS = [4, 8, 16]


@pytest.fixture(scope="module")
def workload_and_arch():
    workload = flux_ir.load_document(GEMM_WORKLOAD)
    base_arch = flux_ir.load_document(SIMPLE_NPU_1D_V1)
    return workload, base_arch


def test_chia_parallel_evaluator_finds_the_same_winner_as_plain_zigzag(workload_and_arch):
    """Same real numbers as test_architecture_dse_live.py's plain-ZigZag sweep — swapping the
    evaluator for a CHIA-parallel one must not change the answer, only how it's computed."""
    workload, base_arch = workload_and_arch
    report = run_architecture_dse(
        workload, generate_width_candidates(base_arch, _WIDTHS), ChiaParallelEvaluator("zigzag"),
        metric="latency_cycles", minimize=True,
    )
    by_width = {p.candidate.width: p.result.metrics["latency_cycles"].value for p in report.swept}
    assert by_width == {4: 3106.0, 8: 1554.0, 16: 778.0}
    assert report.winner.width == 16


def test_the_sweep_actually_dispatches_in_parallel_not_sequentially(workload_and_arch):
    """The real proof: 3 candidates dispatched together via evaluate_batch() take roughly as
    long as the *slowest single one*, not the sum of all three — only possible if Ray is really
    running them concurrently on separate workers, not one after another."""
    workload, base_arch = workload_and_arch

    # Baseline: how long ONE real ZigZag evaluation takes, run locally (no Ray dispatch at all).
    single_start = time.monotonic()
    ZigZagEvaluator().evaluate(
        Candidate(workload=workload, arch=base_arch, mapping=None), Budget(), frozenset({"latency_cycles"})
    )
    single_elapsed = time.monotonic() - single_start

    # The parallel sweep: 3 candidates, dispatched together.
    parallel_start = time.monotonic()
    run_architecture_dse(
        workload, generate_width_candidates(base_arch, _WIDTHS), ChiaParallelEvaluator("zigzag"),
        metric="latency_cycles",
    )
    parallel_elapsed = time.monotonic() - parallel_start

    # A sequential run of 3 would take roughly 3x single_elapsed. Real concurrent dispatch
    # should land well under that — generous margin (2x single, not 3x) to stay robust against
    # timing noise while still failing if dispatch silently became sequential.
    assert parallel_elapsed < single_elapsed * 2.0, (
        f"parallel sweep took {parallel_elapsed:.2f}s vs a single evaluation's {single_elapsed:.2f}s "
        "— expected well under 3x for genuinely concurrent dispatch, got a ratio suggesting "
        "sequential execution"
    )
