"""Content-hash-keyed persistent cache for deterministic, expensive tool calls (D89).

Yosys synthesis reads the whole design and is deterministic, so the key is exactly the inputs
the tool reads. Scoped to `synth.synthesize_and_measure` only: `SynthesisResult` is pure data,
whereas `HarnessRunResult` carries a `vcd_path` into a run's temp directory that a cache hit
could not honestly return.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tool_results (
    key TEXT PRIMARY KEY,
    result_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def content_key(*parts: Any) -> str:
    """A deterministic SHA256 hex digest over `parts` (JSON-serializable inputs only)."""
    payload = json.dumps(parts, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class ToolResultCache:
    """A disk-backed (SQLite) cache from a content-hash key to a previously computed tool
    result, as plain JSON-safe dicts; the caller reconstructs its own typed result.
    """

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "ToolResultCache":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    def get(self, key: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT result_json FROM tool_results WHERE key = ?", (key,)
        ).fetchone()
        return json.loads(row[0]) if row is not None else None

    def put(self, key: str, value: dict[str, Any]) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO tool_results (key, result_json, created_at) VALUES (?, ?, ?)",
            (key, json.dumps(value), datetime.now(timezone.utc).isoformat()),
        )
        self._conn.commit()
