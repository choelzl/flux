"""`flux_knowledge_lookup`/`flux_get_result`/`flux_find_results`/`flux_list_public_corpus`
against real data — D9's priority-2 nodes (docs/decisions.md D9/D11): real BM25 retrieval over
the real ingested RISC-V corpus, a real `ResultStore` populated with a real ZigZag evaluation,
and the real `corpus/` directory's actual public/holdout split.

Requires the real `chia` package (see `flows/chia_nodes/README.md` for the submodule gotcha) and
`flux-evaluator-zigzag`.
"""

from __future__ import annotations

import logging
from pathlib import Path

import flux_ir
import pytest
import ray
from flux_chia_nodes import (
    flux_evaluate,
    flux_find_results,
    flux_get_result,
    flux_knowledge_lookup,
    flux_list_public_corpus,
)
from flux_store import ResultStore

logging.getLogger("zigzag").setLevel(logging.WARNING)

FLUX_ROOT = Path(__file__).resolve().parents[2]
GEMM_WORKLOAD = FLUX_ROOT / "core/ir/workload/examples/mlp-gemm0.yaml"
CORPUS_ROOT = FLUX_ROOT / "mentor" / "benchmarks"


def test_knowledge_lookup_retrieves_from_the_real_riscv_corpus():
    """No synthetic index — this hits flux_knowledge's real, process-wide cached BM25 index over
    the real ingested corpus/riscv-unpriv/ chapters.
    """
    results = flux_knowledge_lookup("fence instruction memory ordering", "riscv-unpriv", k=3)
    assert len(results) > 0
    assert all(r["chunk"]["standard_id"] == "riscv-unpriv" for r in results)
    assert all(r["score"] > 0 for r in results)


def test_knowledge_lookup_returns_json_safe_dicts_not_dataclasses():
    import json

    results = flux_knowledge_lookup("csr access ordering", k=1)
    json.dumps(results)  # raises if a dataclass/Chunk object leaked through unconverted


def test_get_result_and_find_results_round_trip_a_real_zigzag_evaluation(tmp_path):
    workload = flux_ir.load_document(GEMM_WORKLOAD)
    db_path = str(tmp_path / "flux.db")

    result = flux_evaluate("zigzag", workload)
    with ResultStore(db_path) as store:
        workload_hash = store.put_document("workload", workload)
        result_id = store.put_result(result, workload_hash=workload_hash)

    fetched = flux_get_result(db_path, result_id)
    assert fetched["result"]["metrics"]["latency_cycles"]["value"] == pytest.approx(145.0)
    assert fetched["evaluator"].startswith("zigzag@")

    found = flux_find_results(db_path, workload_hash=workload_hash)
    assert len(found) == 1
    assert found[0]["id"] == result_id


def test_get_result_returns_none_for_a_missing_id(tmp_path):
    db_path = str(tmp_path / "flux.db")
    ResultStore(db_path).close()  # create an empty store
    assert flux_get_result(db_path, 99999) is None


def test_find_results_with_no_filters_returns_everything(tmp_path):
    workload = flux_ir.load_document(GEMM_WORKLOAD)
    db_path = str(tmp_path / "flux.db")
    result = flux_evaluate("zigzag", workload)
    with ResultStore(db_path) as store:
        workload_hash = store.put_document("workload", workload)
        store.put_result(result, workload_hash=workload_hash)
        store.put_result(result, workload_hash=workload_hash)

    assert len(flux_find_results(db_path)) == 2


def test_list_public_corpus_matches_the_real_corpus_directory_and_excludes_holdout():
    """mentor/corpus/public/ has exactly seven entries: the original three width-axis entries (X=4/8/16),
    two real memory-size-axis entries (gbuf=1.25/64.0 KiB, docs/decisions.md D58), and one real
    second-workload entry (docs/decisions.md D59) — a genuinely different real workload
    (mlp-ffn0.yaml, a two-layer feedforward block) sharing the same X=8 architecture as v1.
    corpus/holdout/ still has exactly one entry (X=32, docs/calibration-report.md's Finding 3)
    that must never appear here. The public list below is maintained by hand deliberately — an
    independent statement of what should exist, not a re-derivation of what does.
    """
    entries = flux_list_public_corpus(str(CORPUS_ROOT))
    ids = {e["id"] for e in entries}
    # UPDATED (docs/decisions.md D123): the dual-core entry is a real seventh public entry, added
    # for the multi-core (Stream) work; this list was simply never updated, and — as in D119 —
    # nothing noticed, because the standard regression runs tests/unit/ and tests/conformance/
    # only. The holdout entry is still correctly absent, which is the part that matters, and
    # tests/unit/test_corpus_holdout_real.py now checks that against the real corpus every run.
    assert ids == {
        "mlp-gemm0-simple-npu-1d-v1",
        "mlp-gemm0-simple-npu-1d-v2",
        "mlp-gemm0-simple-npu-1d-v3",
        "mlp-gemm0-simple-npu-1d-gbuf1p25kb",
        "mlp-gemm0-simple-npu-1d-gbuf64kb",
        "mlp-gemm0-simple-npu-1d-dual-core-v1",
        "mlp-ffn0-simple-npu-1d-v1",
    }
    assert "mlp-gemm0-simple-npu-1d-v4" not in ids  # the holdout entry
    assert all(e["partition"] == "public" for e in entries)
    assert all(e["objective"] is not None for e in entries)  # D58: every real entry has one now


def test_list_public_corpus_tool_has_no_holdout_bypass_parameter():
    """Structural check, not just a behavioural one: the tool's own signature must not accept
    anything resembling an acknowledge-holdout-access override — the safety guarantee is that no
    such parameter exists to be passed, not that it defaults to False.
    """
    import inspect

    params = inspect.signature(flux_list_public_corpus).parameters
    assert "acknowledge_holdout_access" not in params
    assert set(params) == {"corpus_root"}


def test_dispatches_through_a_real_ray_task():
    from chia.base.ChiaFunction import get

    ref = flux_knowledge_lookup.chia_remote("fence.i", "riscv-unpriv", 2)
    assert isinstance(ref, ray.ObjectRef)
    results = get(ref)
    assert len(results) > 0
