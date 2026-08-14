"""Real ranking/standings for a corpus benchmark problem (docs/decisions.md D58) — the piece
gap-analysis.md G13 names directly: "No agreed set of (workload, architecture-family, objective,
constraint) problems exists for comparing search strategies or cost models head-to-head." Builds
entirely on `ResultStore.find_results()` (no new storage engine, no new schema beyond
`CorpusEntry`'s own new `objective` field, docs/decisions.md D58) — this module is a pure
query/ranking layer over data that's already real and already stored, the same "build on
`find_results`, don't reinvent it" pattern `CachingEvaluator` (D19) already established.

**Ranks across the whole architecture family a workload has ever been evaluated against, not
just one corpus entry's own named (workload, arch) pair** — filtering only by `workload_hash`,
never `arch_hash`, is the deliberate choice that makes this a genuine architecture-family
comparison (G13's own definition), not a single-point lookup. A corpus entry's `arch_path` names
*a* reference point worth having in the corpus, not the only architecture a "leaderboard" for that
workload is allowed to include.

**Deliberately read-only, matching the `CorpusStore`/`ResultStore` precedent (D11).** This module
has no "submit"/"put" function of its own — populating the store with results to be ranked is the
search/generation loop's job, the same split D11 already established for corpus access ("no 'put'
tool exists... a deliberate scope limit, not an oversight").
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import flux_ir

from .corpus import CorpusEntry
from .store import ResultStore


class LeaderboardEntryError(Exception):
    """Raised when a corpus entry can't be ranked: either it has no declared `objective`
    (`CorpusEntry.objective`, docs/decisions.md D58 — the field `corpus/README.md` had named "not
    implemented" since D11), or no stored result reports that objective's metric at all. Both are
    real, named reasons — never a silently empty standings list that could be mistaken for
    "checked, genuinely found nothing" when the actual cause is "can't check this entry at all."
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
    """Rank every stored result for `entry`'s workload (across every architecture anyone has ever
    evaluated it against — see module docstring) by `entry.objective`'s metric, best first
    (`rank=1` is the current record-holder for this benchmark problem).

    `repo_root` resolves `entry.workload_path` (repo-relative, matching every other reference to
    `ir/*/examples/*.yaml` in this repo) to a real file to hash — the same `flux_ir.content_hash`
    every `ResultStore.put_result` call was already keyed by, so a result stored from a real
    evaluation of this exact workload document is found by construction, not by a second,
    separately-maintained lookup.
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
            rank=0,  # placeholder — the real rank is assigned below, after every result is in
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
