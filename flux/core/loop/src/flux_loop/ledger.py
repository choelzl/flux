"""The campaign LEDGER (docs/decisions.md D509): typed entries on the record, the loop's memory
across passes and relaunches.

An improve ladder remembers what it did to a design -- the sweep ran, a depth pass was taken
(or never ran), a verified alternative measured slower and stays a contender, a redesign pass
ended where the last one did, the design stands. Before D509 that memory was nine ad-hoc event
strings appended by the NLU problem and counted by string and digest, with one kind
back-filled from another for rows written before it existed. The vocabulary is an enum here,
an entry is a dataclass, and counting is one method; a kind that has a VOID twin (a pass noted
at its start and voided when it never ran, D506) is counted net of it.

The ledger writes through `Records` to the campaign's events (`kind`, `detail`), so a row
reads the same whether it was written by this class or by hand.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

__all__ = ["Entry", "Kind", "Ledger"]


class Kind(str, Enum):
    SWEEP = "sweep"                        # the pipeline sweep ran its full ladder for this design
    DEPTH_PASS = "depth_pass"              # a model pass to cut the design's logic depth was taken
    DEPTH_PASS_VOID = "depth_pass_void"    # ...and never ran (a server fault at its first turn)
    NOT_FASTER = "not_faster"              # a verified design measured no better than the one that stands
    CONTENDER = "contender"                # a verified alternative, slower, kept with its numbers
    CONTENDER_PASS = "contender_pass"      # the contender had its own depth pass
    IMPORTED = "imported"                  # a sibling campaign's design was tried here
    REDESIGN = "redesign"                  # the best refused attempt of a different-algorithm pass
    REDESIGN_STALLED = "redesign_stalled"  # a redesign pass ended where the last one did
    REST = "rest"                          # the design stands; nothing on the ladder was due
    DECISION = "decision"                  # an orchestrator's pick and why (D505)

    @property
    def void(self) -> "Kind | None":
        """The kind that cancels one of these, when there is one."""
        return Kind.DEPTH_PASS_VOID if self is Kind.DEPTH_PASS else None


@dataclass(frozen=True)
class Entry:
    kind: Kind
    part: str
    digest: str = ""
    detail: dict[str, Any] = field(default_factory=dict)
    at: str = ""


class Ledger:
    """The entries of one campaign. `records` is the loop's `Records` (or None: a run
    without a record keeps no ledger, and every count is zero)."""

    def __init__(self, records: Any) -> None:
        self._records = records

    @property
    def _store(self) -> Any | None:
        rec = self._records
        store = getattr(rec, "store", None) if rec is not None else None
        return store if store is not None and getattr(rec, "campaign_id", None) else None

    def note(self, kind: Kind, part: str, digest: str = "", **detail: Any) -> None:
        store = self._store
        if store is None:
            return
        try:
            store.append_event(self._records.campaign_id, kind.value,
                               {"op": part, "digest": digest, **detail})
        except Exception:  # noqa: BLE001 -- the ledger is memory, never a gate
            pass

    def entries(self, kind: Kind | None = None, part: str | None = None) -> list[Entry]:
        store = self._store
        if store is None:
            return []
        try:
            rows = store.events(self._records.campaign_id)
        except Exception:  # noqa: BLE001
            return []
        out: list[Entry] = []
        for e in rows:
            k = e.get("kind")
            try:
                kk = Kind(k)
            except ValueError:
                continue                                        # not a ledger row (a conclusion, a note)
            d = dict(e.get("detail") or {})
            p = str(d.pop("op", "") or "")
            if (kind is not None and kk is not kind) or (part is not None and p != part):
                continue
            out.append(Entry(kk, p, str(d.pop("digest", "") or ""), d, str(e.get("created_at") or "")))
        return out

    def count(self, kind: Kind, part: str, digest: str) -> int:
        """How many `kind` entries the ledger holds for this part's design (its digest), net
        of the kind's void twin -- the ladder's memory across relaunches (D503)."""
        rows = self.entries(part=part)
        n = sum(1 for r in rows if r.kind is kind and r.digest == digest)
        if kind.void is not None:
            n -= sum(1 for r in rows if r.kind is kind.void and r.digest == digest)
        return max(0, n)
