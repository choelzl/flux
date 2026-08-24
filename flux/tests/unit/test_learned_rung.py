"""Evaluation's AI half: a screening stage fitted on this campaign's own measured trials
(docs/decisions.md D461).

Cedric's fourth column: "Evaluation: Analytical model and real tools <AND/OR> AI Model (not
necessarily LLM) and real tools". The dangerous part of that sentence is not the model, it is
the possibility of a PREDICTED number being read as a measured one -- which this repository
refuses everywhere else (D297/D351).

What these pin: the screen does not exist until the record can support it, it inserts itself
BELOW the real stages and never becomes the last one, its numbers are tagged modelled and named
as a learned evaluator in the record, it says how much to trust itself, it cannot predict outside
what has been measured, its prediction is never served from the cache, and a document can switch
it on.
"""

from __future__ import annotations

import pytest
from flux_loop import (Candidate, LoopRequest, Problem, PromptProblem, Surrogate, TaskError,
                       TaskSpec, Verdict, make_role, rig, run_loop)


class Widths(Problem):
    """Candidates that are points: a width, and a real stage that measures its cost."""

    name = "widths"

    def __init__(self, widths, roles=None) -> None:
        self.widths = list(widths)
        self._roles = roles
        self.measured: list[int] = []

    def objective(self, request):
        return {"study": "widths"}

    def search(self, state):
        yield [Candidate(name=f"w{w}", artifact="", knobs={"width": w}) for w in self.widths]

    def build(self, cand, subgoal, state):
        return cand.artifact

    def judge(self, built, cand, subgoal, state):
        return Verdict(True, 0.0)

    def stages(self):
        return self.chained(["real"])

    def measure(self, cand, stage, state):
        predicted = self.role_measure(cand, stage, state)
        if predicted is not None:
            return predicted
        w = int(cand.knobs["width"])
        self.measured.append(w)
        return {"cost": 10.0 + 2.0 * w}

    def frontier_axes(self):
        return (lambda p: -p.metrics["cost"], lambda p: p.metrics["cost"])


def _request(db, **kw):
    kw.setdefault("steps", 1)
    return LoopRequest(db=db, prototype=False, compute=False, critique_rounds=0, **kw)


def _train(db, widths=(1, 2, 3, 4, 5)):
    """A plain run: real tools only, which is what fills the record."""
    problem = Widths(widths)
    out = run_loop(problem, _request(db), log=lambda _m: None)
    assert out.decision is not None
    return problem


def test_the_screen_does_not_exist_until_the_record_can_support_it(tmp_path):
    said: list[str] = []
    problem = Widths([1, 2], rig(evaluator={"learned": {"metric": "cost"}}))
    out = run_loop(problem, _request(str(tmp_path / "w.db")), log=said.append)
    assert problem.stages() == ["real"], "the chain is the problem's own"
    assert any("not in this chain" in m and "4 needed" in m for m in said), said
    assert out.decision is not None and out.decision.stage == "real"


def test_once_the_record_holds_enough_the_screen_joins_the_chain_below_the_real_stage(tmp_path):
    db = str(tmp_path / "w.db")
    _train(db)
    problem = Widths([1, 2, 3, 4, 5], rig(evaluator={"learned": {"metric": "cost"}}))
    out = run_loop(problem, _request(db), log=lambda _m: None)
    assert problem.stages() == ["learned", "real"], "the screen is FIRST, the answer is last"
    predicted = [s for s in out.scored if s.stage == "learned"]
    assert len(predicted) == 5 and all("cost" in s.metrics for s in predicted)
    assert all(s.payload.get("predicted") for s in predicted)
    assert all(list(s.metrics) == ["cost"] for s in predicted), (
        "one predicted metric, and no bookkeeping numbers pretending to be measurements")
    assert out.decision is not None and out.decision.stage == "real", (
        "the decision is still measured, never predicted")


def test_it_says_how_much_to_trust_it(tmp_path):
    db = str(tmp_path / "w.db")
    _train(db)
    out = run_loop(Widths([2, 4], rig(evaluator={"learned": {"metric": "cost"}})),
                   _request(db), log=lambda _m: None)
    fitted = [l for l in out.lessons if "learned screen was fitted" in l]
    assert len(fitted) == 1, out.lessons
    assert "5 measured cost point(s)" in fitted[0] and "leave-one-out mean error" in fitted[0]
    assert "ORDERS candidates; it never answers" in fitted[0]


def test_a_prediction_cannot_leave_the_range_that_was_measured(tmp_path):
    """The reason this is a kNN and not a fitted line: a screen built from five points must
    not invent a sixth that beats everything ever built."""
    db = str(tmp_path / "w.db")
    _train(db, widths=(1, 2, 3))          # costs 12, 14, 16
    problem = Widths([50, 100], rig(evaluator={"learned": {"metric": "cost", "min_points": 3}}))
    out = run_loop(problem, _request(db), log=lambda _m: None)
    predicted = [s.metrics["cost"] for s in out.scored if s.stage == "learned"]
    assert predicted, "the screen ran"
    assert all(12.0 <= v <= 16.0 for v in predicted), predicted


def test_a_design_already_measured_predicts_its_own_measured_number(tmp_path):
    db = str(tmp_path / "w.db")
    _train(db)
    out = run_loop(Widths([3], rig(evaluator={"learned": {"metric": "cost"}})),
                   _request(db), log=lambda _m: None)
    got = [s for s in out.scored if s.stage == "learned"]
    assert got and got[0].metrics["cost"] == 16.0, got[0].metrics


def test_the_record_says_the_number_was_modelled_and_who_modelled_it(tmp_path):
    from flux_store import CampaignStore

    db = str(tmp_path / "w.db")
    _train(db)
    run_loop(Widths([2, 4], rig(evaluator={"learned": {"metric": "cost"}})),
             _request(db), log=lambda _m: None)
    store = CampaignStore(db)
    cid = store.list_campaigns()[0]["campaign_id"]
    rows = [t for t in store.trials(cid) if t.stage == "learned" and t.result is not None]
    assert rows, "the predictions are on record"
    for t in rows:
        est = t.result.metrics["cost"]
        assert est.method.value == "analytic", est.method
        assert t.result.provenance.evaluator.startswith("learned:cost@real")


def test_a_prediction_is_never_served_from_the_cache(tmp_path):
    """The model refits as the record grows, so last run's prediction is not this one's."""
    db = str(tmp_path / "w.db")
    _train(db, widths=(1, 2, 3, 4))
    first = Widths([9], rig(evaluator={"learned": {"metric": "cost"}}))
    out1 = run_loop(first, _request(db), log=lambda _m: None)
    _train(db, widths=(9,))               # 9 is now measured: cost 28
    second = Widths([9], rig(evaluator={"learned": {"metric": "cost"}}))
    out2 = run_loop(second, _request(db), log=lambda _m: None)
    was = [s.metrics["cost"] for s in out1.scored if s.stage == "learned"][0]
    now = [s.metrics["cost"] for s in out2.scored if s.stage == "learned"][0]
    assert was != now and now == 28.0, (was, now)


def test_the_screen_can_cut_what_is_not_worth_the_real_stage(tmp_path):
    db = str(tmp_path / "w.db")
    _train(db)
    problem = Widths([1, 2, 3, 4, 5],
                     rig(evaluator={"learned": {"metric": "cost", "within": 0.95,
                                                "higher_is_better": False}}))
    out = run_loop(problem, _request(db), log=lambda _m: None)
    assert len(problem.measured) < 5, "the screen spent the real stage on fewer designs"
    assert any("went no further" in l for l in out.lessons), out.lessons


def test_a_learned_stage_refuses_to_be_the_only_stage():
    model = Surrogate(metric="cost")
    with pytest.raises(ValueError, match="cannot be a problem's only stage"):
        model.stages(None, [])
    with pytest.raises(ValueError, match="already a stage of this problem"):
        model.stages(None, ["learned"])


def test_a_candidate_with_nothing_to_predict_from_is_not_dropped(tmp_path):
    """A missing knob takes the training mean: a neutral prediction moves the ordering
    nowhere, which is honest -- refusing the candidate would drop it from the chain."""
    db = str(tmp_path / "w.db")
    _train(db)

    class Blank(Widths):
        def search(self, state):
            yield [Candidate(name="blank", artifact="", knobs={"style": "odd"})]

        def measure(self, cand, stage, state):
            predicted = self.role_measure(cand, stage, state)
            return predicted if predicted is not None else {"cost": 20.0}

    problem = Blank([], rig(evaluator={"learned": {"metric": "cost"}}))
    out = run_loop(problem, _request(db), log=lambda _m: None)
    got = [s for s in out.scored if s.stage == "learned"]
    assert got and "0 feature(s) of 1" in got[0].payload["predicted_from"]
    assert out.decision is not None and out.decision.stage == "real", "it still climbed"


def test_a_document_switches_the_screen_on():
    doc = {"id": "learned", "statement": "write the word good", "extension": ".txt",
           "roles": {"evaluator": {"learned": {"metric": "bytes", "within": 0.9}}},
           "gate": {"test": ["true"]},
           "stages": [{"name": "size", "command": ["wc", "-c", "{artifact}"],
                      "metrics_re": {"bytes": r"(\d+)"}}],
           "objectives": [{"metric": "bytes", "direction": "minimize"}]}
    problem = PromptProblem(TaskSpec.from_dict(doc))
    model = problem.roles().evaluator
    assert model.metric == "bytes" and model.within == 0.9
    assert problem.stages() == ["size"], "and it is not in the chain until it is fitted"
    with pytest.raises(TaskError, match="needs the `metric`"):
        TaskSpec.from_dict({**doc, "roles": {"evaluator": "learned"}})
    with pytest.raises(TaskError, match="takes"):
        TaskSpec.from_dict({**doc, "roles": {"evaluator": {"learned": {"metric": "b",
                                                                       "layers": 4}}}})


def test_the_registry_offers_it():
    from flux_loop import available_roles

    assert available_roles("evaluator") == ["learned"]
    assert make_role("evaluator", {"learned": {"metric": "cost"}}).name == "learned"
