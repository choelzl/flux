"""D840: a design's name is its own across starts. Each start numbered its drafts from 1 again, so
a later start's `digits#1` was another design than the first start's, and Results, the Loops list
and the decision mixed them: the number carries on from the record; Results tells designs apart by
what they are and picks the decided one by its key, else by its numbers."""

from __future__ import annotations

import re

from test_agent_sessions import _digits, _fake

from flux_loop import PromptProblem, request_for, run_loop
from flux_web.results import _decided, content_key


def _names(db: str) -> list[str]:
    from flux_store import CampaignStore

    store = CampaignStore(db)
    try:
        return [str((t.candidate or {}).get("name")) for c in store.list_campaigns() for t in store.trials(c["campaign_id"])]
    finally:
        store.close()


def test_a_later_start_numbers_on_from_the_record(tmp_path):
    from flux_loop.loop import _number_on
    from flux_loop.types import LoopState

    db = str(tmp_path / "d.db")
    task = _digits(_fake(tmp_path))
    run_loop(PromptProblem(task), request_for(task, db=db), proposer=None, log=lambda _m: None)
    top = max(int(m.group(1)) for n in _names(db) if (m := re.search(r"#(\d+)", n)))
    again = PromptProblem(task)
    request = request_for(task, db=db)
    state = LoopState(request=request, say=lambda _m: None, proposer=None, feedback=None)
    state.records = again.open_records(request, lambda _m: None)
    assert again._count == 0
    _number_on(again, state)
    assert again._count == top >= 1, "the next draft is #top+1, not #1 again"


def test_the_decision_is_the_design_it_names_among_those_a_name_was_given_to():
    old = {"base": "p#1", "key": content_key({"artifact": "old"}), "stages": {"long": {"speedup": 1.028}}, "last": "2026-10-01", "decision": False}
    new = {"base": "p#1", "key": content_key({"artifact": "new"}), "stages": {"long": {"speedup": 1.031}}, "last": "2026-10-04", "decision": False}
    _decided([old, new], {"name": "p#1", "key": old["key"], "metrics": {}})
    assert old["decision"] and not new["decision"], "by its key"
    old["decision"] = False
    _decided([old, new], {"name": "p#1", "key": None, "metrics": {"speedup": 1.028}})
    assert old["decision"] and not new["decision"], "a record before D840: by the conclusion's numbers"
    old["decision"] = False
    _decided([old, new], "p#1")
    assert new["decision"] and not old["decision"], "else the latest"
