"""Unit tests for flux_store.leaderboard: pure ranking logic against a real (SQLite, tmp_path)
ResultStore and synthetic Results — no real evaluator needed. See
tests/integration/test_leaderboard_live.py for the real-evaluator version, ranking real ZigZag
results for the real corpus/ entries.
"""

from __future__ import annotations

import flux_ir
import pytest
from flux_evaluator_abi import (
    Bottleneck,
    Domain,
    Escalation,
    Estimate,
    Limiter,
    Method,
    Provenance,
    Result,
    Validity,
)
from flux_store import ResultStore
from flux_store.corpus import CorpusEntry, CorpusPartition, Objective
from flux_store.leaderboard import LeaderboardEntryError, rank_results_for_entry


def _result(evaluator: str, metric: str, value: float) -> Result:
    return Result(
        metrics={metric: Estimate(value=value, ci_low=value, ci_high=value, unit="u", method=Method.ANALYTIC)},
        validity=Validity(ok=True, checker_version="test"),
        domain=Domain(in_domain=True),
        bottleneck=Bottleneck(limiter=Limiter.COMPUTE),
        provenance=Provenance(evaluator=evaluator, inputs={}),
        escalation=Escalation(recommended=False),
    )


@pytest.fixture
def repo_root(tmp_path):
    d = tmp_path / "repo"
    (d / "core/ir/workload/examples").mkdir(parents=True)
    (d / "core/ir/workload/examples/w.yaml").write_text("id: w\nvalue: 1\n")
    (d / "core/ir/workload/examples/other.yaml").write_text("id: other\nvalue: 2\n")
    return d


@pytest.fixture
def store(tmp_path):
    with ResultStore(tmp_path / "flux.db") as s:
        yield s


def _entry(objective: Objective | None = None, workload_path: str = "core/ir/workload/examples/w.yaml") -> CorpusEntry:
    return CorpusEntry(
        id="e1", partition=CorpusPartition.PUBLIC,
        workload_path=workload_path,
        arch_path="core/ir/architecture/examples/a.yaml",
        description="test entry",
        objective=objective,
    )


def _workload_hash(repo_root, workload_path="core/ir/workload/examples/w.yaml"):
    return flux_ir.content_hash(flux_ir.load_document(repo_root / workload_path))


def test_entry_without_objective_raises(store, repo_root):
    entry = _entry(objective=None)
    with pytest.raises(LeaderboardEntryError, match="no declared objective"):
        rank_results_for_entry(store, entry, repo_root=repo_root)


def test_no_matching_results_raises(store, repo_root):
    entry = _entry(objective=Objective(metric="latency_cycles", minimize=True))
    with pytest.raises(LeaderboardEntryError, match="nothing to rank"):
        rank_results_for_entry(store, entry, repo_root=repo_root)


def test_ranks_ascending_when_minimizing(store, repo_root):
    wh = _workload_hash(repo_root)
    store.put_result(_result("evalA", "latency_cycles", 300), workload_hash=wh, arch_hash="archA")
    store.put_result(_result("evalB", "latency_cycles", 100), workload_hash=wh, arch_hash="archB")
    store.put_result(_result("evalC", "latency_cycles", 200), workload_hash=wh, arch_hash="archC")

    entry = _entry(objective=Objective(metric="latency_cycles", minimize=True))
    standings = rank_results_for_entry(store, entry, repo_root=repo_root)

    assert [s.value for s in standings] == [100, 200, 300]
    assert [s.rank for s in standings] == [1, 2, 3]
    assert standings[0].evaluator == "evalB"
    assert standings[0].arch_hash == "archB"


def test_ranks_descending_when_maximizing(store, repo_root):
    wh = _workload_hash(repo_root)
    store.put_result(_result("evalA", "throughput", 10), workload_hash=wh, arch_hash="archA")
    store.put_result(_result("evalB", "throughput", 50), workload_hash=wh, arch_hash="archB")

    entry = _entry(objective=Objective(metric="throughput", minimize=False))
    standings = rank_results_for_entry(store, entry, repo_root=repo_root)

    assert [s.value for s in standings] == [50, 10]
    assert standings[0].evaluator == "evalB"


def test_ignores_results_for_a_different_workload(store, repo_root):
    wh_target = _workload_hash(repo_root, "core/ir/workload/examples/w.yaml")
    wh_other = _workload_hash(repo_root, "core/ir/workload/examples/other.yaml")
    store.put_result(_result("evalA", "latency_cycles", 100), workload_hash=wh_target, arch_hash="archA")
    store.put_result(_result("evalB", "latency_cycles", 1), workload_hash=wh_other, arch_hash="archB")

    entry = _entry(objective=Objective(metric="latency_cycles", minimize=True))
    standings = rank_results_for_entry(store, entry, repo_root=repo_root)

    assert len(standings) == 1
    assert standings[0].evaluator == "evalA"


def test_includes_every_architecture_evaluated_for_the_workload_not_just_the_entrys_own_arch(store, repo_root):
    """The real point of this module (docs/decisions.md D58): a corpus entry names *a* reference
    architecture, but ranking isn't limited to that one arch_hash — every real result for the
    same workload, across every architecture anyone has evaluated it against, competes."""
    wh = _workload_hash(repo_root)
    store.put_result(_result("evalA", "latency_cycles", 500), workload_hash=wh, arch_hash="arch-not-named-by-entry")
    store.put_result(_result("evalB", "latency_cycles", 900), workload_hash=wh, arch_hash="also-not-named")

    entry = _entry(objective=Objective(metric="latency_cycles", minimize=True))
    standings = rank_results_for_entry(store, entry, repo_root=repo_root)

    assert {s.arch_hash for s in standings} == {"arch-not-named-by-entry", "also-not-named"}


def test_skips_results_missing_the_objective_metric(store, repo_root):
    wh = _workload_hash(repo_root)
    store.put_result(_result("evalA", "latency_cycles", 100), workload_hash=wh, arch_hash="archA")
    store.put_result(_result("evalB", "energy_pj", 999), workload_hash=wh, arch_hash="archB")  # different metric

    entry = _entry(objective=Objective(metric="latency_cycles", minimize=True))
    standings = rank_results_for_entry(store, entry, repo_root=repo_root)

    assert len(standings) == 1
    assert standings[0].evaluator == "evalA"


def test_standing_to_dict_is_json_safe(store, repo_root):
    import json

    wh = _workload_hash(repo_root)
    store.put_result(_result("evalA", "latency_cycles", 100), workload_hash=wh, arch_hash="archA")
    entry = _entry(objective=Objective(metric="latency_cycles", minimize=True))
    standings = rank_results_for_entry(store, entry, repo_root=repo_root)

    json.dumps(standings[0].to_dict())
    assert standings[0].to_dict()["rank"] == 1
