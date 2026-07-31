"""Benchmark corpus + holdout discipline (docs/04.md §8, docs/05.md §3): a partition of the
corpus is never visible to search or to any agent — the mechanism that caught CHIA's gem5-
alignment agent overfitting (docs/02.md §5.8): the agent tuned a threshold "calibrated to
[training benchmark]'s inner loop," which then degraded a holdout benchmark it could not see.
"Enforced by the store, not by convention" (docs/04.md §8) means exactly what it says: there is
no single `entries()` method with a flag a caller can forget to set. `public_entries()` is the
only method a search strategy or agent should ever call, and it structurally cannot return
holdout entries. `all_entries()` requires a required, keyword-only `acknowledge_holdout_access`
argument with no default — omitting it is a `TypeError` raised by Python itself, not a lint
warning, and passing it explicitly is a deliberate, greppable act every legitimate call site
(calibration/validation reporting, never a search loop) has to commit to in its own source.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

import yaml


class CorpusPartition(str, Enum):
    PUBLIC = "public"
    HOLDOUT = "holdout"


@dataclass(frozen=True, slots=True)
class CorpusEntry:
    """One benchmark point: a (workload, architecture) pair plus enough metadata to explain why
    it's in the corpus at all. Paths are repo-relative, matching how every test in this repo
    already references `ir/*/examples/*.yaml` (see e.g. tests/integration/*_live.py).
    """

    id: str
    partition: CorpusPartition
    workload_path: str
    arch_path: str
    description: str


class HoldoutAccessError(Exception):
    """Raised by `CorpusStore.all_entries()` when called without explicitly acknowledging that
    the result includes the holdout partition. Not raised by `public_entries()`, which simply
    never has holdout entries to return in the first place.
    """


class DuplicateCorpusEntryError(Exception):
    """Raised when two corpus manifests (public and/or holdout) declare the same entry `id` —
    ambiguous which partition it belongs to, which is exactly the kind of mistake this module
    exists to make impossible to make silently.
    """


def _load_entry(manifest_path: Path, partition: CorpusPartition) -> CorpusEntry:
    data: dict[str, Any] = yaml.safe_load(manifest_path.read_text())
    return CorpusEntry(
        id=data["id"],
        partition=partition,
        workload_path=data["workload_path"],
        arch_path=data["arch_path"],
        description=data["description"],
    )


def load_corpus(corpus_root: str | Path) -> list[CorpusEntry]:
    """Load every `*.yaml` manifest under `corpus_root/public/` and `corpus_root/holdout/`,
    tagging each entry's partition from the directory it was loaded from — the partition is a
    property of *where the file lives*, not a field a manifest author could get wrong or a
    loader caller could override.
    """
    root = Path(corpus_root)
    entries: list[CorpusEntry] = []
    seen_ids: dict[str, CorpusPartition] = {}
    for partition, dirname in (
        (CorpusPartition.PUBLIC, "public"),
        (CorpusPartition.HOLDOUT, "holdout"),
    ):
        for manifest_path in sorted((root / dirname).glob("*.yaml")):
            entry = _load_entry(manifest_path, partition)
            if entry.id in seen_ids:
                raise DuplicateCorpusEntryError(
                    f"corpus entry id {entry.id!r} declared in both "
                    f"{seen_ids[entry.id].value}/ and {partition.value}/"
                )
            seen_ids[entry.id] = partition
            entries.append(entry)
    return entries


class CorpusStore:
    """Loads a corpus once at construction and exposes the two-method access surface described
    in this module's docstring.
    """

    def __init__(self, corpus_root: str | Path) -> None:
        self._entries = load_corpus(corpus_root)

    def public_entries(self) -> list[CorpusEntry]:
        """The only method search strategies and agents should call. Structurally cannot return
        a holdout entry: it filters by partition, it does not accept one as a parameter."""
        return [e for e in self._entries if e.partition is CorpusPartition.PUBLIC]

    def all_entries(self, *, acknowledge_holdout_access: bool) -> list[CorpusEntry]:
        """For validation/reporting code that legitimately needs the holdout partition — e.g.
        checking whether a calibrated confidence interval still covers a point it was never
        fitted on (docs/calibration-report.md's Finding 3 did this by hand; this is that made
        into an enforced, reusable primitive). Never call this from a search strategy or expose
        its result to an agent.
        """
        if not acknowledge_holdout_access:
            raise HoldoutAccessError(
                "all_entries() includes the holdout partition, which must never be visible to "
                "search or to any agent (docs/05.md §3 — this is precisely the mechanism that "
                "caught CHIA's gem5-alignment agent overfitting). Pass "
                "acknowledge_holdout_access=True only from validation/reporting code with a "
                "genuine reason to see held-out points; if you're writing a search strategy or "
                "an agent tool, call public_entries() instead."
            )
        return list(self._entries)
