"""D899: one stage-aware eligibility reading, shared by the decision, the results and the charts. An
external review reproduced a successful measurement of only `{cost: 1}` with `speed >= 10` required:
the loop reported speed missing, the web said **accepted** with no reason. A required number missing
at the stage that judges it is now "not measured"; one a later stage will judge is "pending"; neither
is eligible."""

from __future__ import annotations

from flux_loop.eligibility import eligibility
from flux_loop.objective import Objective, Objectives
from flux_records import Records
from flux_web.results import designs

CHAIN = ["screen", "confirm"]
SPEED = Objectives([Objective("speed", "maximize", goal=10, stage="confirm"), Objective("cost", "minimize")])


def test_met_missed_not_measured_and_pending():
    ok = eligibility(SPEED, {"screen": {"speed": 3}, "confirm": {"speed": 12, "cost": 1}}, CHAIN)
    assert (ok.eligible, ok.pending, ok.reasons) == (True, False, ())
    missed = eligibility(SPEED, {"confirm": {"speed": 8}}, CHAIN)
    assert not missed.eligible and not missed.pending and missed.reasons == ("speed 8 is below the limit 10 (confirm)",)
    absent = eligibility(SPEED, {"confirm": {"cost": 1}}, CHAIN)
    assert not absent.eligible and not absent.pending and absent.reasons == ("speed not measured (confirm)",)
    nan = eligibility(SPEED, {"confirm": {"speed": float("nan")}}, CHAIN)
    assert not nan.eligible and nan.reasons == ("speed not measured (confirm)",)
    waiting = eligibility(SPEED, {"screen": {"speed": 50}}, CHAIN)
    assert not waiting.eligible and waiting.pending and waiting.reasons == ("speed waits for the confirm stage",)
    cut = eligibility(SPEED, {"screen": {"speed": 50}}, CHAIN, stopped=True)
    assert not cut.eligible and not cut.pending and cut.reasons == ("speed not measured (confirm)",)
    gone = eligibility(Objectives([Objective("speed", "maximize", goal=10, stage="place")]), {"screen": {"speed": 50}}, CHAIN)
    assert not gone.eligible and not gone.pending, "a stage the chain does not have can never judge it"


def test_a_limit_without_a_stage_is_judged_on_the_deepest_reached_and_no_limit_is_eligible():
    free = Objectives([Objective("speed", "maximize", goal=10)])
    assert eligibility(free, {"screen": {"speed": 20}, "confirm": {"speed": 5}}, CHAIN).reasons == (
        "speed 5 is below the limit 10 (confirm)",)
    assert eligibility(Objectives([Objective("cost", "minimize")]), {"screen": {}}, CHAIN).eligible


def _record(tmp_path, rows):
    db = str(tmp_path / "r.db")
    rec = Records(db, objective={"study": "t"}, name="t")
    rec.phase("search")
    rec.remember("objectives", {"objectives": [o.to_doc() for o in SPEED]})
    for name, stage, metrics in rows:
        rec.trial({"name": name, "artifact": f"design {name}"}, f"t:{name}@{stage}", stage=stage, strategy="loop",
                  metrics=metrics, evaluator=stage)
    rec.close("paused")
    return db


def test_the_results_no_longer_accept_a_design_missing_a_required_number(tmp_path):
    """The review's case, plus a pending design and a met one: each says why, the cost stays for plotting."""
    db = _record(tmp_path, [("cheap", "confirm", {"cost": 1.0}),
                            ("early", "screen", {"speed": 40.0, "cost": 2.0}),
                            ("good", "confirm", {"speed": 11.0, "cost": 3.0})])
    stages = [{"name": "screen"}, {"name": "confirm"}]
    by = {d["name"]: d for d in designs(db, stages)["designs"]}
    cheap, early, good = by["cheap"], by["early"], by["good"]
    assert (cheap["verdict"], cheap["eligible"], cheap["pending"]) == ("failed", False, False)
    assert cheap["reasons"] == ["speed not measured (confirm)"] and cheap["why"] == cheap["reasons"]
    assert cheap["numbers"] == {"cost": 1.0}, "the valid cost is kept, for the charts"
    assert (early["verdict"], early["eligible"], early["pending"]) == ("pending", False, True)
    assert early["reasons"] == ["speed waits for the confirm stage"]
    assert (good["verdict"], good["eligible"], good["reasons"]) == ("accepted", True, [])
    counts = designs(db, stages)["counts"]
    assert counts == {"accepted": 1, "pending": 1, "failed": 1}
