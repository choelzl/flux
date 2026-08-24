"""Benchmark corpus with holdout discipline (docs/stores.md): the holdout partition is never
visible to search or to any agent, so overfitting to training benchmarks shows up.

Enforced by the store: `public_entries()` is the only method a search or agent should call and
cannot return holdout entries. `all_entries()` takes a required keyword-only
`acknowledge_holdout_access`, so every legitimate call site (calibration/validation reporting)
opts in visibly in its own source.
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
class Objective:
    """What "best" means for a corpus entry (D58). `metric` is a `Result.metrics` key (e.g.
    `"latency_cycles"`), not validated against an enum since new evaluators add new names.
    """

    metric: str
    minimize: bool

    def to_dict(self) -> dict[str, Any]:
        return {"metric": self.metric, "minimize": self.minimize}


@dataclass(frozen=True, slots=True)
class CorpusEntry:
    """One benchmark point: a (workload, architecture) pair plus metadata on why it is in the
    corpus. Paths are repo-relative.

    `objective` is optional here; `leaderboard.py`'s ranking requires it and raises a named
    error when it is missing.
    """

    id: str
    partition: CorpusPartition
    workload_path: str
    arch_path: str
    description: str
    objective: Objective | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "partition": self.partition.value,
            "workload_path": self.workload_path,
            "arch_path": self.arch_path,
            "description": self.description,
            "objective": self.objective.to_dict() if self.objective is not None else None,
        }


class HoldoutAccessError(Exception):
    """Raised by `CorpusStore.all_entries()` when called without acknowledging holdout access."""


class CorpusRootError(Exception):
    """Raised when `load_corpus` is pointed at a path that does not exist or has neither a
    `public/` nor a `holdout/` directory. An empty corpus is legitimate and returns `[]`.
    """


class DuplicateCorpusEntryError(Exception):
    """Raised when two corpus manifests (public and/or holdout) declare the same entry `id`."""


def _load_entry(manifest_path: Path, partition: CorpusPartition) -> CorpusEntry:
    data: dict[str, Any] = yaml.safe_load(manifest_path.read_text())
    raw_objective = data.get("objective")
    objective = (
        Objective(metric=raw_objective["metric"], minimize=bool(raw_objective["minimize"]))
        if raw_objective is not None
        else None
    )
    return CorpusEntry(
        id=data["id"],
        partition=partition,
        workload_path=data["workload_path"],
        arch_path=data["arch_path"],
        description=data["description"],
        objective=objective,
    )


def load_corpus(corpus_root: str | Path) -> list[CorpusEntry]:
    """Load every `*.yaml` manifest under `corpus_root/public/` and `corpus_root/holdout/`.

    The partition comes from the directory a file lives in, never from a field in it.
    """
    root = Path(corpus_root)
    partitions = ((CorpusPartition.PUBLIC, "public"), (CorpusPartition.HOLDOUT, "holdout"))
    # `Path.glob` on a missing directory yields nothing, so a wrong root would load as an empty
    # corpus. An absent `holdout/` is normal; a root with neither partition is an error (D172).
    if not root.is_dir():
        raise CorpusRootError(f"corpus root {str(root)!r} does not exist or is not a directory")
    if not any((root / dirname).is_dir() for _, dirname in partitions):
        raise CorpusRootError(
            f"corpus root {str(root)!r} contains neither a public/ nor a holdout/ directory — "
            "it is not a corpus. An empty or absent holdout/ alone is fine."
        )

    entries: list[CorpusEntry] = []
    seen_ids: dict[str, CorpusPartition] = {}
    for partition, dirname in partitions:
        for manifest_path in sorted((root / dirname).glob("*.yaml")):
            entry = _load_entry(manifest_path, partition)
            if entry.id in seen_ids:
                first = seen_ids[entry.id]
                where = (
                    f"twice in {partition.value}/" if first is partition
                    else f"in both {first.value}/ and {partition.value}/"
                )
                raise DuplicateCorpusEntryError(f"corpus entry id {entry.id!r} declared {where}")
            seen_ids[entry.id] = partition
            entries.append(entry)
    return entries


class CorpusStore:
    """Loads a corpus once and exposes the two-method access surface (module docstring)."""

    def __init__(self, corpus_root: str | Path) -> None:
        self._entries = load_corpus(corpus_root)

    def public_entries(self) -> list[CorpusEntry]:
        """The only method search strategies and agents should call. Structurally cannot return
        a holdout entry: it filters by partition, it does not accept one as a parameter."""
        return [e for e in self._entries if e.partition is CorpusPartition.PUBLIC]

    def all_entries(self, *, acknowledge_holdout_access: bool) -> list[CorpusEntry]:
        """For validation/reporting code that needs the holdout partition (e.g. checking a
        calibrated interval on points it was not fitted on). Never call this from a search
        strategy or expose its result to an agent.
        """
        if not acknowledge_holdout_access:
            raise HoldoutAccessError(
                "all_entries() includes the holdout partition, which must never be visible to "
                "search or to any agent: it is what catches a search overfitting its benchmark. Pass "
                "acknowledge_holdout_access=True only from validation/reporting code with a "
                "genuine reason to see held-out points; if you're writing a search strategy or "
                "an agent tool, call public_entries() instead."
            )
        return list(self._entries)
