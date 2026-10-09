"""Content-addressed result/artifact store (docs/stores.md), SQLite-backed: every design point
records its inputs, evaluator and lineage, so replay is one query away.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import flux_ir

_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    hash TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    canonical_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workload_hash TEXT NOT NULL,
    arch_hash TEXT,
    mapping_hash TEXT,
    evaluator TEXT NOT NULL,
    result_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_results_workload ON results(workload_hash);
CREATE INDEX IF NOT EXISTS idx_results_arch ON results(arch_hash);
CREATE INDEX IF NOT EXISTS idx_results_evaluator ON results(evaluator);
"""

_VALID_KINDS = ("workload", "architecture", "mapping", "objective", "composition", "digest")   # digest: a library digest (D576)


class _Rows:
    """A statement's result, fetched whole under the lock: what a cursor offered and callers
    use (`fetchone`, `fetchall`, iteration, `lastrowid`, `rowcount`)."""

    def __init__(self, rows: list, lastrowid: Any, rowcount: int) -> None:
        self._rows, self.lastrowid, self.rowcount = rows, lastrowid, rowcount

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list:
        return list(self._rows)

    def __iter__(self):
        return iter(self._rows)


class _LockedConnection:
    """A SQLite connection any thread may use (D569): every statement runs under one lock and
    its rows are fetched before the lock is released, so two threads never interleave on one
    cursor. Everything else (`commit`, `rollback`, `close`, attributes) passes through."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        import threading

        self._conn = conn
        self._lock = threading.RLock()

    def execute(self, sql: str, params: Any = ()) -> _Rows:
        with self._lock:
            cur = self._conn.execute(sql, params)
            rows = cur.fetchall() if cur.description else []
            return _Rows(rows, cur.lastrowid, cur.rowcount)

    def executemany(self, sql: str, seq: Any) -> _Rows:
        with self._lock:
            cur = self._conn.executemany(sql, seq)
            return _Rows([], cur.lastrowid, cur.rowcount)

    def executescript(self, script: str) -> None:
        with self._lock:
            self._conn.executescript(script)

    def commit(self) -> None:
        with self._lock:
            self._conn.commit()

    def rollback(self) -> None:
        with self._lock:
            self._conn.rollback()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._conn, name)


class ResultStore:
    """A content-addressed store for documents (docs/stores.md), and the `results` table a
    campaign's trials write their measurements into (`CampaignStore.complete_trial`)."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)
        # usable from any thread, one statement at a time, every result fetched whole (D569)
        self._conn = _LockedConnection(sqlite3.connect(self.db_path, check_same_thread=False, timeout=60))
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "ResultStore":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    def put_document(self, kind: str, doc: dict[str, Any]) -> str:
        """Store an IR document, content-addressed (docs/ir.md). Returns its hash.
        Idempotent: storing the same document twice is a no-op, not a duplicate row.
        """
        if kind not in _VALID_KINDS:
            raise ValueError(f"unknown IR kind {kind!r}; expected one of {_VALID_KINDS}")
        content_hash = flux_ir.content_hash(doc)
        self._conn.execute(
            "INSERT OR IGNORE INTO documents (hash, kind, canonical_json, created_at) "
            "VALUES (?, ?, ?, ?)",
            (content_hash, kind, flux_ir.canonicalize(doc), _now()),
        )
        self._conn.commit()
        return content_hash

    def documents(self, kind: str) -> list[dict[str, Any]]:
        """Every stored document of `kind`, oldest first."""
        rows = self._conn.execute("SELECT canonical_json FROM documents WHERE kind = ? ORDER BY created_at, hash", (kind,)).fetchall()
        return [json.loads(r[0]) for r in rows]

    def get_document(self, content_hash: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT canonical_json FROM documents WHERE hash = ?", (content_hash,)
        ).fetchone()
        return json.loads(row[0]) if row else None

def _row_to_dict(row: tuple[Any, ...]) -> dict[str, Any]:
    result_id, workload_hash, arch_hash, mapping_hash, evaluator, result_json, created_at = row
    return {
        "id": result_id,
        "workload_hash": workload_hash,
        "arch_hash": arch_hash,
        "mapping_hash": mapping_hash,
        "evaluator": evaluator,
        "result": json.loads(result_json),
        "created_at": created_at,
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
