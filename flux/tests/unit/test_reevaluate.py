"""D988: invalidate and re-evaluate. After the measurement scripts change, one run checks and
measures every design on record again; the earlier rows stay as history and no reader counts them."""

from __future__ import annotations

import sys

from flux_store import CampaignStore

from flux_loop import LoopRequest, PromptProblem, TaskSpec, run_loop
from flux_loop.reevaluate import designs_on_record
from flux_loop.types import Candidate
from toy_document import STAGE, landscape, run_toy, toy


CLIMB = {"orchestrate": [{"name": "climb", "knobs": ["table"], "wave": 2, "patience": 1, "budget": 4}]}


def _store(db):
    store = CampaignStore(db)
    (cid,) = [r[0] for r in store._conn.execute("SELECT campaign_id FROM campaigns")]
    return store, cid


def test_reevaluation_measures_every_design_again_and_invalidates_the_rest(tmp_path):
    db = str(tmp_path / "out.db")
    run_toy(toy(flow=CLIMB), db=db)
    store, cid = _store(db)
    before = store.trials(cid, status="ok")
    designs = designs_on_record(store, cid)
    assert before and len(designs) == len({t.candidate_key for t in before})
    assert store.invalid_through(cid) == 0 and store.trials(cid, current=True) == store.trials(cid)
    store.close()

    # today's measurement: the same stage reports twice the speedup
    doubled = tmp_path / "doubled.py"
    doubled.write_text("import subprocess, sys\n"
                       f"out = subprocess.run([sys.executable, {STAGE[1]!r}, *sys.argv[1:]], capture_output=True, text=True).stdout\n"
                       "for line in out.split():\n"
                       "    k, v = line.split('=')\n"
                       "    print(f\"{k}={float(v) * (2 if k == 'speedup' else 1)}\")\n")
    doc = toy(flow=CLIMB, stages=[{"name": "screen", "command": [sys.executable, str(doubled), *STAGE[2:]],
                       "metrics": ["speedup", "bytes"]}])
    prob = PromptProblem(TaskSpec.from_dict(doc))
    req = LoopRequest(db=db, steps=40, finalists=0, prototype=False, batch=10_000, reevaluate=True)
    run_loop(prob, req, log=lambda _m: None)

    store, cid = _store(db)
    through = store.invalid_through(cid)
    assert through == max(t.seq for t in before)
    now = [t for t in store.trials(cid, status="ok", current=True) if t.stage == "screen"]
    gates = [t for t in store.trials(cid, current=True) if t.stage != "screen"]
    assert len(gates) == len(designs)                                # each checked by today's gate
    assert {t.candidate_key for t in now} == {t.candidate_key for t in before}
    assert all(t.seq > through for t in now)
    for t in now:
        knobs = Candidate.from_record(t.candidate).knobs
        assert t.result.value_of("speedup") == round(landscape(knobs)["speedup"], 6) * 2
    assert len(store.trials(cid)) == len(before) + len(now) + len(gates)# nothing deleted
    store.close()
