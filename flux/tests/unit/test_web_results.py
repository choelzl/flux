"""A loop's results (D690): the designs measured successfully, each accepted or failed by the
loop's limits (the stages' cutoffs and the objectives' limits), with every limit it misses.
A draft the gate refused is not a result."""

from __future__ import annotations

from flux_records import Records
from flux_web.results import designs

STAGES = [{"name": "screen", "cutoff": {"metric": "fmax_mhz", "at": 900}}, {"name": "confirm"}]


def test_baseline_measurements_are_identified_for_graphs(tmp_path):
    db = str(tmp_path / "r.db")
    rec = Records(db, objective={"study": "t"}, name="t")
    for name, baseline in (("initial", True), ("improved", False)):
        rec.trial({"name": name, "artifact": name, "meta": {"baseline": baseline}}, f"{name}@screen",
                  stage="screen", strategy="loop", metrics={"area_um2": 30.0}, evaluator="screen")
    rec.close("paused")
    rows = {d["name"]: d for d in designs(db, [{"name": "screen"}])["designs"]}
    assert rows["initial"]["baseline"] is True
    assert rows["improved"]["baseline"] is False


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
    assert got["counts"] == {"accepted": 1, "pending": 0, "failed": 2}
    fast, slow, tiny = by["fast"], by["slow"], by["tiny"]
    assert fast["verdict"] == "accepted" and fast["decision"] and got["designs"][0]["name"] == "fast"
    assert fast["shown"] == "confirm" and fast["numbers"]["fmax_mhz"] == 1200.0 and fast["meets"]["fmax_mhz"] is True
    assert slow["verdict"] == "failed" and slow["why"] == ["fmax_mhz 800 is below the limit 1000 (confirm)"]
    assert slow["meets"]["fmax_mhz"] is False
    assert tiny["verdict"] == "failed" and tiny["shown"] == "screen"
    assert tiny["why"] == ["fmax_mhz 700 is below 900 (the screen cutoff)", "fmax_mhz not measured (confirm)"], tiny["why"]
    assert (fast["eligible"], fast["pending"], fast["reasons"]) == (True, False, [])
    assert (tiny["eligible"], tiny["pending"]) == (False, False), "cut at the screen: it never reaches the stage that judges"
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


def test_past_a_page_the_counts_are_of_every_design(tmp_path):
    """D901 (B2): with 1,200 accepted designs the Loops list said 1,000 designs, 1,200 accepted and 1,000 this
    start; the total and the designs since a start are counted before the page is cut."""
    import time

    db = str(tmp_path / "many.db")
    rec = Records(db, objective={"study": "t"}, name="t")
    rec.phase("search")
    for i in range(1200):
        rec.trial({"name": f"d{i}", "artifact": f"design {i}"}, f"t:d{i}@screen", stage="screen", strategy="loop",
                  metrics={"area_um2": float(i)}, evaluator="screen")
    rec.close("paused")
    got = designs(db, [{"name": "screen"}], since=0)
    assert len(got["designs"]) == 1000 and got["total"] == 1200 and got["counts"]["accepted"] == 1200
    assert got["this_start"] == 1200 and "_firsts" not in got
    assert len(designs(db, [{"name": "screen"}], limit=None)["designs"]) == 1200, "raw views include every design"
    assert designs(db, [{"name": "screen"}], since=time.time() + 60)["this_start"] == 0, "none since a later start"


def test_one_view_per_record_however_often_the_decision_changes(tmp_path, monkeypatch):
    """D901 (B3): the kept views were keyed by the decision too, so every new winner kept another full
    snapshot (20 decisions, 20 x 1,000 designs). The decision checks the view; a changed one replaces it."""
    import flux_web.results as res

    monkeypatch.setattr(res, "_KEPT", res.OrderedDict())
    db = _record(tmp_path)
    for name in ("fast", "slow", "tiny", "fast", "slow"):
        assert any(d["name"] == name for d in designs(db, STAGES, decision=name)["designs"])
    assert len(res._KEPT) == 1, list(res._KEPT)
    first = designs(db, STAGES, decision="slow")
    assert designs(db, STAGES, decision="slow") is first, "the same decision and record: kept"
    assert designs(db, STAGES, decision="fast") is not first, "another decision: read again, in its place"
    monkeypatch.setattr(res, "_KEEP_VIEWS", 2)
    other = []
    for i in range(4):                                     # more records than the bound: the oldest go
        sub = tmp_path / f"r{i}"
        sub.mkdir()
        other.append(_record(sub))
        designs(other[-1], STAGES)
    assert len(res._KEPT) == 2 and [k[0] for k in res._KEPT] == other[-2:]


def test_no_feasible_design_shows_no_decision_and_the_closest(tmp_path):
    """D900: a pass with no design meeting every requirement concludes with no decision and names its
    closest; a conclusion from before that named a design missing a limit marks no decision either --
    its design is the closest. An eligible decision is marked as before."""
    db = _record(tmp_path)
    rec = Records(db, objective={"study": "t"}, name="t")
    rec.conclude({"decision": None, "no_decision": "no feasible design yet", "closest": "slow",
                  "unmet": ["fmax_mhz 800 is below the limit 1000 (confirm)"]})
    rec.close("paused")
    from flux_web.results import decision_doc

    doc = decision_doc(db)
    assert doc["name"] is None and doc["closest"]["name"] == "slow"
    got = designs(db, STAGES, doc)
    assert got["feasible"] is False and not any(d["decision"] for d in got["designs"])
    assert got["closest"]["name"] == "slow" and got["closest"]["reasons"] == ["fmax_mhz 800 is below the limit 1000 (confirm)"]
    assert got["designs"][0]["name"] == "slow" and got["designs"][0]["closest"]
    old = designs(db, STAGES, decision="slow")                 # a pass before D900 decided on it
    assert old["feasible"] is False and old["closest"]["name"] == "slow"
    good = designs(db, STAGES, decision="fast")
    assert good["feasible"] is True and good["closest"] is None


def test_a_conclusion_with_no_decision_stands_over_an_older_answer(tmp_path):
    import os
    import time

    from flux_web.results import decision_of

    db = _record(tmp_path)
    ans = tmp_path / "answer.json"
    ans.write_text('{"decision": {"name": "fast"}}')
    os.utime(ans, (time.time() - 3600, time.time() - 3600))
    rec = Records(db, objective={"study": "t"}, name="t")
    rec.conclude({"decision": None, "closest": "slow"})
    rec.close("paused")
    assert decision_of(db, ans) is None, "the stale answer's decision is not shown"
