"""Benchmark corpus + holdout discipline (docs/stores.md, docs/roadmap.md): a partition of the
corpus is never visible to search or to any agent — the mechanism that caught CHIA's gem5-
alignment agent overfitting (docs/landscape.md): the agent tuned a threshold "calibrated to
[training benchmark]'s inner loop," which then degraded a holdout benchmark it could not see.
"Enforced by the store, not by convention" (docs/stores.md) means exactly what it says: there is
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
class Objective:
    """What "best" means for a corpus entry (docs/decisions.md D58) — the piece
    `corpus/README.md` had named "not implemented" since D11: a (workload, architecture) pair
    alone isn't a benchmark *problem* in gap-analysis.md G13's own sense until it also declares
    what's being optimized. `metric` names one of `flux_evaluator_abi.Result.metrics`' real keys
    (e.g. `"latency_cycles"`, `"energy_pj"`) — not validated against a fixed enum here, since new
    evaluators can introduce new metric names and this module has no reason to know the full set.
    """

    metric: str
    minimize: bool

    def to_dict(self) -> dict[str, Any]:
        return {"metric": self.metric, "minimize": self.minimize}


@dataclass(frozen=True, slots=True)
class CorpusEntry:
    """One benchmark point: a (workload, architecture) pair plus enough metadata to explain why
    it's in the corpus at all. Paths are repo-relative, matching how every test in this repo
    already references `ir/*/examples/*.yaml` (see e.g. tests/integration/*_live.py).

    `objective` is optional (docs/decisions.md D58), not required, so existing/synthetic entries
    that predate it stay valid — `leaderboard.py`'s ranking functions are the ones that actually
    require it, raising a clear, named error on an entry that lacks one, rather than this loader
    silently rejecting a structurally-fine manifest that just isn't rankable yet.
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
    """Raised by `CorpusStore.all_entries()` when called without explicitly acknowledging that
    the result includes the holdout partition. Not raised by `public_entries()`, which simply
    never has holdout entries to return in the first place.
    """


class CorpusRootError(Exception):
    """Raised when `load_corpus` is pointed at something that isn't a corpus at all — a path that
    doesn't exist, or one holding neither a `public/` nor a `holdout/` directory. Distinct from
    "this corpus has no entries", which is a legitimate (if unhelpful) state and returns `[]`.
    """


class DuplicateCorpusEntryError(Exception):
    """Raised when two corpus manifests (public and/or holdout) declare the same entry `id` —
    ambiguous which partition it belongs to, which is exactly the kind of mistake this module
    exists to make impossible to make silently.
    """


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
    """Load every `*.yaml` manifest under `corpus_root/public/` and `corpus_root/holdout/`,
    tagging each entry's partition from the directory it was loaded from — the partition is a
    property of *where the file lives*, not a field a manifest author could get wrong or a
    loader caller could override.
    """
    root = Path(corpus_root)
    partitions = ((CorpusPartition.PUBLIC, "public"), (CorpusPartition.HOLDOUT, "holdout"))
    # A wrong `corpus_root` used to load silently as an empty corpus: `Path.glob` on a directory
    # that doesn't exist yields nothing rather than raising, so a typo'd or mis-rooted path made
    # `public_entries()` return `[]` — indistinguishable from "this corpus genuinely has no public
    # entries". `leaderboard.py`'s own docstring names exactly this anti-pattern ("never a silently
    # empty standings list that could be mistaken for 'checked, genuinely found nothing'") while
    # its data source practised it. An *absent* `holdout/` is fine and normal; a root with neither
    # partition directory is a misconfiguration, not a corpus (docs/decisions.md D172).
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
                "search or to any agent (docs/roadmap.md — this is precisely the mechanism that "
                "caught CHIA's gem5-alignment agent overfitting). Pass "
                "acknowledge_holdout_access=True only from validation/reporting code with a "
                "genuine reason to see held-out points; if you're writing a search strategy or "
                "an agent tool, call public_entries() instead."
            )
        return list(self._entries)
