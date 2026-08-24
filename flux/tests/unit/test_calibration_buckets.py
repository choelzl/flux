"""Conditioned residual pools (docs/decisions.md D318).

One pooled correction assumes a family's bias has the same shape throughout. For the interconnect
screen it measurably does not: over 45 fabrics placed whole, depth-2 designs read +0.19 optimistic
on frequency and depth-1 read +0.35, while the pooled figure of +0.29 is wrong for both, in
opposite directions.
"""

from __future__ import annotations


from flux_calibration.store import CalibrationStore



def _store(tmp_path, rows):
    """rows are (predicted, reference, bucket)."""
    cal = CalibrationStore(tmp_path / "c.db")
    for i, (pred, ref, bucket) in enumerate(rows):
        cal.add_record(workload_hash="w", arch_hash=f"a{i}", evaluator="e", metric="m",
                       predicted_value=pred, reference_value=ref,
                       reference_source="test", bucket=bucket)
    return cal


def test_a_bucket_narrows_the_pool(tmp_path):
    cal = _store(tmp_path, [(100.0, 80.0, "depth1"), (100.0, 80.0, "depth1"),
                            (100.0, 95.0, "depth2")])
    assert len(cal.records_for("e", "m")) == 3
    assert len(cal.records_for("e", "m", bucket="depth1")) == 2
    assert len(cal.records_for("e", "m", bucket="depth2")) == 1


def test_the_conditioned_correction_differs_from_the_pooled_one(tmp_path):
    """The whole point. If every bucket agreed with the pool there would be nothing to gain."""
    cal = _store(tmp_path, [(100.0, 74.0, "depth1"), (100.0, 74.0, "depth1"),
                            (100.0, 96.0, "depth2"), (100.0, 96.0, "depth2")])
    pooled = [r["relative_residual"] for r in cal.records_for("e", "m")]
    d2 = [r["relative_residual"] for r in cal.records_for("e", "m", bucket="depth2")]
    assert sum(pooled) / len(pooled) > sum(d2) / len(d2) + 0.1


def test_omitting_the_bucket_pools_everything_exactly_as_before(tmp_path):
    """Every existing caller passes no bucket and must be unaffected."""
    cal = _store(tmp_path, [(100.0, 80.0, "depth1"), (100.0, 90.0, None)])
    assert len(cal.records_for("e", "m")) == 2


def test_unbucketed_records_are_not_returned_for_a_bucket(tmp_path):
    """A record with no bucket is not evidence about any particular one."""
    cal = _store(tmp_path, [(100.0, 90.0, None)])
    assert cal.records_for("e", "m", bucket="depth1") == []


def test_a_store_written_before_buckets_existed_still_opens(tmp_path):
    """The column is added on open, leaving old rows NULL -- which is what 'not bucketed' means.
    A calibration store outlives the code that fills it and must never need a migration step."""
    import sqlite3

    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.executescript(
        "CREATE TABLE calibration_records (id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " workload_hash TEXT NOT NULL, arch_hash TEXT, evaluator TEXT NOT NULL,"
        " metric TEXT NOT NULL, predicted_value REAL NOT NULL, reference_value REAL NOT NULL,"
        " reference_source TEXT NOT NULL, relative_residual REAL NOT NULL, caveat TEXT,"
        " created_at TEXT NOT NULL);")
    con.execute("INSERT INTO calibration_records (workload_hash, evaluator, metric,"
                " predicted_value, reference_value, reference_source, relative_residual,"
                " created_at) VALUES ('w','e','m',100.0,80.0,'old',0.25,'2020-01-01')")
    con.commit(); con.close()

    cal = CalibrationStore(path)
    (kept,) = cal.records_for("e", "m")
    assert kept["relative_residual"] == 0.25 and kept["bucket"] is None


def test_caveated_records_are_still_excluded_within_a_bucket(tmp_path):
    """Bucketing must not quietly re-admit records the pool already knows not to trust."""
    cal = CalibrationStore(tmp_path / "c.db")
    cal.add_record(workload_hash="w", arch_hash="a", evaluator="e", metric="m",
                   predicted_value=100.0, reference_value=80.0, reference_source="t",
                   bucket="depth1", caveat="out of domain")
    assert cal.records_for("e", "m", bucket="depth1") == []
    assert len(cal.records_for("e", "m", bucket="depth1", exclude_caveated=False)) == 1


# -- the demo's choice of bucket -------------------------------------------------------------


# -- the correction actually applied ---------------------------------------------------------



DEPTH2 = {"stages": [{"in": 4}, {"in": 7}]}
DEPTH1 = {"stages": [{"in": 28}]}

