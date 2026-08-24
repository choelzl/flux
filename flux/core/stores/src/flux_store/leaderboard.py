"""Ranking/standings for a corpus benchmark problem (D58): a read-only query layer over
`ResultStore.find_results()`.

Ranks across every architecture a workload has been evaluated against: it filters by
`workload_hash` only, never `arch_hash`, so an entry's `arch_path` is one reference point, not
the only contender. Populating the store is the search loop's job (D11).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import flux_ir

from .corpus import CorpusEntry
from .store import ResultStore


class LeaderboardEntryError(Exception):
    """Raised when a corpus entry can't be ranked: it has no `objective`, or no stored result
    reports that objective's metric. Never a silently empty standings list.
    """


@dataclass(frozen=True, slots=True)
class Standing:
    """One ranked result — `rank` is 1-based, assigned only after every candidate result for the
    same objective has been collected and sorted, never a running counter during collection."""

    rank: int
    result_id: int
    evaluator: str
    arch_hash: str | None
    value: float
    result: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "rank": self.rank,
            "result_id": self.result_id,
            "evaluator": self.evaluator,
            "arch_hash": self.arch_hash,
            "value": self.value,
            "result": self.result,
        }


def rank_results_for_entry(
    store: ResultStore, entry: CorpusEntry, *, repo_root: str | Path
) -> list[Standing]:
    """Rank every stored result for `entry`'s workload (any architecture) by
    `entry.objective`'s metric, best first (`rank=1` is the record-holder).

    `repo_root` resolves `entry.workload_path` to a file hashed with `flux_ir.content_hash`,
    the same key `ResultStore.put_result` uses.
    """
    if entry.objective is None:
        raise LeaderboardEntryError(
            f"corpus entry {entry.id!r} has no declared objective — cannot rank it "
            "(see CorpusEntry.objective / Objective in flux_store.corpus)"
        )

    workload_doc = flux_ir.load_document(Path(repo_root) / entry.workload_path)
    workload_hash = flux_ir.content_hash(workload_doc)
    metric = entry.objective.metric

    standings: list[Standing] = []
    for row in store.find_results(workload_hash=workload_hash):
        metrics = row["result"]["metrics"]
        if metric not in metrics:
            continue  # a real evaluator that doesn't report this metric — skipped, not an error
        standings.append(Standing(
            rank=0,  # assigned below, after every result is in
            result_id=row["id"],
            evaluator=row["evaluator"],
            arch_hash=row["arch_hash"],
            value=metrics[metric]["value"],
            result=row["result"],
        ))

    if not standings:
        raise LeaderboardEntryError(
            f"no stored result for corpus entry {entry.id!r} reports metric {metric!r} — "
            "nothing to rank yet (has anything been evaluated and stored for this workload?)"
        )

    standings.sort(key=lambda s: s.value, reverse=not entry.objective.minimize)
    return [replace(s, rank=i + 1) for i, s in enumerate(standings)]
