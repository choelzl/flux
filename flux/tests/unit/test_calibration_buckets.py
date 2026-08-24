"""Conditioned residual pools (docs/decisions.md D318).

One pooled correction assumes a family's bias has the same shape throughout. For the interconnect
screen it measurably does not: over 45 fabrics placed whole, depth-2 designs read +0.19 optimistic
on frequency and depth-1 read +0.35, while the pooled figure of +0.29 is wrong for both, in
opposite directions.
"""

from __future__ import annotations


import pytest
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


def test_depth_is_the_bucket():
    import flux_interconnect.flow as demo

    assert demo.calibration_bucket({"stages": [{"in": 4}]}) == "depth1"
    assert demo.calibration_bucket({"stages": [{"in": 4}, {"in": 7}]}) == "depth2"


def test_a_stageless_fabric_is_one_rank():
    """A butterfly is a radix, not a stage list, and is still a single rank of selectors."""
    import flux_interconnect.flow as demo

    assert demo.calibration_bucket({"radix": 8}) == "depth1"


def test_very_deep_fabrics_share_one_pool():
    """Only one fabric in this study is deeper than four ranks; a pool of one corrects nothing
    and splitting further just starves every bucket."""
    import flux_interconnect.flow as demo

    assert demo.calibration_bucket({"stages": [{}] * 9}) == "depth4"


def test_a_missing_spec_has_no_bucket():
    """Then the record lands in the pooled set, which is the honest default rather than a guess."""
    import flux_interconnect.flow as demo

    assert demo.calibration_bucket(None) is None


# -- the correction actually applied ---------------------------------------------------------


def _cal_store(tmp_path, rows):
    """rows are (composed, placed, bucket) — real pairs in the shape learn_from_placement writes."""
    from flux_evaluator_interconnect_phys.adapter import EVALUATOR_ID

    cal = CalibrationStore(tmp_path / "c.calibration.db")
    for i, (comp, placed, bucket) in enumerate(rows):
        cal.add_record(workload_hash="w", arch_hash=f"a{i}", evaluator=EVALUATOR_ID,
                       metric="fmax_mhz", predicted_value=float(comp),
                       reference_value=float(placed), reference_source="whole-fabric",
                       bucket=bucket)
    cal.close()
    return str(tmp_path / "c.db")


DEPTH2 = {"stages": [{"in": 4}, {"in": 7}]}
DEPTH1 = {"stages": [{"in": 28}]}


def test_the_correction_uses_the_fabrics_own_depth(tmp_path):
    """The point of bucketing. Two fabrics with the SAME composed estimate must come out
    differently, because their pools measured different biases."""
    import flux_interconnect.flow as demo

    db = _cal_store(tmp_path, [(1000.0, 750.0, "depth1")] * 8 + [(1000.0, 900.0, "depth2")] * 8)
    d1, note1 = demo.corrected_fmax(db, DEPTH1, 1000.0)
    d2, note2 = demo.corrected_fmax(db, DEPTH2, 1000.0)
    assert d1 == pytest.approx(750.0, rel=0.02) and "depth1" in note1
    assert d2 == pytest.approx(900.0, rel=0.02) and "depth2" in note2


def test_a_thin_bucket_falls_back_to_the_pool(tmp_path):
    """Four records is not a correction. `_MIN_BUCKET_RECORDS` sends it to the pooled pool
    rather than fitting on almost nothing — the failure D101 recorded."""
    import flux_interconnect.flow as demo

    db = _cal_store(tmp_path, [(1000.0, 800.0, "depth1")] * 10 + [(1000.0, 500.0, "depth3")] * 2)
    value, note = demo.corrected_fmax(db, {"stages": [{"in": 2}] * 3}, 1000.0)
    assert "pooled" in note
    assert value > 600.0, "the two depth-3 records must not drag the correction on their own"


def test_no_calibration_leaves_the_estimate_alone(tmp_path):
    """An uncorrected number is honest; an invented correction is not."""
    import flux_interconnect.flow as demo

    empty = str(tmp_path / "nothing.db")
    assert demo.corrected_fmax(empty, DEPTH2, 900.0) == (900.0, "")


def test_the_correction_is_reported_not_applied_silently(tmp_path):
    """A caller has to be able to SHOW the correction: a frontier that quietly rewrites its own
    frequencies is indistinguishable from one that measured them."""
    import flux_interconnect.flow as demo

    db = _cal_store(tmp_path, [(1000.0, 800.0, "depth2")] * 8)
    _, note = demo.corrected_fmax(db, DEPTH2, 1000.0)
    assert note and "n=" in note


def test_an_impossible_residual_is_refused(tmp_path):
    """A mean residual at or below -100% would divide by zero or flip the sign."""
    import flux_interconnect.flow as demo

    db = _cal_store(tmp_path, [(1.0, 1000.0, "depth2")] * 8)
    value, _ = demo.corrected_fmax(db, DEPTH2, 900.0)
    assert value > 0
