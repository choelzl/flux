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
