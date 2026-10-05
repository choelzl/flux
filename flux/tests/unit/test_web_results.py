"""A loop's results (D690): the designs measured successfully, each accepted or failed by the
loop's limits (the stages' cutoffs and the objectives' limits), with every limit it misses.
A draft the gate refused is not a result."""

from __future__ import annotations

from flux_records import Records
from flux_web.results import designs

STAGES = [{"name": "screen", "cutoff": {"metric": "fmax_mhz", "at": 900}}, {"name": "confirm"}]


def _record(tmp_path):
    db = str(tmp_path / "r.db")
    rec = Records(db, objective={"study": "t"}, name="t")
    rec.phase("search")
    rec.remember("objectives", {"objectives": [{"metric": "fmax_mhz", "direction": "maximize", "goal": 1000, "stage": "confirm"},
                                                       {"metric": "area_um2", "direction": "minimize"}]})
    trial = lambda name, stage, metrics, error=None: rec.trial({"name": name, "artifact": f"module {name};"}, f"t:{name}@{stage}",
                                                              stage=stage, strategy="loop", metrics=metrics, error=error, evaluator=stage)
    trial("fast", "screen", {"fmax_mhz": 1500.0, "area_um2": 30.0})
    trial("fast", "confirm", {"fmax_mhz": 1200.0, "area_um2": 31.0})
    trial("slow", "screen", {"fmax_mhz": 950.0, "area_um2": 12.0})
    trial("slow", "confirm", {"fmax_mhz": 800.0, "area_um2": 12.5})          # under the objective's 1000
    trial("tiny", "screen", {"fmax_mhz": 700.0, "area_um2": 5.0})            # under the screen cutoff: not measured further
    trial("broken", "gate", {"score": 3.0}, error="FAIL line 4")              # the gate refused it: not a result
    rec.close("paused")
    return db


def test_measured_designs_accepted_or_failed_by_the_limits(tmp_path):
    got = designs(_record(tmp_path), STAGES, decision="fast")
    by = {d["name"]: d for d in got["designs"]}
    assert set(by) == {"fast", "slow", "tiny"}, "a draft the gate refused is not a result"
    assert got["counts"] == {"accepted": 1, "failed": 2}
    fast, slow, tiny = by["fast"], by["slow"], by["tiny"]
    assert fast["verdict"] == "accepted" and fast["decision"] and got["designs"][0]["name"] == "fast"
    assert fast["shown"] == "confirm" and fast["numbers"]["fmax_mhz"] == 1200.0 and fast["meets"]["fmax_mhz"] is True
    assert slow["verdict"] == "failed" and slow["why"] == ["fmax_mhz 800 is below the limit 1000 (confirm)"]
    assert slow["meets"]["fmax_mhz"] is False
    assert tiny["verdict"] == "failed" and tiny["shown"] == "screen"
    assert tiny["why"] == ["fmax_mhz 700 is below 900 (the screen cutoff)"], tiny["why"]
    assert got["metrics"][:2] == ["fmax_mhz", "area_um2"] and got["limits"][0]["goal"] == 1000


def test_a_within_cutoff_is_judged_against_the_best_measured(tmp_path):
    stages = [{"name": "screen", "cutoff": {"metric": "fmax_mhz", "within": 0.9}}, {"name": "confirm"}]
    got = designs(_record(tmp_path), stages)
    by = {d["name"]: d for d in got["designs"]}
    assert not any("screen cutoff" in w for w in by["fast"]["why"])          # 1500 is the best
    assert any("within 90% of the best 1500" in w for w in by["slow"]["why"])


def test_the_designs_are_kept_until_the_record_changes(tmp_path, monkeypatch):
    """D774: a record is read once per change -- a list's look at a running loop not even that often."""
    import flux_web.results as res

    db = _record(tmp_path)
    reads = []
    real = res._designs
    monkeypatch.setattr(res, "_designs", lambda *a: reads.append(1) or real(*a))
    first = designs(db, STAGES, decision="fast")
    assert designs(db, STAGES, decision="fast") is first and len(reads) == 1, "unchanged: kept"
    rec = Records(db, objective={"study": "t"}, name="t")
    rec.trial({"name": "new", "artifact": "module new;"}, "t:new@screen", stage="screen", strategy="loop",
              metrics={"fmax_mhz": 990.0, "area_um2": 9.0}, evaluator="screen")
    rec.close("paused")
    assert designs(db, STAGES, decision="fast", stale_s=30) is first, "changed, but a list may wait 30 s"
    got = designs(db, STAGES, decision="fast")
    assert len(reads) == 2 and any(d["name"] == "new" for d in got["designs"]), "changed: read again"


def test_the_decision_is_the_records_latest_pass_and_the_best_are_ranked_by_the_loops_rule(tmp_path):
    """D809: a loop running for days has its decision from its first pass's end -- the record's
    `conclusion`, not only the answer a run writes when it ends -- and the best are ranked by the
    objectives' own rule over the deepest stage, whether or not there is a decision."""
    import os
    import time

    from flux_web.results import decision_of

    db = _record(tmp_path)
    assert decision_of(db) is None, "no pass ended yet"
    got = designs(db, STAGES)
    ranks = {d["name"]: d["rank"] for d in got["designs"]}
    # on the deepest stage (confirm): fast meets the 1000 limit, slow does not; tiny never reached it
    assert ranks == {"fast": 1, "slow": 2, "tiny": None}, ranks
    rec = Records(db, objective={"study": "t"}, name="t")
    rec.conclude({"decision": "slow", "decided_by": "a test"})
    rec.close("paused")
    assert decision_of(db) == "slow"
    from flux_web.results import decision_said

    assert decision_said(db) == "a test", "D815: why, as the loop said it"
    assert decision_said(str(tmp_path / "none.db")) == ""
    ans = tmp_path / "answer.json"
    ans.write_text('{"decision": {"name": "fast"}}')
    os.utime(ans, (time.time() - 3600, time.time() - 3600))
    assert decision_of(db, ans) == "slow", "an older answer gives way to the record's newer pass"
    os.utime(ans, None)
    assert decision_of(db, ans) == "fast", "a run's answer newer than the record's last pass"
    assert decision_of(str(tmp_path / "none.db"), tmp_path / "missing.json") is None
