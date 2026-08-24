"""Real, content-hash-keyed persistent caching for real, deterministic, expensive external-tool
calls (docs/decisions.md D89) — the structurally different remainder D86 named directly: unlike
`flux_store.CachingEvaluator` (D19/D79/D86), there is no reducible sub-document to narrow a
dependency against here — real Yosys synthesis genuinely needs the *whole* design (Yosys flattens
hierarchy during `synth`), so the real cache key is exactly the real inputs the tool itself reads,
not a subset of a larger caller-supplied document. The same input always produces the same real
output (Yosys's own generic synthesis flow is deterministic given identical source text), so a
plain content-hash cache is the right, honest mechanism — not a placeholder for "real" caching,
a genuinely different but equally real one.

**Deliberately scoped to `synth.synthesize_and_measure` only, not `build.compile_and_run`/
`compose.compile_and_run_composite` (docs/decisions.md D89's own real, checked reason):**
`SynthesisResult` (`total_cells`, `cells_by_type`) is pure data with no filesystem references, so
a cache hit is unambiguously safe and correct. `HarnessRunResult`, by contrast, carries a real
`vcd_path` pointing at that specific run's own temp directory — a cache hit would have to either
fabricate a path to a trace file that was never actually written this time, or silently return a
stale path from a prior (possibly already-cleaned-up) run, either way a real, silent-wrong-answer
risk this repo's own design principles refuse to produce. Left uncached, named honestly, not
attempted.
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
    """A real, deterministic SHA256 hex digest over `parts` — real inputs only (source strings,
    dicts, tuples of JSON-serializable values), never a timestamp or anything random, so the same
    real inputs always produce the same real key.
    """
    payload = json.dumps(parts, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class ToolResultCache:
    """A real, disk-backed (SQLite) cache mapping a real content-hash key to a real, previously-
    computed tool result — the same real cross-run persistence `flux_store.ResultStore` gives
    Evaluator-ABI results, for a structurally different (non-Evaluator-ABI) shape. Stores/returns
    plain JSON-safe dicts, not a fixed result type, the same "store returns a dict, the caller
    reconstructs its own typed result" decoupling `flux_store.ResultStore` already established —
    this cache has no idea what `SynthesisResult` is, deliberately.
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
