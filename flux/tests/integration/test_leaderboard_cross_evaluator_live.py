"""Real, end-to-end proof that the leaderboard ranks across genuinely different evaluator
*backends*, not just different architectures through the same one (docs/decisions.md D82's own
new corpus entry, `mlp-gemm0-simple-npu-1d-dual-core-v1`) — real ZigZag (single-core) and real
Stream (multi-core, `evaluators/stream`) both report `latency_cycles` for the exact same
`mlp-gemm0.yaml` workload, and `flux_store.leaderboard.rank_results_for_entry` ranks them
together purely by `workload_hash` + declared objective, with no idea in advance which
evaluator produced which result. See tests/integration/test_leaderboard_live.py for the
established, ZigZag-only version this one deliberately doesn't duplicate.
"""

from __future__ import annotations

import logging
from pathlib import Path

import flux_ir
import pytest
from flux_evaluator_abi import Budget, Candidate
from flux_evaluator_stream import StreamEvaluator
from flux_evaluator_zigzag import ZigZagEvaluator
from flux_store import CorpusStore, ResultStore
from flux_store.leaderboard import rank_results_for_entry

logging.getLogger("zigzag").setLevel(logging.WARNING)

FLUX_ROOT = Path(__file__).resolve().parents[2]
_CORPUS = CorpusStore(FLUX_ROOT / "mentor" / "benchmarks")


def _entry(entry_id: str):
    return next(e for e in _CORPUS.public_entries() if e.id == entry_id)


@pytest.fixture(scope="module")
def populated_store(tmp_path_factory):
    """Real ZigZag (single-core, X=8) and real Stream (dual-core) evaluations of the identical
    `mlp-gemm0.yaml` workload, stored in the same real `ResultStore` — the genuine cross-
    evaluator-family case this test exists to prove.
    """
    db_path = tmp_path_factory.mktemp("leaderboard-cross") / "flux.db"
    workload = flux_ir.load_document(FLUX_ROOT / "core/ir/workload/examples/mlp-gemm0.yaml")
    workload_hash = flux_ir.content_hash(workload)
    budget = Budget()

    with ResultStore(db_path) as store:
        single_core_arch = flux_ir.load_document(FLUX_ROOT / "core/ir/architecture/examples/simple-npu-1d-v1.yaml")
        zigzag_result = ZigZagEvaluator().evaluate(
            Candidate(workload=workload, arch=single_core_arch, mapping=None), budget, frozenset({"latency_cycles"}),
        )
        store.put_result(
            zigzag_result, workload_hash=workload_hash, arch_hash=flux_ir.content_hash(single_core_arch),
        )

        dual_core_arch = flux_ir.load_document(
            FLUX_ROOT / "core/ir/architecture/examples/simple-npu-1d-dual-core-v1.yaml"
        )
        stream_result = StreamEvaluator(timeout_s=250.0).evaluate(
            Candidate(workload=workload, arch=dual_core_arch, mapping=None), budget, frozenset({"latency_cycles"}),
        )
        store.put_result(
            stream_result, workload_hash=workload_hash, arch_hash=flux_ir.content_hash(dual_core_arch),
        )

    with ResultStore(db_path) as store:
        yield store


def test_real_stream_multicore_result_ranks_ahead_of_real_zigzag_single_core(populated_store):
    """The real, physically sensible finding this entry's own description names: splitting
    mlp-gemm0.yaml's one GEMM across two real compute cores (Stream, 1148.0 cycles) genuinely
    beats the single-core baseline (ZigZag, 1554.0 cycles) — not assumed, ranked.
    """
    standings = rank_results_for_entry(
        populated_store, _entry("mlp-gemm0-simple-npu-1d-dual-core-v1"), repo_root=FLUX_ROOT,
    )
    assert len(standings) >= 2
    assert standings[0].value == pytest.approx(1148.0)
    assert standings[0].evaluator == "stream-dse@real"

    zigzag_standing = next(s for s in standings if s.evaluator.startswith("zigzag"))
    assert zigzag_standing.value == pytest.approx(1554.0)
    assert standings[0].rank < zigzag_standing.rank


def test_workload_hash_is_the_only_thing_that_matters_not_the_evaluator(populated_store):
    """The real, structural proof this is a genuine cross-evaluator ranking, not two separate
    lists happening to share a name: both the ZigZag-authored entry
    (mlp-gemm0-simple-npu-1d-v1) and the Stream-authored one
    (mlp-gemm0-simple-npu-1d-dual-core-v1) rank the exact same standings, since both name the
    identical mlp-gemm0.yaml workload.
    """
    from_zigzag_entry = rank_results_for_entry(populated_store, _entry("mlp-gemm0-simple-npu-1d-v1"), repo_root=FLUX_ROOT)
    from_stream_entry = rank_results_for_entry(
        populated_store, _entry("mlp-gemm0-simple-npu-1d-dual-core-v1"), repo_root=FLUX_ROOT,
    )
    assert [s.value for s in from_zigzag_entry] == [s.value for s in from_stream_entry]
