"""Stores real ZigZag and Timeloop results — the same controlled comparison as
tests/integration/test_cross_evaluator_same_architecture_report.py, but round-tripped through
flux_store.ResultStore, proving the store's lineage fields are enough to tell the two results
apart and find them again later (docs/04.md §8: "deterministic replay is one command").
"""

from __future__ import annotations

from pathlib import Path

import flux_ir
import pytest
from flux_evaluator_abi import Budget, Candidate
from flux_evaluator_timeloop import TimeloopEvaluator
from flux_evaluator_zigzag import ZigZagEvaluator
from flux_store import ResultStore

FLUX_ROOT = Path(__file__).resolve().parents[2]
GEMM_WORKLOAD = FLUX_ROOT / "ir/workload/examples/mlp-gemm0.yaml"
SIMPLE_NPU_1D = FLUX_ROOT / "ir/architecture/examples/simple-npu-1d-v1.yaml"


def test_store_round_trips_results_from_both_real_backends(tmp_path):
    workload = flux_ir.load_document(GEMM_WORKLOAD)
    arch = flux_ir.load_document(SIMPLE_NPU_1D)
    candidate = Candidate(workload=workload, arch=arch, mapping=None)

    with ResultStore(tmp_path / "flux.db") as store:
        workload_hash = store.put_document("workload", workload)
        arch_hash = store.put_document("architecture", arch)
        assert workload_hash == flux_ir.content_hash(workload)
        assert arch_hash == flux_ir.content_hash(arch)

        zigzag_result = ZigZagEvaluator().evaluate(
            candidate, Budget(), frozenset({"latency_cycles", "energy_pj"})
        )
        timeloop_result = TimeloopEvaluator().evaluate(
            candidate, Budget(), frozenset({"latency_cycles", "energy_pj", "area_mm2"})
        )
        zigzag_id = store.put_result(zigzag_result, workload_hash=workload_hash, arch_hash=arch_hash)
        timeloop_id = store.put_result(
            timeloop_result, workload_hash=workload_hash, arch_hash=arch_hash
        )

        # Deterministic replay: fetch by row id, get the same numbers back.
        assert store.get_result(zigzag_id)["result"]["metrics"]["latency_cycles"]["value"] == (
            zigzag_result.metrics["latency_cycles"].value
        )
        assert store.get_result(timeloop_id)["result"]["metrics"]["latency_cycles"]["value"] == (
            timeloop_result.metrics["latency_cycles"].value
        )

        # Warm-start query surface: both results are findable by their shared lineage, and
        # distinguishable by evaluator.
        both = store.find_results(workload_hash=workload_hash, arch_hash=arch_hash)
        assert {r["id"] for r in both} == {zigzag_id, timeloop_id}
        zigzag_only = store.find_results(
            workload_hash=workload_hash, arch_hash=arch_hash, evaluator=zigzag_result.provenance.evaluator
        )
        assert [r["id"] for r in zigzag_only] == [zigzag_id]

        # The stored IR documents themselves replay too — not just the results.
        assert store.get_document(workload_hash) == workload
        assert store.get_document(arch_hash) == arch

        # Closing and reopening the same file must see the same data (it's a real file, not
        # an in-memory-only connection).
    with ResultStore(tmp_path / "flux.db") as reopened:
        assert reopened.get_document(workload_hash) == workload
        assert len(reopened.find_results(workload_hash=workload_hash)) == 2
