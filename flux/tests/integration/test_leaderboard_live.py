"""Real end-to-end leaderboard test (docs/decisions.md D58): evaluates every public corpus/
entry sharing `mlp-gemm0.yaml` through real ZigZag, stores every result in a real `ResultStore`,
and confirms `flux_store.leaderboard.rank_results_for_entry` ranks them correctly against known,
already-pinned real numbers — not synthetic data (see tests/unit/test_leaderboard.py for the pure
ranking-logic version).

Three real benchmark families exist in `corpus/public/` today: two share `mlp-gemm0.yaml` but vary
different architecture axes — the width-axis entries (v1/v2/v3, objective `latency_cycles`,
already-pinned real numbers from D13/calibration-report.md) and the memory-size-axis entries
(gbuf1p25kb/gbuf64kb, objective `energy_pj`, real numbers pinned when these entries were added,
D58) — and a third, genuinely different real *workload* (`mlp-ffn0.yaml`, a two-layer feedforward
block, D59) with its own single entry. Evaluating all three families into the *same* store and
ranking each by its own objective is the real "architecture-family" comparison G13 asks for, and
the real proof `rank_results_for_entry` correctly ranks across a family — and correctly keeps
families for *different* workloads from ever mixing (workload_hash differs) — without being told
in advance which architectures belong together; only the shared `workload_hash` and the declared
objective decide.

A fourth real entry, `mlp-gemm0-simple-npu-1d-dual-core-v1` (docs/decisions.md D82), is
deliberately **not** evaluated here: its architecture has no top-level `hierarchy` at all (a
genuine multi-core document, real per-core structure inside `interconnect.multi_core` instead —
`evaluators/zigzag`'s own translator correctly raises `NotExpressibleError` on it, "0 compute
nodes", the same fail-loudly posture this repo's whole adapter set shares) — real Stream, not
ZigZag, is the only evaluator that can express it. See
`test_leaderboard_cross_evaluator_live.py` for that entry's own real, dedicated leaderboard proof
(and the real, physically sensible cross-evaluator finding it ranks: Stream's dual-core result
genuinely beats ZigZag's single-core one for the identical workload).
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
# Excludes the real Stream-only multi-core entry (docs/decisions.md D82) — see this module's own
# docstring for why; it has its own dedicated real test elsewhere.
_PUBLIC_ENTRIES = [e for e in _CORPUS.public_entries() if e.id != "mlp-gemm0-simple-npu-1d-dual-core-v1"]


@pytest.fixture(scope="module")
def populated_store(tmp_path_factory):
    """Real ZigZag evaluation of every ZigZag-expressible public corpus entry sharing
    mlp-gemm0.yaml, stored once and reused by every test in this module (avoids re-running
    ZigZag five times over)."""
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
    """docs/decisions.md D13: real ZigZag latency across X=4/8/16 is strictly monotonic
    (wider is faster) — 3106/1554/778 cycles. The leaderboard for the latency_cycles objective
    must put X=16 (v3) first."""
    standings = rank_results_for_entry(populated_store, _entry("mlp-gemm0-simple-npu-1d-v1"), repo_root=FLUX_ROOT)
    # At least the 3 width-axis architectures compete here (the gbuf-axis entries, also X=8,
    # report latency_cycles too — ZigZagEvaluator always returns both metrics regardless of what
    # was requested, a real, checked fact about this adapter, not assumed).
    assert len(standings) >= 3
    assert standings[0].value == pytest.approx(778.0)  # X=16, the real proven fastest


def test_ranks_the_memory_size_axis_family_by_real_energy_matching_the_already_proven_optimum(populated_store):
    """docs/decisions.md D26/D27/D58: real ZigZag energy at gbuf=1.25 KiB
    (1116618.0081255918 pJ) beats gbuf=64.0 KiB (1116738.826398288 pJ) — the real,
    within-the-memory-size-sweep-at-fixed-width optimum D26/D27 established.

    Deliberately does NOT assert gbuf1p25kb ranks #1 across *every* real architecture in the
    corpus: a genuine, real finding surfaced the first time this test ran (docs/decisions.md
    D58) — the width-axis v2 entry (X=4, the narrowest array) has even *lower* absolute energy
    (561367.5287841092 pJ) than either memory-size point, since energy scales with compute
    width broadly, not just buffer size. D26 only ever proved 1.25 KiB is optimal *within the
    width=8 memory-size sweep*, never that it's the global energy minimum across every real
    architecture this corpus now contains — this is exactly the honest, wider competition
    `rank_results_for_entry` ranking across the *whole* architecture family (not just one
    entry's own narrow point) is supposed to surface, and did.
    """
    standings = rank_results_for_entry(populated_store, _entry("mlp-gemm0-simple-npu-1d-gbuf1p25kb"), repo_root=FLUX_ROOT)
    gbuf_1p25_rank = next(s.rank for s in standings if s.value == pytest.approx(1116618.0081255918))
    gbuf_64_rank = next(s.rank for s in standings if s.value == pytest.approx(1116738.826398288))
    assert gbuf_1p25_rank < gbuf_64_rank


def test_ranks_the_second_real_workloads_family_in_isolation_from_the_first(populated_store):
    """docs/decisions.md D59: mlp-ffn0.yaml's own corpus entry shares an objective (latency_cycles)
    with the mlp-gemm0.yaml width-axis family, but a genuinely *different* workload_hash — the
    real, checked proof that ranking correctly separates by workload, not just by objective
    metric. A real, single-competitor "leaderboard" (only one architecture has ever been
    evaluated against this workload so far) still ranks correctly: exactly one standing, at
    rank 1, matching the real, aggregate-across-both-layers ZigZag number this workload was
    verified against before being added to the corpus."""
    standings = rank_results_for_entry(populated_store, _entry("mlp-ffn0-simple-npu-1d-v1"), repo_root=FLUX_ROOT)
    assert len(standings) == 1
    assert standings[0].rank == 1
    assert standings[0].value == pytest.approx(1560.0)


def test_holdout_entry_is_not_reachable_through_public_entries():
    """The holdout entry (v4, X=32) is never in `_PUBLIC_ENTRIES` — there is no way to ask this
    module to rank it without first calling `all_entries(acknowledge_holdout_access=True)`
    directly, the same structural guarantee `flux_list_public_corpus` already has."""
    assert "mlp-gemm0-simple-npu-1d-v4" not in {e.id for e in _PUBLIC_ENTRIES}
    holdout = next(
        e for e in _CORPUS.all_entries(acknowledge_holdout_access=True)
        if e.partition is CorpusPartition.HOLDOUT
    )
    assert holdout.id == "mlp-gemm0-simple-npu-1d-v4"


def test_leaderboard_chia_node_ranks_via_the_real_public_only_entry_lookup(populated_store):
    """`flux_leaderboard` (docs/decisions.md D58) end to end: looks `entry_id` up via
    `public_entries()` internally, then ranks — real CorpusStore + real ResultStore + real data."""
    from flux_chia_nodes.store import flux_leaderboard

    result = flux_leaderboard(
        corpus_root=str(FLUX_ROOT / "mentor" / "benchmarks"),
        entry_id="mlp-gemm0-simple-npu-1d-v1",
        db_path=populated_store.db_path,
    )
    assert result[0]["rank"] == 1
    assert result[0]["value"] == pytest.approx(778.0)


def test_leaderboard_chia_node_rejects_a_holdout_entry_id():
    from flux_chia_nodes.store import flux_leaderboard

    with pytest.raises(ValueError, match="not a public corpus entry"):
        flux_leaderboard(
            corpus_root=str(FLUX_ROOT / "mentor" / "benchmarks"),
            entry_id="mlp-gemm0-simple-npu-1d-v4",  # the real holdout entry's own id
            db_path=":memory:",
        )
