"""Flux result/artifact store (docs/stores.md): content-addressed IR documents and
Evaluator results with full lineage.
"""

from __future__ import annotations

from .caching import CacheStats, CachingEvaluator
from .campaign import CampaignStore, CampaignStoreError, RemainingBudget, Trial
from .corpus import (
    CorpusEntry,
    CorpusPartition,
    CorpusStore,
    CorpusRootError,
    DuplicateCorpusEntryError,
    HoldoutAccessError,
    Objective,
    load_corpus,
)
from .leaderboard import LeaderboardEntryError, Standing, rank_results_for_entry
from .store import ResultStore

__all__ = [
    "ResultStore",
    "CampaignStore",
    "CampaignStoreError",
    "RemainingBudget",
    "Trial",
    "CachingEvaluator",
    "CacheStats",
    "CorpusEntry",
    "CorpusPartition",
    "CorpusStore",
    "HoldoutAccessError",
    "DuplicateCorpusEntryError",
    "CorpusRootError",
    "load_corpus",
    "Objective",
    "Standing",
    "LeaderboardEntryError",
    "rank_results_for_entry",
]
