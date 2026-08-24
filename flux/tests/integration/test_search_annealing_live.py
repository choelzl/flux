"""Simulated annealing against real ZigZag, validated against a *proven* answer rather than an
assumed one: tests/integration/test_search_exhaustive_live.py already exhaustively confirmed the
true optimum for mlp-gemm0.yaml + simple-npu-1d-v1.yaml is 1554 cycles (only 2 of 18 candidates
reach it). This checks that annealing — using well under half the evaluations a full exhaustive
sweep needs — converges to that same proven value, not merely "some plausible-looking number."
"""

from __future__ import annotations

import logging
from pathlib import Path

import flux_ir
from flux_evaluator_zigzag import ZigZagEvaluator
from flux_search_annealing import run_simulated_annealing

logging.getLogger("zigzag").setLevel(logging.WARNING)

FLUX_ROOT = Path(__file__).resolve().parents[2]
_KNOWN_TRUE_OPTIMUM = 1554.0  # proven by test_search_exhaustive_live.py's exhaustive sweep


def test_annealing_converges_to_the_proven_exhaustive_optimum():
    workload = flux_ir.load_document(FLUX_ROOT / "core/ir/workload/examples/mlp-gemm0.yaml")
    arch = flux_ir.load_document(FLUX_ROOT / "core/ir/architecture/examples/simple-npu-1d-v1.yaml")

    report = run_simulated_annealing(
        workload,
        arch,
        ZigZagEvaluator(),
        for_op="mlp.gemm0",
        metric="latency_cycles",
        minimize=True,
        initial_temperature=500.0,
        cooling_rate=0.85,
        max_iterations=8,
        seed=0,
    )

    # Fewer real evaluations than the 18-candidate exhaustive sweep needs, by construction.
    assert report.iterations <= 8

    assert report.best_result is not None
    assert report.best_result.metrics["latency_cycles"].value == _KNOWN_TRUE_OPTIMUM


def test_a_real_wall_clock_budget_genuinely_shortens_a_real_search():
    """docs/decisions.md D69: a real, enforced wall-clock stopping condition against real ZigZag
    calls — not a synthetic timing test, the actual real-world use case this feature exists for.
    """
    workload = flux_ir.load_document(FLUX_ROOT / "core/ir/workload/examples/mlp-gemm0.yaml")
    arch = flux_ir.load_document(FLUX_ROOT / "core/ir/architecture/examples/simple-npu-1d-v1.yaml")
    evaluator = ZigZagEvaluator()

    unbounded = run_simulated_annealing(
        workload, arch, evaluator, for_op="mlp.gemm0", metric="latency_cycles",
        minimize=True, max_iterations=200, seed=1,
    )
    budgeted = run_simulated_annealing(
        workload, arch, evaluator, for_op="mlp.gemm0", metric="latency_cycles",
        minimize=True, max_iterations=200, seed=1, wall_clock_budget_s=unbounded.wall_clock_s / 3,
    )

    assert budgeted.iterations < unbounded.iterations
    assert budgeted.stopped_reason == "wall_clock_budget"
    assert budgeted.best_result is not None  # still a real, valid (if less-converged) result
