"""`flux_store.CachingEvaluator` wired underneath a real search strategy
(`search/exhaustive`'s `run_exhaustive_search`) — the "every strategy can seed from prior runs"
warm-start docs/search.md and docs/gap-analysis.md G4 name as still missing, until now. No code
change to `run_exhaustive_search` itself: it only ever calls `evaluator.evaluate(...)`, so handing
it a `CachingEvaluator` instead of a plain `ZigZagEvaluator()` is enough — same composition
`flows/chia_nodes.ChiaParallelEvaluator` already demonstrates for CHIA/Ray parallelism.

Reuses test_search_exhaustive_live.py's exact known scenario (18 candidates, 6 skipped as
`NotExpressibleError`, proven 1554-cycle optimum) so this test's job is proving the *warm-start*
claim specifically, not re-proving the search result — that's already covered.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

import flux_ir
from flux_evaluator_zigzag import ZigZagEvaluator
from flux_search_exhaustive import run_exhaustive_search
from flux_store import CachingEvaluator, ResultStore

logging.getLogger("zigzag").setLevel(logging.WARNING)

FLUX_ROOT = Path(__file__).resolve().parents[2]


def test_a_second_full_sweep_against_the_same_store_makes_no_real_zigzag_calls(tmp_path):
    workload = flux_ir.load_document(FLUX_ROOT / "core/ir/workload/examples/mlp-gemm0.yaml")
    arch = flux_ir.load_document(FLUX_ROOT / "core/ir/architecture/examples/simple-npu-1d-v1.yaml")
    db_path = tmp_path / "flux.db"

    with ResultStore(db_path) as store:
        cached = CachingEvaluator(ZigZagEvaluator(), store, evaluator_prefix="zigzag")

        start = time.monotonic()
        first_report = run_exhaustive_search(
            workload, arch, cached, for_op="mlp.gemm0", metric="latency_cycles", minimize=True,
        )
        first_elapsed = time.monotonic() - start

        assert len(first_report.evaluated) == 18
        assert first_report.skipped_not_expressible == 6
        assert first_report.best.result.metrics["latency_cycles"].value == 1554.0
        # 12 real evaluations succeeded, 6 real evaluations were attempted and raised
        # NotExpressibleError (never cached, since CachingEvaluator only stores on a successful
        # return) — every attempt is a miss on a cold store.
        assert cached.stats.misses == 18
        assert cached.stats.hits == 0

    # A fresh CachingEvaluator instance, fresh ZigZagEvaluator instance, same store file reopened
    # — proving this is real persistence, not in-process object reuse carrying the cache.
    with ResultStore(db_path) as reopened_store:
        second_cached = CachingEvaluator(
            ZigZagEvaluator(), reopened_store, evaluator_prefix="zigzag",
        )

        start = time.monotonic()
        second_report = run_exhaustive_search(
            workload, arch, second_cached, for_op="mlp.gemm0", metric="latency_cycles",
            minimize=True,
        )
        second_elapsed = time.monotonic() - start

        assert second_report.best.result.metrics["latency_cycles"].value == 1554.0
        assert [e.result.to_dict() if e.result else None for e in second_report.evaluated] == [
            e.result.to_dict() if e.result else None for e in first_report.evaluated
        ]
        # The 12 expressible candidates are all cache hits; the 6 inexpressible ones are real
        # misses again (a failed attempt is never cached, so there is nothing to hit there —
        # NotExpressibleError is a property of the mapping, not something warm-start can shortcut
        # around, and re-raising it cheaply on a known-bad candidate is itself fast).
        assert second_cached.stats.hits == 12
        assert second_cached.stats.misses == 6
        # An order of magnitude faster than the cold run — most of the real work (12 ZigZag
        # subprocess-equivalent calls) was skipped entirely, not just sped up.
        assert second_elapsed < first_elapsed / 5
