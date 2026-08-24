"""The sibling lookup is the loop's (D540): another campaign of the same DOCUMENT in the same
store, found by the identity document's `study`, its design read from its rows -- no world
needs to know a study name to take a design a sibling campaign verified."""

from __future__ import annotations

from flux_loop import Kind, LoopRequest, LoopState, PromptProblem, TaskSpec, prototype_digest, request_for
from flux_loop.records import sibling_design
from flux_records import Records


def _doc(id_, part="p"):
    return TaskSpec.from_dict({"id": id_, "statement": "x", "parts": [part], "gate": {"test": ["a"]},
                               "stages": [{"name": "screen", "command": ["m"], "metrics_re": {"fmax_mhz": r"(\d+)"}}],
                               "objectives": [{"metric": "fmax_mhz", "direction": "maximize"}]})


def _sibling(db, objective, name, *, part="p", proto="def design(x): return x", rtl="module m; endmodule", fmax=700.0):
    rec = Records(db, objective=objective, name=name)
    rec.phase("search")
    rec.trial({"name": "proto", "artifact": proto, "subgoal": part, "meta": {"kind": "prototype"}},
              f"{part}@prototype", stage="prototype", strategy="model", metrics={"score": 0.0}, evaluator="check")
    rec.trial({"name": "rtl", "artifact": rtl, "subgoal": part, "meta": {"prototype_sha": prototype_digest(proto)}},
              f"{part}@admit", stage="admit", strategy="model", metrics={"ok": 1.0}, evaluator="gate")
    rec.trial({"name": "rtl", "artifact": rtl, "subgoal": part, "meta": {}},
              f"{part}@screen", stage="screen", strategy="model", metrics={"fmax_mhz": fmax}, analytic=False, evaluator="yosys")
    return rec


def _state(problem, db):
    req = request_for(problem.task, db=db)
    st = LoopState(request=req, say=lambda _m: None, proposer=None, feedback=None)
    st.records = problem.open_records(req, st.say)
    return st


def test_a_sibling_of_the_same_document_offers_its_admitted_design(tmp_path):
    db = str(tmp_path / "s.db")
    prob = PromptProblem(_doc("nlu"))
    ident = prob.objective(request_for(prob.task, db=db))
    assert ident["study"] == "nlu"
    _sibling(db, {**ident, "seed": 1}, "nlu-focused", fmax=716.0)
    _sibling(db, {"study": "other", "task": "x"}, "other-study", fmax=900.0)   # another document: ignored
    st = _state(prob, db)
    got = sibling_design(prob, "p", "def design(x): return 0", st)
    assert got is not None and got["campaign"] == "nlu-focused" and got["value"] == 716.0
    assert got["fmax_mhz"] == 716.0 and got["digest"] == prototype_digest("def design(x): return x")
    assert prob.siblings("p", "def design(x): return 0", st) == got


def test_the_own_design_and_an_imported_or_slower_one_are_not_offered_again(tmp_path):
    db = str(tmp_path / "s.db")
    prob = PromptProblem(_doc("nlu"))
    ident = prob.objective(request_for(prob.task, db=db))
    proto = "def design(x): return x"
    _sibling(db, {**ident, "seed": 1}, "nlu-focused", proto=proto)
    st = _state(prob, db)
    assert sibling_design(prob, "p", proto, st) is None, "its own text is not an import"
    st.ledger.note(Kind.IMPORTED, "p", prototype_digest(proto))
    assert sibling_design(prob, "p", "other", st) is None, "taken before"


def test_a_document_with_a_campaign_block_still_names_itself(tmp_path):
    doc = _doc("nlu").to_dict()
    doc["campaign"] = {"name": "nlu", "ops": ["p"]}
    prob = PromptProblem(TaskSpec.from_dict(doc))
    assert prob.objective(request_for(prob.task, db="x.db")) == {"study": "nlu", "ops": ["p"]}
