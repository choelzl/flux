"""The campaign record (D446): `flux_records.Records` writes the trials and a resumed campaign
reads them back.

Pins: the record is created where `--db` says, a trial carries its phase and stage, a refusal its
reason, every knob (the stack too) is part of the identity, an unwritable record does not stop the
run, the same identity resumes, a resumed campaign reads its designs back (D367), and the loop
writes each dse phase's measurements under that phase's name.
"""

from __future__ import annotations

import json

from flux_records import Records

OBJECTIVE = {"study": "toy", "stage": 2}
SHIPPED = {"table": 256, "ways": 4, "stack": "a"}


def Recorder(db, objective, log=lambda _m: None):
    """The record, with `trial(knobs, ...)` keyed as the loop keys a knob point."""
    rec = Records(db, objective=objective, log=log)
    generic = rec.trial

    def trial(knobs, *, stage, strategy, metrics, error=None, wall_s=0.0):
        generic(dict(knobs), json.dumps(knobs, sort_keys=True), stage=stage, strategy=strategy,
                metrics=metrics, error=error, wall_s=wall_s, analytic=("bytes",), evaluator=f"toy@{stage}")
    rec.trial = trial
    return rec


def test_it_creates_the_database_the_flag_names(tmp_path):
    db = tmp_path / "run.db"
    rec = Recorder(str(db), OBJECTIVE, lambda _m: None)
    rec.close("completed")
    assert db.is_file(), "--db named a file that was never created"
    assert db.stat().st_size > 0


def test_trials_are_recorded_with_their_phase_and_stage(tmp_path):
    """A trial records its stage, i.e. the number's fidelity (D351)."""
    from flux_store import CampaignStore

    db = tmp_path / "run.db"
    rec = Recorder(str(db), OBJECTIVE, lambda _m: None)
    rec.phase("stage1")
    rec.trial(SHIPPED, stage="screen", strategy="incumbent",
              metrics={"speedup": 1.0607, "bytes": 8192.0},
              error=None, wall_s=7.0)
    rec.phase("confirm")
    rec.trial(SHIPPED, stage="decide", strategy="confirmed",
              metrics={"speedup": 1.0440, "bytes": 8192.0},
              error=None, wall_s=360.0)
    rec.close("completed")

    store = CampaignStore(str(db))
    trials = store.trials(store.list_campaigns()[0]["campaign_id"])
    store.close()
    assert len(trials) == 2
    assert {t.phase for t in trials} == {"stage1", "confirm"}
    assert {t.stage for t in trials} == {"screen", "decide"}


def test_a_refusal_is_recorded_with_its_reason(tmp_path):
    """A refusal is recorded with its reason."""
    from flux_store import CampaignStore

    db = tmp_path / "run.db"
    rec = Recorder(str(db), OBJECTIVE, lambda _m: None)
    rec.phase("stage2")
    rec.trial(SHIPPED, stage="screen", strategy="shrink", metrics=None,
              error="shrink: below the floor, speedup 1.0000 < 1.0558", wall_s=7.0)
    rec.close("completed")

    store = CampaignStore(str(db))
    trials = store.trials(store.list_campaigns()[0]["campaign_id"])
    store.close()
    assert len(trials) == 1
    assert trials[0].status == "refused"
    assert "below the floor" in (trials[0].error or "")


def test_the_stack_is_part_of_the_recorded_identity(tmp_path):
    """Stack `a` and `a,b` on identical knobs are two designs, not one row."""
    from flux_store import CampaignStore

    db = tmp_path / "run.db"
    rec = Recorder(str(db), OBJECTIVE, lambda _m: None)
    rec.phase("compose")
    for stack in ("a", "a,b"):
        rec.trial({**SHIPPED, "stack": stack}, stage="screen", strategy="compose",
                  metrics={"speedup": 1.06}, error=None, wall_s=7.0)
    rec.close("completed")

    store = CampaignStore(str(db))
    trials = store.trials(store.list_campaigns()[0]["campaign_id"])
    store.close()
    assert len({t.candidate_key for t in trials}) == 2, "the stack must distinguish them"
    assert any("a,b" in t.candidate_key for t in trials)


def test_an_unwritable_database_costs_the_record_not_the_run(tmp_path):
    """An unwritable record does not stop the study."""
    unwritable = tmp_path / "nope" / "deeper" / "run.db"      # parent does not exist
    said = []
    rec = Recorder(str(unwritable), OBJECTIVE, said.append)
    rec.phase("stage1")
    rec.trial(SHIPPED, stage="screen", strategy="incumbent",
              metrics={"speedup": 1.0}, error=None, wall_s=1.0)
    rec.close("completed")
    assert rec.store is None
    assert any("no campaign record" in m for m in said), "the failure must be reported, not hidden"


def test_the_same_objective_resumes_rather_than_forking(tmp_path):
    """Campaign id is the objective's hash (D220): re-running continues the same campaign."""
    from flux_store import CampaignStore

    db = tmp_path / "run.db"
    first = Recorder(str(db), OBJECTIVE, lambda _m: None)
    first_id = first.campaign_id
    first.close("completed")
    second = Recorder(str(db), OBJECTIVE, lambda _m: None)
    assert second.campaign_id == first_id
    second.close("completed")

    store = CampaignStore(str(db))
    assert len(store.list_campaigns()) == 1, "the same objective forked a sibling campaign"
    store.close()


def test_a_resumed_campaign_reads_its_own_record_back(tmp_path):
    """Screened designs, best first; a confirmed number and a refusal are not among them (D367)."""
    first = Recorder(str(tmp_path / "x.db"), OBJECTIVE)
    assert not first.resumed
    good = {**SHIPPED, "table": 1024}
    first.phase("climb")
    first.trial(SHIPPED, stage="screen", strategy="seed", metrics={"speedup": 1.0485, "bytes": 8192.0})
    first.trial(good, stage="screen", strategy="climb", metrics={"speedup": 1.0632, "bytes": 32768.0})
    first.trial(good, stage="confirm", strategy="confirmed", metrics={"speedup": 1.0570, "bytes": 32768.0})
    first.trial({**SHIPPED, "ways": 1}, stage="screen", strategy="climb", metrics=None, error="simulation failed")
    first.close("completed")

    second = Recorder(str(tmp_path / "x.db"), OBJECTIVE)
    assert second.resumed
    known = second.known(stage="screen", metric="speedup")
    assert [(k, round(v, 4)) for k, v in known] == [(good, 1.0632), (SHIPPED, 1.0485)]
    assert second.known(stage="screen", metric="speedup", want=lambda k: k["table"] == 256) == [(SHIPPED, 1.0485)]
    assert Recorder(str(tmp_path / "y.db"), OBJECTIVE).known(stage="screen", metric="speedup") == []


def test_the_loop_records_each_dse_phase_by_name(tmp_path):
    """Each measurement carries the phase that proposed it as its strategy, on the stage it ran."""
    import sqlite3

    from toy_document import run_toy, toy

    db = tmp_path / "loop.db"
    run_toy(toy(flow={"orchestrate": [{"name": "climb", "knobs": ["table"], "wave": 2, "patience": 1, "budget": 4},
                              {"name": "compose", "knobs": ["stack"], "reach": "any", "wave": 6, "patience": 1}]}),
            db=str(db))
    with sqlite3.connect(db) as con:
        rows = con.execute("SELECT strategy_kind, stage FROM trials").fetchall()
    assert {"seed", "climb", "compose"} <= {k for k, _s in rows}, rows
    assert {s for _k, s in rows} == {"screen"}
