"""`FactStore` — persistence for mined facts (docs/decisions.md D250), deliberately SEPARATE
from the BM25 knowledge corpus: mined measured facts and licensed spec text are different
provenance classes (D243/D244), and mixing them in one index would blur exactly the boundary
the `pointers` field keeps sharp.

Three commitments:

1. **Identity is content.** A fact's id is the hash of (kind, statement, pointers) — re-mining
   the same stores yields the same facts, and `put_facts` is idempotent, never a duplicate row.
2. **Staleness is checkable, not assumed away.** Every fact points at the exact store rows it
   was computed from; `verify()` re-mines the pointed store and reports per fact:
   `intact` (the same statement is still derivable), `dangling` (the source store is gone or
   unreadable), or `superseded` (the store exists but no longer yields this statement — new
   trials changed the evidence). A recalled fact can always be re-derived or shown broken.
3. **Recall is filtering, not ranking.** `facts(kind=, contains=)` — cheap, exact, and honest
   about being so. Retrieval-quality ranking belongs to the corpus index; a measured fact is
   recalled by what it is, not by lexical similarity.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .mining import Fact, mine_knowledge


def fact_id(fact: Fact | dict[str, Any]) -> str:
    """Content-derived identity: same fact from the same rows -> same id, always."""
    d = fact.to_dict() if isinstance(fact, Fact) else fact
    return hashlib.sha256(json.dumps(
        {"kind": d["kind"], "statement": d["statement"], "pointers": d["pointers"]},
        sort_keys=True).encode()).hexdigest()


@dataclass(frozen=True)
class StoredFact:
    id: str
    fact: dict[str, Any]
    mined_at: str

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "fact": self.fact, "mined_at": self.mined_at}


class FactStore:
    def __init__(self, db_path: str | Path) -> None:
        self._conn = sqlite3.connect(str(db_path))
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS facts ("
            "  id TEXT PRIMARY KEY,"
            "  kind TEXT NOT NULL,"
            "  statement TEXT NOT NULL,"
            "  fact_json TEXT NOT NULL,"
            "  mined_at TEXT NOT NULL)"
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "FactStore":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    def put_facts(self, facts: list[Fact] | list[dict[str, Any]]) -> list[str]:
        """Store facts, idempotently (content identity). Returns the ids in input order —
        already-present facts return their existing id, never a duplicate row."""
        ids = []
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for fact in facts:
            d = fact.to_dict() if isinstance(fact, Fact) else fact
            fid = fact_id(d)
            self._conn.execute(
                "INSERT OR IGNORE INTO facts (id, kind, statement, fact_json, mined_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (fid, d["kind"], d["statement"], json.dumps(d, sort_keys=True), now),
            )
            ids.append(fid)
        self._conn.commit()
        return ids

    def facts(
        self, *, kind: str | None = None, contains: str | None = None
    ) -> list[StoredFact]:
        """Exact filtering: by fact kind and/or case-insensitive substring of the statement."""
        query = "SELECT id, fact_json, mined_at FROM facts"
        clauses, params = [], []
        if kind is not None:
            clauses.append("kind = ?")
            params.append(kind)
        if contains is not None:
            clauses.append("statement LIKE ? COLLATE NOCASE")
            params.append(f"%{contains}%")
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY mined_at, id"
        return [
            StoredFact(id=r[0], fact=json.loads(r[1]), mined_at=r[2])
            for r in self._conn.execute(query, params).fetchall()
        ]

    def verify(self, stored: StoredFact) -> str:
        """Re-derive the fact from the store it points at: `intact` | `dangling` |
        `superseded`. Verification is re-mining, not a row-existence probe — the statement is
        computed from the rows, so 'the same statement is still derivable' is exactly the
        claim a consumer needs, and anything weaker could bless a fact whose numbers moved."""
        pointers = stored.fact.get("pointers", {})
        campaign_db = pointers.get("campaign_db")
        calibration_db = pointers.get("calibration_db")
        source = campaign_db or calibration_db
        if source is None or not Path(source).is_file():
            return "dangling"
        try:
            mined = mine_knowledge(
                campaign_db_paths=[campaign_db] if campaign_db else None,
                calibration_db_paths=[calibration_db] if calibration_db else None,
            )
        except Exception:  # noqa: BLE001 — an unreadable store is dangling, not a crash
            return "dangling"
        current = {(f.kind, f.statement) for f in mined.facts}
        if (stored.fact["kind"], stored.fact["statement"]) in current:
            return "intact"
        return "superseded"
