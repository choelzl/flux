"""Content-addressed result/artifact store (docs/04.md §8): every design point records inputs,
evaluator, and full lineage; deterministic replay is one query away. SQLite-backed for local use
— Postgres+S3 for shared deployments is future work, same interface (docs/04.md §8).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import flux_ir
from flux_evaluator_abi import Result

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

_VALID_KINDS = ("workload", "architecture", "mapping")


class ResultStore:
    """A content-addressed store for Flux IR documents and Evaluator results (docs/04.md §8).

    `get_result`/`find_results` return plain dicts (matching `Result.to_dict()`'s shape) rather
    than reconstructed `Result` dataclasses — the store is deliberately decoupled from any one
    evaluator's in-memory types (it has no idea which `Metric`/`Limiter` enum values a future
    evaluator might introduce); callers that need typed access reconstruct what they need from
    the dict.
    """

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)
        self._conn = sqlite3.connect(self.db_path)
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
        """Store an IR document, content-addressed (docs/04.md §3.4). Returns its hash.
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

    def get_document(self, content_hash: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT canonical_json FROM documents WHERE hash = ?", (content_hash,)
        ).fetchone()
        return json.loads(row[0]) if row else None

    def put_result(
        self,
        result: Result,
        *,
        workload_hash: str,
        arch_hash: str | None = None,
        mapping_hash: str | None = None,
    ) -> int:
        """Store a Result, tagged with the lineage that produced it. Returns the row id."""
        cursor = self._conn.execute(
            "INSERT INTO results "
            "(workload_hash, arch_hash, mapping_hash, evaluator, result_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                workload_hash,
                arch_hash,
                mapping_hash,
                result.provenance.evaluator,
                json.dumps(result.to_dict()),
                _now(),
            ),
        )
        self._conn.commit()
        assert cursor.lastrowid is not None
        return cursor.lastrowid

    def get_result(self, result_id: int) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT id, workload_hash, arch_hash, mapping_hash, evaluator, result_json, "
            "created_at FROM results WHERE id = ?",
            (result_id,),
        ).fetchone()
        return _row_to_dict(row) if row else None

    def find_results(
        self,
        *,
        workload_hash: str | None = None,
        arch_hash: str | None = None,
        evaluator: str | None = None,
    ) -> list[dict[str, Any]]:
        """Query stored results by any combination of lineage fields (all optional; None means
        'do not filter on this'). This is the warm-start query surface docs/04.md §6 describes
        ("every strategy can seed from prior runs").
        """
        clauses: list[str] = []
        params: list[Any] = []
        for column, value in (
            ("workload_hash", workload_hash),
            ("arch_hash", arch_hash),
            ("evaluator", evaluator),
        ):
            if value is not None:
                clauses.append(f"{column} = ?")
                params.append(value)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._conn.execute(
            "SELECT id, workload_hash, arch_hash, mapping_hash, evaluator, result_json, "
            f"created_at FROM results {where} ORDER BY id",
            params,
        ).fetchall()
        return [_row_to_dict(row) for row in rows]


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
