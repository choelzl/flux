"""D931: an explicit record in a folder that does not exist yet is made there. It logged one line,
computed a result and left no evidence (the review's P3 persistence finding)."""

from __future__ import annotations

from flux_loop import LoopRequest, run_loop
from test_decision_history import Drafts


def test_a_record_in_a_missing_folder_is_made(tmp_path):
    db = tmp_path / "not" / "yet" / "there.db"
    out = run_loop(Drafts(3), LoopRequest(db=str(db), prototype=False, critique_rounds=0), proposer=None, log=lambda _m: None)
    assert out.decision is not None and db.exists() and db.stat().st_size > 0
