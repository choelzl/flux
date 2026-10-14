"""D1072: a design's text kept once in a record -- a design measured at every stage, again and again,
was a copy per trial (gigabytes of the same text, a Results page of minutes); rows written before
are moved over a few at a time, and a list reads the rows without the text."""

from __future__ import annotations

import hashlib
import json
import sqlite3

from flux_loop.types import Candidate
from flux_store import CampaignStore


def _record(path):
    store = CampaignStore(str(path))
    cid, _ = store.start_campaign({"o": 1}, "h", campaign_id="c")
    return store, cid


def _trial(store, cid, cand, stage):
    seq = store.begin_trial(cid, phase="loop", candidate=cand, candidate_key=cand["name"], strategy_kind="loop", stage=stage)
    store.complete_trial(cid, seq, status="ok", result=None, error=None, wall_clock_s=0.0)


def test_a_design_measured_at_every_stage_is_kept_once(tmp_path):
    store, cid = _record(tmp_path / "r.db")
    text = "module m; endmodule\n" * 1000
    for stage in ("check", "screen", "place"):
        _trial(store, cid, {"name": "m#1", "artifact": text, "knobs": {}}, stage)
    _trial(store, cid, {"name": "n=1", "artifact": "", "knobs": {"n": 1}}, "screen")
    assert store._conn.execute("SELECT count(*) FROM artifacts").fetchone()[0] == 1
    whole = store.trials(cid)
    assert [t.candidate.get("artifact") for t in whole] == [text, text, text, ""]
    assert whole[0].candidate["artifact"] is whole[1].candidate["artifact"], "one text in memory, not a copy a row"
    light = store.trials(cid, artifacts=False)
    assert all("artifact" not in t.candidate for t in light[:3])
    assert light[0].artifact_key[:16] == Candidate.from_record(whole[0].candidate).key() and light[3].artifact_key == ""
    store.close()


def test_rows_written_before_are_moved_over_and_read_meanwhile(tmp_path):
    db = tmp_path / "r.db"
    store, cid = _record(db)
    for i in range(5):
        _trial(store, cid, {"name": f"m#{i % 2}", "artifact": f"text {i % 2}"}, "screen")
    store.close()
    con = sqlite3.connect(db)                       # as a Flux before D1072 wrote them
    with con:
        for rid, cj in con.execute("SELECT id, candidate_json FROM trials").fetchall():
            name = json.loads(cj)["name"]
            con.execute("UPDATE trials SET candidate_json = ?, artifact_key = NULL WHERE id = ?",
                        (json.dumps({"name": name, "artifact": "text " + name[-1]}), rid))
        con.execute("DELETE FROM artifacts")
    con.close()
    store = CampaignStore(str(db))
    assert [t.candidate["artifact"] for t in store.trials(cid)] == ["text 0", "text 1", "text 0", "text 1", "text 0"]
    assert store.share_artifacts(batch=2) == (5, True)
    assert store.share_artifacts() == (0, True), "nothing left to move"
    rows = store._conn.execute("SELECT candidate_json, artifact_key FROM trials").fetchall()
    assert all("artifact" not in json.loads(cj) for cj, _k in rows)
    assert rows[0][1] == hashlib.sha256(b"text 0").hexdigest()
    assert store._conn.execute("SELECT count(*) FROM artifacts").fetchone()[0] == 2
    assert [t.candidate["artifact"] for t in store.trials(cid)] == ["text 0", "text 1", "text 0", "text 1", "text 0"]
    store.close()


def test_a_record_made_before_opens_read_only_and_writes_in_its_own_shape(tmp_path):
    """An Ask's sandbox opens the loop's record read-only: a record made before D1072 cannot gain the
    table there, and is read as it is; one that cannot gain it at a write keeps the text in the row."""
    db = tmp_path / "r.db"
    store, cid = _record(db)
    _trial(store, cid, {"name": "m#1", "artifact": "text"}, "screen")
    store.close()
    con = sqlite3.connect(db)
    with con:
        con.execute("UPDATE trials SET candidate_json = ?", (json.dumps({"name": "m#1", "artifact": "text"}),))
        con.execute("DROP TABLE artifacts")
        con.execute("ALTER TABLE trials DROP COLUMN artifact_key")
    con.close()
    db.chmod(0o444)
    try:
        ro = CampaignStore(str(db))
        assert [t.candidate["artifact"] for t in ro.trials(cid)] == ["text"]
        assert ro.trials(cid, artifacts=False)[0].artifact_key is None and ro.share_artifacts() == (0, False)
        ro.close()
    finally:
        db.chmod(0o644)
        for side in ("-wal", "-shm"):              # made read-only with it (a mount read-only makes none)
            (tmp_path / f"r.db{side}").unlink(missing_ok=True)
    store = CampaignStore(str(db))                 # writable again: the table comes, the old row moves on compact
    _trial(store, cid, {"name": "m#2", "artifact": "text"}, "screen")
    assert store.share_artifacts() == (1, True)
    assert [t.candidate["artifact"] for t in store.trials(cid)] == ["text", "text"]
    assert store._conn.execute("SELECT count(*) FROM artifacts").fetchone()[0] == 1
    store.close()
