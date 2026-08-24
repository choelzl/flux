"""D528 (review step 10): a SHORTLIST per part -- every admitted design of the part on record,
best first by the objectives on the stage the reload compared on -- on the part's state, on
the standing, and where the contender step draws from when the ledger holds no contender."""

from __future__ import annotations

import sys

import pytest

from flux_loop import Candidate, Kind, LoopRequest, LoopState
from flux_loop.ladder import contender
from flux_loop.records import _reload


@pytest.mark.skipif(sys.platform != "linux", reason="the sandbox uses resource limits")
def test_the_record_gives_a_part_its_shortlist_and_the_contender_falls_back_to_it(tmp_path):
    from nlu_fixtures import nlu_problem
    from test_nlu_vectorize import SCALAR_EXP

    db = str(tmp_path / "s.db")
    prob = nlu_problem(ops=("exp",), ulp_budget=1, clock_period_ps=1250.0, seed=1, test_rounds=0)
    st0 = LoopState(request=LoopRequest(), say=lambda _m: None, proposer=None, feedback=None, workdir=str(tmp_path))
    v = prob.prototype_check(SCALAR_EXP, "exp", st0)
    proto_a = v.payload["prototype"]
    proto_b = proto_a.replace("return 15360", "return 15360 + 0")           # a different text of exp
    proto_c = proto_a.replace("return 15360", "return 15360 + 0 + 0")       # and a third
    rec = prob.open_records(LoopRequest(db=db), lambda _m: None)      # the identity the document problem uses
    stage = prob.stages()[1] if len(prob.stages()) > 1 else prob.stages()[0]  # the parts' stage (confirm)
    designs = []
    for proto, k, fmax in ((proto_a, 8, 700.0), (proto_b, 12, 880.0), (proto_c, 16, 760.0)):
        rec.trial({"name": "prototype:exp", "artifact": proto, "knobs": {}, "meta": {"kind": "prototype"},
                   "subgoal": "exp", "score": 0.0, "why": ""}, "exp:prototype", stage="prototype", strategy="loop",
                  metrics={"score": 0.0}, error=None, analytic=True, evaluator="prototype@python")
        cand = prob.transpile(proto, "exp", st0, pipeline=k)
        doc = cand.to_record(); doc["subgoal"] = "exp"
        rec.trial(doc, "exp:admit", stage="admit", strategy="loop", metrics={"score": 0.0}, error=None)
        alone = cand.to_record(); alone["subgoal"] = None
        rec.trial(alone, f"exp:{stage}", stage=stage, strategy="loop", metrics={"fmax_mhz": fmax, "area_um2": 100.0 + k}, error=None)
        designs.append(cand)
    rec.close("paused")
    # the reload: the best MEASURED design stands (D504), and the shortlist is the record's
    prob2 = nlu_problem(ops=("exp",), ulp_budget=1, clock_period_ps=1250.0, seed=1, test_rounds=0)
    req = LoopRequest(db=db)
    st = LoopState(request=req, say=lambda _m: None, proposer=None, feedback=None, workdir=str(tmp_path))
    st.records = prob2.open_records(req, lambda _m: None)
    assert st.records.resumed and st.records.campaign_id == rec.campaign_id
    _reload(prob2, st)
    assert st.admitted["exp"].name == "transpiled_exp_p12"
    short = st.part("exp").shortlist
    assert [e["name"] for e in short] == ["transpiled_exp_p12", "transpiled_exp_p16", "transpiled_exp_p8"]
    assert short[0]["standing"] and not short[1]["standing"] and short[0]["metrics"]["fmax_mhz"] == 880.0
    assert short[1]["pipeline"] == 16 and short[1]["prototype_sha"] == designs[2].meta["prototype_sha"]
    # the standing says what else is on record
    line = prob2.standing(st)["parts"]["exp"]
    assert "also on record: transpiled_exp_p16 760 MHz/116 um2; transpiled_exp_p8 700 MHz/108 um2" in line
    # no contender on the ledger: the shortlist's best other design is the contender, with its
    # verified prototype from the record; one measured slower is skipped
    c = contender(st, "exp", "fmax_mhz")
    assert c is not None and c["from"] == "shortlist" and c["name"] == "transpiled_exp_p16" and c["value"] == 760.0
    assert c["artifact"] == proto_c and c["digest"]
    st.ledger.note(Kind.NOT_FASTER, "exp", c["digest"])
    c2 = contender(st, "exp", "fmax_mhz")
    assert c2 is not None and c2["name"] == "transpiled_exp_p8" and c2["artifact"] == proto_a
    st.records.close("paused")
