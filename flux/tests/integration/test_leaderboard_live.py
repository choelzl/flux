"""End-to-end leaderboard (D58): every ZigZag-expressible public corpus entry through real ZigZag
into one `ResultStore`, ranked by `rank_results_for_entry` against pinned numbers.

The corpus holds a width-axis family (objective `latency_cycles`), a memory-size-axis family
(`energy_pj`), both on `mlp-gemm0.yaml`, and a separate workload (`mlp-ffn0.yaml`, D59). Only the
shared `workload_hash` and the declared objective decide which results compete.

The dual-core entry (D82) is skipped: it has no top-level `hierarchy`, so only Stream can express
it (see test_leaderboard_cross_evaluator_live.py).
"""

from __future__ import annotations

import logging
from pathlib import Path

import flux_ir
import pytest
from flux_evaluator_abi import Budget, Candidate
from flux_evaluator_zigzag import ZigZagEvaluator
from flux_store import CorpusPartition, CorpusStore, ResultStore
from flux_store.leaderboard import rank_results_for_entry

logging.getLogger("zigzag").setLevel(logging.WARNING)

FLUX_ROOT = Path(__file__).resolve().parents[2]
_CORPUS = CorpusStore(FLUX_ROOT / "mentor" / "benchmarks")
# Excludes the Stream-only multi-core entry (D82).
_PUBLIC_ENTRIES = [e for e in _CORPUS.public_entries() if e.id != "mlp-gemm0-simple-npu-1d-dual-core-v1"]


@pytest.fixture(scope="module")
def populated_store(tmp_path_factory):
    """Every ZigZag-expressible public entry evaluated once and stored, shared by the module."""
    db_path = tmp_path_factory.mktemp("leaderboard") / "flux.db"
    evaluator = ZigZagEvaluator()
    budget = Budget()
    with ResultStore(db_path) as store:
        for entry in _PUBLIC_ENTRIES:
            workload = flux_ir.load_document(FLUX_ROOT / entry.workload_path)
            arch = flux_ir.load_document(FLUX_ROOT / entry.arch_path)
            workload_hash = flux_ir.content_hash(workload)
            arch_hash = flux_ir.content_hash(arch)
            candidate = Candidate(workload=workload, arch=arch, mapping=None)
            result = evaluator.evaluate(candidate, budget, frozenset({"latency_cycles", "energy_pj"}))
            store.put_result(result, workload_hash=workload_hash, arch_hash=arch_hash)
    with ResultStore(db_path) as store:
        yield store


def _entry(entry_id: str):
    return next(e for e in _PUBLIC_ENTRIES if e.id == entry_id)


def test_ranks_the_width_axis_family_by_real_latency_matching_the_already_proven_optimum(populated_store):
    """Latency across X=4/8/16 is 3106/1554/778 cycles (D13), so X=16 (v3) ranks first."""
    standings = rank_results_for_entry(populated_store, _entry("mlp-gemm0-simple-npu-1d-v1"), repo_root=FLUX_ROOT)
    # At least the 3 width-axis entries compete (the gbuf entries report latency_cycles too:
    # ZigZagEvaluator always returns both metrics).
    assert len(standings) >= 3
    assert standings[0].value == pytest.approx(778.0)  # X=16


def test_ranks_the_memory_size_axis_family_by_real_energy_matching_the_already_proven_optimum(populated_store):
    """gbuf=1.25 KiB beats gbuf=64 KiB on energy (D26/D27).

    Not asserted first overall: the narrower X=4 entry has lower energy still (D58), since energy
    scales with compute width, not just buffer size.
    """
    standings = rank_results_for_entry(populated_store, _entry("mlp-gemm0-simple-npu-1d-gbuf1p25kb"), repo_root=FLUX_ROOT)
    gbuf_1p25_rank = next(s.rank for s in standings if s.value == pytest.approx(1116618.0081255918))
    gbuf_64_rank = next(s.rank for s in standings if s.value == pytest.approx(1116738.826398288))
    assert gbuf_1p25_rank < gbuf_64_rank


def test_ranks_the_second_real_workloads_family_in_isolation_from_the_first(populated_store):
    """mlp-ffn0 shares the latency objective with the gemm0 family but not its workload_hash, so
    it ranks alone: one standing, at rank 1, with its pinned two-layer number (D59)."""
    standings = rank_results_for_entry(populated_store, _entry("mlp-ffn0-simple-npu-1d-v1"), repo_root=FLUX_ROOT)
    assert len(standings) == 1
    assert standings[0].rank == 1
    assert standings[0].value == pytest.approx(1560.0)


def test_holdout_entry_is_not_reachable_through_public_entries():
    """The holdout entry (v4, X=32) is never in `_PUBLIC_ENTRIES`."""
    assert "mlp-gemm0-simple-npu-1d-v4" not in {e.id for e in _PUBLIC_ENTRIES}
    holdout = next(
        e for e in _CORPUS.all_entries(acknowledge_holdout_access=True)
        if e.partition is CorpusPartition.HOLDOUT
    )
    assert holdout.id == "mlp-gemm0-simple-npu-1d-v4"


