"""Calibration store (docs/calibration.md): reference measurements versioned alongside the model,
with residual tracking.

Every record's `reference_source` says what kind of reference it is: `cross_model:<evaluator>`
(another cost model; shows disagreement, not which is right), `rtl_sim` (a measured simulation),
or `silicon`.
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
    -- Optional sub-pool within an (evaluator, metric) family (docs/decisions.md D318). NULL for
    -- every caller that does not use it, which is the pre-existing behaviour exactly: an
    -- unbucketed query still pools everything.
    bucket TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_calibration_evaluator_metric
    ON calibration_records(evaluator, metric);


-- Ground-truth *attempts*, as distinct from measurements (docs/decisions.md D114). A reference
-- run that yields no shared metric produces no `calibration_records` row, so nothing recorded
-- that the budget was spent — the escalation gate then re-ran a real simulator on every call.
-- Deliberately a separate table rather than a sentinel row in `calibration_records`: a sentinel
-- would need fabricated predicted/reference values and filtering at every read site, and
-- `relative_residual NOT NULL` has no honest value for "nothing was comparable".
-- Metric-independent by design: an attempt buys one reference *run* for a candidate, whatever
-- metrics come back. Keyed by reference_source too, so buying one reference never masks not
-- having bought another. Created on open, so existing stores gain it with no migration.
CREATE TABLE IF NOT EXISTS calibration_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workload_hash TEXT NOT NULL,
    arch_hash TEXT,
    evaluator TEXT NOT NULL,
    reference_source TEXT NOT NULL,
    yielded_records INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_calibration_attempts_lookup
    ON calibration_attempts(evaluator, reference_source, workload_hash);
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
    # How many distinct (workload_hash, arch_hash) points the `n` records cover (D171): `n` is
    # the mean/std denominator, this is the trust gate's (a point recorded three times is one
    # measurement). `None` when unknown; the `_MIN_TRUSTED_N` gate then falls back to `n`.
    distinct_points: int | None = None


class CalibrationStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.executescript(_SCHEMA)
        # Older stores lack the column; adding it leaves existing rows NULL ("not bucketed").
        if "bucket" not in {r[1] for r in self._conn.execute(
                "PRAGMA table_info(calibration_records)")}:
            self._conn.execute("ALTER TABLE calibration_records ADD COLUMN bucket TEXT")
        # after the column exists, not in `_SCHEMA`: on an older store the index would fail the open
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_calibration_bucket "
                           "ON calibration_records(evaluator, metric, bucket)")
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
        bucket: str | None = None,
    ) -> int:
        if reference_value == 0:
            raise ValueError("reference_value must be non-zero to compute a relative residual")
        relative_residual = (predicted_value - reference_value) / reference_value
        cursor = self._conn.execute(
            "INSERT INTO calibration_records "
            "(workload_hash, arch_hash, evaluator, metric, predicted_value, reference_value, "
            "reference_source, relative_residual, caveat, bucket, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                bucket,
                _now(),
            ),
        )
        self._conn.commit()
        assert cursor.lastrowid is not None
        return cursor.lastrowid

    def evaluator_metric_pairs(self) -> list[tuple[str, str]]:
        """Every (evaluator, metric) family with at least one record (D243)."""
        rows = self._conn.execute(
            "SELECT DISTINCT evaluator, metric FROM calibration_records "
            "ORDER BY evaluator, metric"
        ).fetchall()
        return [(r[0], r[1]) for r in rows]

    def records_for(
        self, evaluator: str, metric: str, *, exclude_caveated: bool = True,
        bucket: str | None = None,
    ) -> list[dict[str, Any]]:
        """Records for one (evaluator, metric) family, optionally narrowed to one `bucket`.

        A bucket (D318) lets a family's bias take more than one shape (e.g. the interconnect
        screen's frequency bias differs by pipeline depth). Omitting `bucket` pools everything.
        A thin bucket just returns few records; `calibrate_estimate`'s `_MIN_TRUSTED_N` gate
        then widens instead of correcting.
        """
        query = (
            "SELECT id, workload_hash, arch_hash, evaluator, metric, predicted_value, "
            "reference_value, reference_source, relative_residual, caveat, bucket, created_at "
            "FROM calibration_records WHERE evaluator = ? AND metric = ?"
        )
        params: list[Any] = [evaluator, metric]
        if bucket is not None:
            query += " AND bucket = ?"
            params.append(bucket)
        if exclude_caveated:
            query += " AND caveat IS NULL"
        rows = self._conn.execute(query, params).fetchall()
        columns = [
            "id", "workload_hash", "arch_hash", "evaluator", "metric", "predicted_value",
            "reference_value", "reference_source", "relative_residual", "caveat", "bucket",
            "created_at",
        ]
        return [dict(zip(columns, row)) for row in rows]


    def exact_match_caveat(
        self, evaluator: str, metric: str, workload_hash: str, arch_hash: str | None
    ) -> str | None:
        """The `caveat` on this exact (workload, arch) record, if any (D112): whether a measured
        point is known not to be representative. `has_exact_match` answers only "was it measured".
        """
        row = self._conn.execute(
            "SELECT caveat FROM calibration_records WHERE evaluator = ? AND metric = ? "
            "AND workload_hash = ? AND (arch_hash = ? OR (arch_hash IS NULL AND ? IS NULL)) "
            "AND caveat IS NOT NULL LIMIT 1",
            (evaluator, metric, workload_hash, arch_hash, arch_hash),
        ).fetchone()
        return row[0] if row else None

    def has_exact_match(self, evaluator: str, metric: str, workload_hash: str, arch_hash: str | None) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM calibration_records WHERE evaluator = ? AND metric = ? "
            "AND workload_hash = ? AND (arch_hash = ? OR (arch_hash IS NULL AND ? IS NULL)) LIMIT 1",
            (evaluator, metric, workload_hash, arch_hash, arch_hash),
        ).fetchone()
        return row is not None

    def residual_stats(
        self, evaluator: str, metric: str, *, exclude_caveated: bool = True,
        bucket: str | None = None,
    ) -> ResidualStats | None:
        """Residual statistics for one (evaluator, metric), optionally narrowed to one `bucket`.

        Omitting `bucket` pools everything. See `records_for` (D318).
        """
        included = self.records_for(evaluator, metric, exclude_caveated=exclude_caveated,
                                    bucket=bucket)
        if not included:
            return None
        excluded_count = 0
        if exclude_caveated:
            excluded_count = len(self.records_for(evaluator, metric, exclude_caveated=False,
                                                  bucket=bucket)) - len(included)

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
            distinct_points=len({(r["workload_hash"], r["arch_hash"]) for r in included}),
        )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
