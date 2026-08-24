"""D510: the record, authoritative -- every row says what made it, and a reload trusts a row
made by today's transpiler and judge instead of spelling and judging it again.

The problem here is a one-part `Problem` whose artifact is a JSON int; `versions()` is a
dict the test moves to stand for a transpiler or judge change.
"""

from __future__ import annotations

import os
import re
import time

from flux_llm import Reply, ScriptedProposer
from flux_loop import Candidate, LoopRequest, LoopState, Problem, Verdict, _reload, run_loop
from flux_loop.records import _record_trial, prototype_digest
from flux_records import Records

FP = {"transpiler": "t1", "judge": "j1"}


class Num(Problem):
    """One part; the design is an int; the gate wants 7; a "transpiled" design is re-spelled
    from a prototype by `transpile`, which counts its calls."""

    name = "num"
    judged = 0
    spelled = 0
    fp: dict[str, str] = dict(FP)

    def subgoals(self):
        return ["n"]

    def versions(self):
        return dict(self.fp)

    def design_prompt(self, subgoal, method, state, human, prior, prior_why):
        return "design n", None

    def parse_design(self, reply, subgoal):
        return Candidate("n-v", reply.strip(), subgoal=subgoal,
                         meta={"transpiled": True, "prototype_sha": prototype_digest(reply.strip())}), ""

    def compose(self, admitted, state):
        return admitted.get("n")

    def build(self, cand, subgoal, state):
        return int(cand.artifact)

    def judge(self, built, cand, subgoal, state):
        Num.judged += 1
        return Verdict(built == 7, float(abs(built - 7)), "" if built == 7 else "not 7")

    def transpile(self, prototype, subgoal, state, **kw):
        Num.spelled += 1
        return Candidate("n-v", str(int(prototype)), subgoal=subgoal,
                         meta={"transpiled": True, "prototype_sha": prototype_digest(prototype)})

    def stages(self):
        return ["screen"]

    def measure(self, cand, stage, state):
        return {"cost": float(int(cand.artifact))}


def _state(db, rec, workdir, fp=None):
    p = Num()
    if fp is not None:
        p.fp = dict(fp)
    st = LoopState(request=LoopRequest(db=db), say=lambda _m: None, proposer=None, feedback=None,
                   workdir=workdir, records=rec)
    st.versions = dict(p.versions())
    return p, st


def _rows(db, stage=None, objective=None):
    rec = Records(db, objective=objective or {"n": 1})
    out = [t for t in rec.store.trials(rec.campaign_id) if stage is None or t.stage == stage]
    rec.close("paused")
    return out


def test_every_row_says_what_made_it(tmp_path, monkeypatch):
    monkeypatch.setenv("FLUX_TRACE_ROOT", str(tmp_path / "traces"))
    db = str(tmp_path / "p.db")
    out = run_loop(Num(), LoopRequest(db=db, steps=2, prototype=False, critique_rounds=0),
                   proposer=ScriptedProposer(["7"]), log=lambda _m: None)
    assert out.decision is not None
    # the trace directory is named by the campaign and the pass, under the root
    objective = Num().objective(LoopRequest(db=db))
    rec = Records(db, objective=objective)
    cid = rec.campaign_id
    rec.close("paused")
    assert re.fullmatch(re.escape(os.path.join(str(tmp_path / "traces"), cid[:12])) + r"/\d{8}T\d{6}(-\d+)?",
                        out.provenance["workdir"]), out.provenance["workdir"]
    # the admitted row: provenance with the revision and the toolchain, the judge's seconds,
    # and the versions of what made it
    admit = [t for t in _rows(db, "admit", objective)][-1]
    meta = admit.candidate["meta"]
    assert set(meta["provenance"]) >= {"git", "toolchain", "prompt"} and len(meta["provenance"]["prompt"]) == 16 and meta["versions"] == FP
    assert admit.wall_clock_s >= 0.0                        # an instant judge rounds to 0.0; the column is filled
    # a measured row too, with the measurement's seconds
    screen = [t for t in _rows(db, "screen", objective)]
    assert screen and "toolchain" in screen[-1].candidate["meta"]["provenance"]


def test_a_prototype_row_carries_the_turns_cost_and_its_trace_directory(tmp_path):
    from flux_loop.prototype import _record_prototype

    db = str(tmp_path / "q.db")
    rec = Records(db, objective={"n": 1})
    _p, st = _state(db, rec, str(tmp_path / "work"))
    st.part("n").proto_passes = 2
    reply = Reply("{}", usage={"input_tokens": 120, "output_tokens": 30}, notes={"model": "qwen-x"})
    _record_prototype(st, "n", "def design(x): return x", Verdict(False, 3.0, "3 over"), ok=False,
                      reply=reply, seconds=1.5)
    rec.close("paused")
    row = _rows(db, "prototype")[-1]
    prov = row.candidate["meta"]["provenance"]
    assert prov["tokens_in"] == 120 and prov["tokens_out"] == 30 and prov["model"] == "qwen-x"
    assert prov["trace"] == os.path.join(str(tmp_path / "work"), "prototypes", "n", "pass2")
    assert row.wall_clock_s == 1.5 and prov["seconds"] == 1.5


def test_a_reload_trusts_a_row_made_by_todays_transpiler_and_judge(tmp_path):
    db = str(tmp_path / "r.db")
    rec = Records(db, objective={"n": 1})
    p, st = _state(db, rec, str(tmp_path))
    cand = Candidate("n-v", "7", subgoal="n", meta={"transpiled": True, "prototype_sha": prototype_digest("7")})
    rec.trial({"name": "prototype:n", "artifact": "7", "knobs": {}, "meta": {"kind": "prototype"}, "subgoal": "n",
               "score": 0.0, "why": ""}, "n:prototype", stage="prototype", strategy="loop", metrics={"score": 0.0},
              error=None, analytic=True, evaluator="prototype@python")
    _record_trial(st, cand, "n", Verdict(True, 0.0, ""), admitted=True)      # carries today's versions
    rec.close("paused")
    Num.judged = Num.spelled = 0
    said: list[str] = []
    rec2 = Records(db, objective={"n": 1})
    p2, st2 = _state(db, rec2, str(tmp_path))
    st2.say = said.append
    _reload(p2, st2)
    assert st2.admitted["n"].artifact == "7" and st2.prototypes["n"] == "7"
    assert Num.judged == 0 and Num.spelled == 0, "trusted as it stands: no judge, no re-spelling"
    assert any("kept frozen (its row was made by today's transpiler and judge)" in m for m in said)
    rec2.close("paused")
    # the judge moved: re-judged ONCE, recorded with today's versions, trusted after that
    rec3 = Records(db, objective={"n": 1})
    p3, st3 = _state(db, rec3, str(tmp_path), fp={"transpiler": "t1", "judge": "j2"})
    _reload(p3, st3)
    assert Num.judged == 1 and Num.spelled == 0 and st3.admitted["n"].artifact == "7"
    rec3.close("paused")
    assert _rows(db, "admit")[-1].candidate["meta"]["versions"] == {"transpiler": "t1", "judge": "j2"}
    rec4 = Records(db, objective={"n": 1})
    p4, st4 = _state(db, rec4, str(tmp_path), fp={"transpiler": "t1", "judge": "j2"})
    _reload(p4, st4)
    assert Num.judged == 1, "the re-judged row is trusted now"
    rec4.close("paused")
    # the transpiler moved: re-spelled from its prototype ONCE (by digest), judged, recorded
    rec5 = Records(db, objective={"n": 1})
    p5, st5 = _state(db, rec5, str(tmp_path), fp={"transpiler": "t2", "judge": "j2"})
    _reload(p5, st5)
    assert Num.spelled == 1 and Num.judged == 2
    rec5.close("paused")
    rec6 = Records(db, objective={"n": 1})
    p6, st6 = _state(db, rec6, str(tmp_path), fp={"transpiler": "t2", "judge": "j2"})
    _reload(p6, st6)
    assert Num.spelled == 1 and Num.judged == 2, "once"
    rec6.close("paused")


def test_a_row_without_its_prototypes_digest_is_not_matched_by_guessing(tmp_path):
    """Before D510 a design admitted before digests was matched to its prototype by
    re-transpiling every verified one; with two on record the guess could land on the
    wrong design. Now: one verified prototype stands in, several mean none."""
    db = str(tmp_path / "s.db")
    rec = Records(db, objective={"n": 1})
    p, st = _state(db, rec, str(tmp_path))
    for proto in ("7", "8"):
        rec.trial({"name": "prototype:n", "artifact": proto, "knobs": {}, "meta": {"kind": "prototype"}, "subgoal": "n",
                   "score": 0.0, "why": ""}, "n:prototype", stage="prototype", strategy="loop", metrics={"score": 0.0},
                  error=None, analytic=True, evaluator="prototype@python")
    old = Candidate("n-v", "7", subgoal="n", meta={"transpiled": True})          # no prototype_sha
    doc = old.to_record(); doc["subgoal"] = "n"
    rec.trial(doc, "n:n-v", stage="admit", strategy="loop", metrics={"score": 0.0}, error=None)
    rec.close("paused")
    Num.judged = Num.spelled = 0
    rec2 = Records(db, objective={"n": 1})
    p2, st2 = _state(db, rec2, str(tmp_path))
    _reload(p2, st2)
    assert st2.admitted["n"].artifact == "7" and "n" not in st2.prototypes
    assert Num.spelled == 0 and Num.judged == 1, "re-judged (no versions on the row), never re-spelled by a guess"
    rec2.close("paused")


def test_flux_gc_keeps_what_a_record_names(tmp_path, monkeypatch):
    import argparse

    from flux_cli.commands import cmd_gc

    root = tmp_path / "traces"
    kept, doomed, young = root / "abc123" / "20260101T000000", root / "abc123" / "20260102T000000", root / "abc123" / "20260103T000000"
    for d in (kept, doomed, young):
        d.mkdir(parents=True)
        (d / "x.txt").write_text("trace")
    old = time.time() - 30 * 86400
    os.utime(kept, (old, old)); os.utime(doomed, (old, old))
    db = str(tmp_path / "g.db")
    rec = Records(db, objective={"n": 1})
    _p, st = _state(db, rec, str(kept))
    from flux_loop.prototype import _record_prototype

    _record_prototype(st, "n", "x", Verdict(False, 1.0, "1"), ok=False)          # names <kept>/prototypes/n/pass1
    rec.close("paused")
    args = argparse.Namespace(db=[db], root=str(root), keep_days=7.0, legacy=False, apply=False)
    assert cmd_gc(args) == 0 and kept.exists() and doomed.exists() and young.exists()
    args.apply = True
    assert cmd_gc(args) == 0
    assert kept.exists() and young.exists() and not doomed.exists()
