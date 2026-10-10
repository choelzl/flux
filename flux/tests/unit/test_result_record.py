"""A trial's `Result` (docs/records.md, D964): metrics with their method, and provenance. It
round-trips; a row of the older, wider shape reads back; a record written before D964 is
read and written as it is."""

from __future__ import annotations

import sqlite3
from contextlib import closing

from flux_store import CampaignStore
from flux_store.result import Estimate, Method, Provenance, Result


def test_a_result_round_trips():
    r = Result(metrics={"area_um2": Estimate(12.5, Method.SIMULATED)}, provenance=Provenance("flux@records", {"stage": "place"}))
    assert Result.from_dict(r.to_dict()) == r
    assert r.to_dict()["metrics"]["area_um2"] == {"value": 12.5, "method": "simulated"}


def test_an_older_wider_row_reads_back():
    old = {"metrics": {"m": {"value": 3.0, "ci_low": 3.0, "ci_high": 3.0, "unit": "", "method": "analytic"}},
           "validity": {"ok": True}, "domain": {"in_domain": True}, "bottleneck": {"limiter": "none"},
           "provenance": {"evaluator": "flux@records", "inputs": {}, "usd_cost": None},
           "escalation": {"recommended": False}, "metric_domains": {}}
    assert Result.from_dict(old) == Result({"m": Estimate(3.0, Method.ANALYTIC)}, Provenance("flux@records"))


def test_a_record_written_before_d964_is_read_and_written_as_it_is(tmp_path):
    db = str(tmp_path / "old.db")
    with closing(sqlite3.connect(db)) as c:
        c.executescript("""
        CREATE TABLE results (id INTEGER PRIMARY KEY AUTOINCREMENT, workload_hash TEXT NOT NULL, arch_hash TEXT,
            mapping_hash TEXT, evaluator TEXT NOT NULL, result_json TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE INDEX idx_results_workload ON results(workload_hash);
        CREATE INDEX idx_results_arch ON results(arch_hash);
        CREATE TABLE campaigns (campaign_id TEXT PRIMARY KEY, objective_hash TEXT NOT NULL, objective_json TEXT NOT NULL,
            status TEXT NOT NULL, phase TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE trials (id INTEGER PRIMARY KEY AUTOINCREMENT, campaign_id TEXT NOT NULL REFERENCES campaigns(campaign_id),
            seq INTEGER NOT NULL, phase TEXT NOT NULL, stage TEXT, candidate_json TEXT NOT NULL, candidate_key TEXT NOT NULL,
            workload_hash TEXT NOT NULL, arch_hash TEXT, result_id INTEGER REFERENCES results(id), status TEXT NOT NULL,
            error TEXT, strategy_kind TEXT NOT NULL, cache_hit INTEGER NOT NULL DEFAULT 0,
            wall_clock_s REAL NOT NULL DEFAULT 0.0, usd_cost REAL, created_at TEXT NOT NULL, UNIQUE (campaign_id, seq));
        INSERT INTO campaigns VALUES ('c', 'h', '{}', 'running', 'p', 't');
        INSERT INTO results VALUES (1, '', 'a', NULL, 'flux@records',
            '{"metrics": {"m": {"value": 1.0, "ci_low": 1.0, "ci_high": 1.0, "unit": "", "method": "simulated"}}, "provenance": {"evaluator": "flux@records"}}', 't');
        INSERT INTO trials (campaign_id, seq, phase, candidate_json, candidate_key, workload_hash, result_id, status, strategy_kind, created_at)
            VALUES ('c', 1, 'p', '{}', 'k', '', 1, 'ok', 's', 't');
        """)
    with CampaignStore(db) as store:
        (old,) = store.trials("c")
        assert old.result.value_of("m") == 1.0, "a reader reads the old schema as it is"
        seq = store.begin_trial("c", phase="p", candidate={"a": 1}, candidate_key="k2", strategy_kind="s")
        store.complete_trial("c", seq, status="ok", result=Result({"m": Estimate(2.0, Method.SIMULATED)}, Provenance("x")),
                             error=None, wall_clock_s=0.0)
        assert [t.result.value_of("m") for t in store.trials("c")] == [1.0, 2.0]
    with closing(sqlite3.connect(db)) as c:
        assert c.execute("SELECT workload_hash FROM trials").fetchall() == [("",), ("",)]
        assert c.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
