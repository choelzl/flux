"""The evaluator's model half (D560, review 2 R9): a surrogate predicts the costly stage from
the record, orders the finalists, writes its predictions as predictions, never decides."""

from __future__ import annotations

import json

from flux_loop import Candidate, LoopRequest, Problem, Verdict, run_loop
from flux_loop.roles import Roles, make_role
from flux_loop.surrogate import Surrogate, predict_fit, predict_knn


class Widths(Problem):
    """Two stages: an estimate that reads 1/1.2 of the placement, over a width knob. The
    frontier is every estimated design; `finalists` of them are placed."""

    name = "widths"

    def __init__(self, widths, evaluator=None):
        self.widths = list(widths)
        self.evaluator = evaluator
        self.placed: list[int] = []

    def roles(self):
        return Roles(evaluator=self.evaluator)

    def objective(self, request):
        return {"study": "widths"}

    def objectives(self):
        from flux_loop import Objectives
        from flux_loop.objective import Objective

        return Objectives([Objective("fmax_mhz", "maximize"), Objective("area_um2", "minimize")])

    def search(self, state):
        yield [Candidate(name=f"w{w}", artifact="", knobs={"width": w}) for w in self.widths]

    def build(self, cand, subgoal, state):
        return cand.artifact

    def judge(self, built, cand, subgoal, state):
        return Verdict(True, 0.0)

    def stages(self):
        return ["estimate", "placement"]

    def analytic_stages(self):
        return frozenset({"estimate"})

    def frontier(self, scored, state):
        return list(scored)

    def measure(self, cand, stage, state):
        w = int(cand.knobs["width"])
        fmax = 100.0 * w + (0.0 if stage == "estimate" else 20.0 * w)    # placed = 1.2 x estimate
        if stage == "placement":
            self.placed.append(w)
        return {"fmax_mhz": fmax, "area_um2": 10.0 * w * w}


def _run(prob, tmp_path, **kw):
    kw.setdefault("steps", 2)
    return run_loop(prob, LoopRequest(db=str(tmp_path / "s.db"), prototype=False, critique_rounds=0, **kw),
                    proposer=kw.pop("proposer", None) if "proposer" in kw else None, log=lambda _m: None)


def test_the_nearest_measured_designs_predict_a_new_one():
    rows = [({"width": 1, "kind": "a"}, {"f": 10.0}), ({"width": 3, "kind": "a"}, {"f": 30.0}),
            ({"width": 5, "kind": "b"}, {"f": 50.0})]
    assert predict_knn(rows, {"width": 3, "kind": "a"}, "f") == 30.0                  # an exact match is itself
    assert 10.0 < predict_knn(rows, {"width": 2, "kind": "a"}, "f", k=2) < 30.0        # between its neighbours
    assert predict_knn(rows, {"width": 2, "kind": "a"}, "nope") is None
    numeric = [({"width": w}, {"f": 120.0 * w}) for w in (1, 2, 3, 4)]
    assert abs(predict_fit(numeric, {"width": 9}, "f") - 1080.0) < 1e-6              # the plane extrapolates
    assert predict_fit(numeric, {"width": 9, "kind": "a"}, "f") is None              # a categorical knob: no plane
    assert predict_fit(numeric[:2], {"width": 9}, "f") is None                        # too few rows


def test_the_surrogate_orders_the_finalists_by_prediction_and_records_it_as_one(tmp_path):
    """Pass 1 places four widths (no surrogate). Pass 2, with the surrogate, brings four new
    widths: the calibration and the record predict them, the two best-predicted are placed
    first, and the predictions sit on the record as `placement~predicted`, tagged analytic."""
    from flux_records import Records

    first = Widths([1, 2, 3, 4])
    out1 = run_loop(first, LoopRequest(db=str(tmp_path / "s.db"), prototype=False, critique_rounds=0, steps=2, finalists=4),
                    log=lambda _m: None)
    assert sorted(first.placed) == [1, 2, 3, 4] and out1.decision is not None

    sur = make_role("evaluator", {"surrogate": {"k": 2}})
    assert isinstance(sur, Surrogate) and sur.kind == "fitted"
    second = Widths([8, 5, 9, 6], evaluator=sur)
    said = []
    out2 = run_loop(second, LoopRequest(db=str(tmp_path / "s.db"), prototype=False, critique_rounds=0, steps=2, finalists=2),
                    log=said.append)
    assert any("4 design(s) measured on placement and 2 calibration(s) fit the prediction" in m for m in said)
    assert any("4 candidate(s) ordered by their predicted fmax_mhz on placement" in m for m in said)
    assert second.placed == [9, 8], "the two best-predicted widths were placed, best first"
    assert out2.decision.candidate.knobs == {"width": 9} and out2.decision.stage == "placement"
    rows = Records(str(tmp_path / "s.db"), objective={"study": "widths"}).known_rows(stage="placement~predicted")
    assert {r.candidate["width"] for r in rows} == {5, 6, 8, 9}
    by_width = {r.candidate["width"]: r.metrics for r in rows}
    assert abs(by_width[9]["fmax_mhz"] - 1080.0) < 1e-6                # the estimate 900 x the calibrated 1.2
    measured = Records(str(tmp_path / "s.db"), objective={"study": "widths"}).known_rows(stage="placement")
    assert {r.candidate["width"] for r in measured} == {1, 2, 3, 4, 8, 9}, "a prediction is not a measurement"


def _seeded(db: str) -> None:
    """A record with widths 1..3 placed, for a surrogate to learn from."""
    run_loop(Widths([1, 2, 3]), LoopRequest(db=db, prototype=False, critique_rounds=0, steps=2, finalists=3),
             log=lambda _m: None)


def test_the_model_half_reads_the_table_and_falls_back_to_the_fit(tmp_path):
    from flux_llm import ScriptedProposer

    db = str(tmp_path / "llm.db")
    _seeded(db)
    proposer = ScriptedProposer([json.dumps({"predictions": [{"index": 0, "fmax_mhz": 100.0}, {"index": 1, "fmax_mhz": 9000.0}]})])
    second = Widths([8, 4], evaluator=make_role("evaluator", {"surrogate": {"kind": "llm"}}))
    said = []
    run_loop(second, LoopRequest(db=db, prototype=False, critique_rounds=0, steps=2, finalists=1),
             proposer=proposer, log=said.append)
    assert second.placed == [4], "the model said width 4 places higher; it was placed first (and alone)"
    assert "PREDICT what the placement stage will measure" in proposer.prompts[0] and "MEASURED ON placement (3 design(s))" in proposer.prompts[0]
    assert any("(the model's reading)" in m for m in said)

    db = str(tmp_path / "broken.db")
    _seeded(db)
    third = Widths([8, 4], evaluator=make_role("evaluator", {"surrogate": {"kind": "llm"}}))
    run_loop(third, LoopRequest(db=db, prototype=False, critique_rounds=0, steps=2, finalists=1),
             proposer=ScriptedProposer(["not json at all"]), log=lambda _m: None)
    assert third.placed == [8], "no usable prediction from the model: the fitted half orders (8 predicts higher)"


def test_without_a_record_or_a_stage_the_surrogate_stays_out_of_the_way(tmp_path):
    prob = Widths([2, 1], evaluator=make_role("evaluator", "surrogate"))
    out = run_loop(prob, LoopRequest(db="", prototype=False, critique_rounds=0, steps=2, finalists=1), log=lambda _m: None)
    assert prob.placed == [2] and out.decision is not None            # the calibration alone orders: 2 predicts higher


def test_the_document_names_the_surrogate_on_its_analytical_line():
    from flux_loop import PromptProblem, TaskSpec
    from flux_loop.task import describe_flow
    import pytest

    doc = {"id": "s", "statement": "s", "gate": {"test": ["true"]}, "roles": {"evaluator": {"surrogate": {"kind": "llm"}}},
           "stages": [{"name": "screen", "command": ["m"], "metrics_re": {"fmax_mhz": r"(\d+)"}},
                      {"name": "confirm", "command": ["m"], "metrics_re": {"fmax_mhz": r"(\d+)"}}],
           "objectives": [{"metric": "fmax_mhz", "direction": "maximize"}]}
    prob = PromptProblem(TaskSpec.from_dict(doc))
    assert isinstance(prob.roles().evaluator, Surrogate) and prob.roles().evaluator.kind == "llm"
    assert any("surrogate (llm) predicts the costly stage" in line for line in describe_flow(prob.task, prob))
    with pytest.raises(ValueError, match="surrogate kind is fitted or llm"):
        make_role("evaluator", {"surrogate": {"kind": "vibes"}})
