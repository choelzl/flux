"""`flux_store.CachingEvaluator` against real ZigZag (docs/search.md's warm-start surface):
proves the second identical call is served from the store rather than re-running ZigZag, by
timing both calls, not just trusting the mechanism — a real evaluation of this workload/arch
pair takes over a second; a cache hit is sub-millisecond.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

import flux_ir
from flux_evaluator_abi import Budget, Candidate
from flux_evaluator_zigzag import ZigZagEvaluator
from flux_store import CachingEvaluator, ResultStore

logging.getLogger("zigzag").setLevel(logging.WARNING)

FLUX_ROOT = Path(__file__).resolve().parents[2]
GEMM_WORKLOAD = FLUX_ROOT / "core/ir/workload/examples/mlp-gemm0.yaml"
SIMPLE_NPU_1D = FLUX_ROOT / "core/ir/architecture/examples/simple-npu-1d-v1.yaml"


def test_second_identical_call_is_served_from_the_store_not_a_real_zigzag_run(tmp_path):
    workload = flux_ir.load_document(GEMM_WORKLOAD)
    arch = flux_ir.load_document(SIMPLE_NPU_1D)
    candidate = Candidate(workload=workload, arch=arch, mapping=None)
    metrics = frozenset({"latency_cycles"})

    with ResultStore(tmp_path / "flux.db") as store:
        cached = CachingEvaluator(ZigZagEvaluator(), store, evaluator_prefix="zigzag")

        start = time.monotonic()
        first = cached.evaluate(candidate, Budget(), metrics)
        first_elapsed = time.monotonic() - start

        start = time.monotonic()
        second = cached.evaluate(candidate, Budget(), metrics)
        second_elapsed = time.monotonic() - start

        assert first.metrics["latency_cycles"].value == second.metrics["latency_cycles"].value
        # The proven 1554-cycle optimum this exact pair evaluates to throughout this repo.
        assert first.metrics["latency_cycles"].value == 1554.0
        assert cached.stats.hits == 1
        assert cached.stats.misses == 1
        # A real cache hit is at least an order of magnitude faster than a real ZigZag run —
        # loose enough not to be flaky, tight enough to prove it isn't re-running ZigZag.
        assert second_elapsed < first_elapsed / 10

        # The result really did land in the store under its real content hashes, independent of
        # CachingEvaluator's own bookkeeping.
        workload_hash = flux_ir.content_hash(workload)
        arch_hash = flux_ir.content_hash(arch)
        stored = store.find_results(
            workload_hash=workload_hash, arch_hash=arch_hash, evaluator_prefix="zigzag",
        )
        assert len(stored) == 1
        assert stored[0]["result"]["metrics"]["latency_cycles"]["value"] == 1554.0


def test_caching_evaluator_composes_with_a_second_real_backend_without_cross_contamination(tmp_path):
    """A ZigZag-wrapping CachingEvaluator and a Timeloop-wrapping one, sharing the same store and
    the same candidate, must never serve each other's results — the exact scenario
    `evaluator_prefix` exists to prevent, now checked against two real adapters, not stubs.
    """
    from flux_evaluator_timeloop import TimeloopEvaluator

    workload = flux_ir.load_document(GEMM_WORKLOAD)
    arch = flux_ir.load_document(SIMPLE_NPU_1D)
    candidate = Candidate(workload=workload, arch=arch, mapping=None)
    metrics = frozenset({"latency_cycles"})

    with ResultStore(tmp_path / "flux.db") as store:
        zigzag_cached = CachingEvaluator(ZigZagEvaluator(), store, evaluator_prefix="zigzag")
        timeloop_cached = CachingEvaluator(
            TimeloopEvaluator(), store, evaluator_prefix="timeloop"
        )

        zigzag_result = zigzag_cached.evaluate(candidate, Budget(), metrics)
        timeloop_result = timeloop_cached.evaluate(candidate, Budget(), metrics)

        # The real, already-established disagreement between the two backends on this exact pair
        # (docs/phase1-exit-criterion-report.md) — proves these are two genuinely different real
        # results, not one masquerading as the other.
        assert zigzag_result.metrics["latency_cycles"].value == 1554.0
        assert timeloop_result.metrics["latency_cycles"].value == 512.0

        # Re-querying each wrapper serves its own cached result, not the other's.
        zigzag_second = zigzag_cached.evaluate(candidate, Budget(), metrics)
        timeloop_second = timeloop_cached.evaluate(candidate, Budget(), metrics)
        assert zigzag_second.metrics["latency_cycles"].value == 1554.0
        assert timeloop_second.metrics["latency_cycles"].value == 512.0
        assert zigzag_cached.stats.hits == 1
        assert timeloop_cached.stats.hits == 1
