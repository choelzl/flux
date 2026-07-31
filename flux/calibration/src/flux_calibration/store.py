"""Calibration store (docs/04.md §5): ground-truth measurements versioned alongside the model,
with residual tracking — the machinery "real" calibration needs.

**What this is calibrated against, honestly:** started with only **cross-model residuals** (two
independently-developed cost models, ZigZag and Timeloop, evaluating the exact same Flux IR
document and disagreeing) — a real, honestly-labelled, weaker signal than silicon: it can tell
you "these two models disagree by 3x here," but not which one (if either) is *right*. Real RTL
simulation now exists too (`evaluators/rtl/`, `reference_source="rtl_sim"`) — an actual measured
ground truth, not another analytic estimate, though still only one small hand-written design, no
synthesis/place-and-route in the loop, and not silicon. Every record's `reference_source` says
exactly what kind of reference it is (`cross_model:<evaluator>`, `rtl_sim`, and `silicon` once
that exists), so nothing downstream can mistake one for another. See docs/calibration-report.md.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS calibration_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workload_hash TEXT NOT NULL,
    arch_hash TEXT,
    evaluator TEXT NOT NULL,
    metric TEXT NOT NULL,
    predicted_value REAL NOT NULL,
    reference_value REAL NOT NULL,
    reference_source TEXT NOT NULL,
    relative_residual REAL NOT NULL,
    caveat TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_calibration_evaluator_metric
    ON calibration_records(evaluator, metric);
"""


@dataclass(frozen=True, slots=True)
class ResidualStats:
    """Summary of relative residuals `(predicted - reference) / reference` for one
    (evaluator, metric) pair, over whatever calibration records currently exist for it.
    """

    n: int
    mean_relative_residual: float
    std_relative_residual: float
    records_excluded_for_caveat: int


class CalibrationStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "CalibrationStore":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    def add_record(
        self,
        *,
        workload_hash: str,
        arch_hash: str | None,
        evaluator: str,
        metric: str,
        predicted_value: float,
        reference_value: float,
        reference_source: str,
        caveat: str | None = None,
    ) -> int:
        if reference_value == 0:
            raise ValueError("reference_value must be non-zero to compute a relative residual")
        relative_residual = (predicted_value - reference_value) / reference_value
        cursor = self._conn.execute(
            "INSERT INTO calibration_records "
            "(workload_hash, arch_hash, evaluator, metric, predicted_value, reference_value, "
            "reference_source, relative_residual, caveat, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                workload_hash,
                arch_hash,
                evaluator,
                metric,
                predicted_value,
                reference_value,
                reference_source,
                relative_residual,
                caveat,
                _now(),
            ),
        )
        self._conn.commit()
        assert cursor.lastrowid is not None
        return cursor.lastrowid

    def records_for(
        self, evaluator: str, metric: str, *, exclude_caveated: bool = True
    ) -> list[dict[str, Any]]:
        query = (
            "SELECT id, workload_hash, arch_hash, evaluator, metric, predicted_value, "
            "reference_value, reference_source, relative_residual, caveat, created_at "
            "FROM calibration_records WHERE evaluator = ? AND metric = ?"
        )
        if exclude_caveated:
            query += " AND caveat IS NULL"
        rows = self._conn.execute(query, (evaluator, metric)).fetchall()
        columns = [
            "id", "workload_hash", "arch_hash", "evaluator", "metric", "predicted_value",
            "reference_value", "reference_source", "relative_residual", "caveat", "created_at",
        ]
        return [dict(zip(columns, row)) for row in rows]

    def has_exact_match(self, evaluator: str, metric: str, workload_hash: str, arch_hash: str | None) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM calibration_records WHERE evaluator = ? AND metric = ? "
            "AND workload_hash = ? AND (arch_hash = ? OR (arch_hash IS NULL AND ? IS NULL)) LIMIT 1",
            (evaluator, metric, workload_hash, arch_hash, arch_hash),
        ).fetchone()
        return row is not None

    def residual_stats(
        self, evaluator: str, metric: str, *, exclude_caveated: bool = True
    ) -> ResidualStats | None:
        included = self.records_for(evaluator, metric, exclude_caveated=exclude_caveated)
        if not included:
            return None
        excluded_count = 0
        if exclude_caveated:
            excluded_count = len(self.records_for(evaluator, metric, exclude_caveated=False)) - len(included)

        residuals = [r["relative_residual"] for r in included]
        n = len(residuals)
        mean = sum(residuals) / n
        if n > 1:
            variance = sum((r - mean) ** 2 for r in residuals) / (n - 1)
            std = variance**0.5
        else:
            std = 0.0
        return ResidualStats(
            n=n, mean_relative_residual=mean, std_relative_residual=std,
            records_excluded_for_caveat=excluded_count,
        )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
