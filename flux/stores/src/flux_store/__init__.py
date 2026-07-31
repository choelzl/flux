"""Flux result/artifact store (docs/04.md §8): content-addressed IR documents and
Evaluator results with full lineage.
"""

from __future__ import annotations

from .corpus import (
    CorpusEntry,
    CorpusPartition,
    CorpusStore,
    DuplicateCorpusEntryError,
    HoldoutAccessError,
    load_corpus,
)
from .store import ResultStore

__all__ = [
    "ResultStore",
    "CorpusEntry",
    "CorpusPartition",
    "CorpusStore",
    "HoldoutAccessError",
    "DuplicateCorpusEntryError",
    "load_corpus",
]
