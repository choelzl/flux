"""`flux_get_result`/`flux_find_results`/`flux_list_public_corpus` — the second of D9's
priority-2 nodes (docs/decisions.md D9/D11): read-only agent access to `flux_store`, which
existed as real, working code since Phase 1/2 but had no CHIA-node or MCP-tool surface until now
(confirmed by grep: zero references in this package before this file) — an agentic proposer with
no way to query prior results or warm-start from history was missing a capability
`search/README.md` itself already names as a gap ("Warm-start against `flux_store.ResultStore`
— no strategy queries it yet").

`flux_list_public_corpus` is holdout-safe **by construction, not by convention**: it calls
`CorpusStore.public_entries()` only and never accepts (or forwards) an
`acknowledge_holdout_access` parameter at all. Exposing that flag on an agent-facing tool would
hand the agent exactly the bypass `flux_store.corpus`'s whole two-method design exists to make
impossible to reach accidentally — so this tool doesn't expose it on purpose, not as an oversight.

`flux_leaderboard` (docs/decisions.md D58) is the same read-only posture applied to
`flux_store.leaderboard.rank_results_for_entry`: it looks its `entry_id` up via
`public_entries()` only, same holdout-safe-by-construction shape as `flux_list_public_corpus` —
never `all_entries()`, so a holdout corpus entry can't be ranked (or even named) through this
tool. Read-only like every other `flux_store`-backed node here: no "submit a result" tool exists
anywhere in this surface — populating the store stays the search/generation loop's job (D11).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from chia.base.ChiaFunction import ChiaFunction
from flux_store import CorpusStore, ResultStore
from flux_store.leaderboard import rank_results_for_entry


@ChiaFunction()
def flux_get_result(db_path: str, result_id: int) -> dict[str, Any] | None:
    """Fetch one stored `Result` by row id, with its full lineage
    (`workload_hash`/`arch_hash`/`mapping_hash`/`evaluator`) — `None` if no such id exists.
    """
    with ResultStore(db_path) as store:
        return store.get_result(result_id)


@ChiaFunction()
def flux_find_results(
    db_path: str,
    workload_hash: str | None = None,
    arch_hash: str | None = None,
    evaluator: str | None = None,
) -> list[dict[str, Any]]:
    """Query stored results by any combination of lineage fields (all optional — omitting all
    three returns every stored result). The warm-start / "has this candidate already been
    evaluated" query surface docs/search.md describes.
    """
    with ResultStore(db_path) as store:
        return store.find_results(
            workload_hash=workload_hash, arch_hash=arch_hash, evaluator=evaluator
        )


@ChiaFunction()
def flux_list_public_corpus(corpus_root: str) -> list[dict[str, Any]]:
    """List every **public** benchmark corpus entry under `corpus_root` — never the holdout
    partition; there is no parameter here that could ask for it. See this module's docstring for
    why that's a structural guarantee, not a default a caller has to remember to keep.
    """
    store = CorpusStore(corpus_root)
    return [entry.to_dict() for entry in store.public_entries()]


@ChiaFunction()
def flux_leaderboard(corpus_root: str, entry_id: str, db_path: str) -> list[dict[str, Any]]:
    """Rank every stored result for the **public** corpus entry `entry_id`'s workload — across
    every architecture anyone has ever evaluated it against, not just that entry's own named
    `arch_path` — by its declared `objective`, best first (docs/decisions.md D58). Raises
    `ValueError` if `entry_id` doesn't name a public corpus entry, or
    `flux_store.leaderboard.LeaderboardEntryError` if that entry has no `objective` or no stored
    result reports its metric yet.

    `repo_root` for resolving `entry.workload_path` is derived as `corpus_root`'s own parent
    directory — the same convention every corpus-consuming test in this repo already assumes
    (`workload_path`/`arch_path` are repo-relative, and `corpus_root` is always `<repo_root>/
    corpus`), not a second, separately-passed path a caller could get out of sync with the first.
    """
    corpus = CorpusStore(corpus_root)
    entry = next((e for e in corpus.public_entries() if e.id == entry_id), None)
    if entry is None:
        raise ValueError(f"entry_id={entry_id!r} is not a public corpus entry under {corpus_root!r}")
    with ResultStore(db_path) as store:
        standings = rank_results_for_entry(store, entry, repo_root=Path(corpus_root).parent)
    return [s.to_dict() for s in standings]
