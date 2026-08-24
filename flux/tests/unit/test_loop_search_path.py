"""The loop's SEARCH path (docs/decisions.md D446): a problem that proposes candidates in
batches instead of writing one part at a time.

Five applications predate `flux_loop` and none of them is a "write one part, judge it, freeze
it" problem: macarray enumerates a space and invents multipliers, bankmap climbs a chain of
solvers, interconnect_mapping crosses two fields, prefetcher climbs and shrinks, interconnect
lets a model direct a menu. What they share is the shape these tests pin: batches through a
gate, one measurement call per batch, a frontier, finalists on a costlier stage, a decision.
"""

from __future__ import annotations

import pytest
from flux_loop import (BuildError, Candidate, LoopRequest, Problem, StageNames, Verdict,
                       run_loop)


class Sweep(Problem):
    """A two-stage sweep: the batch is the space, odd designs do not build, the coarse stage
    scores every survivor at once and the fine stage confirms the finalists."""

    name = "sweep"

    def __init__(self, *, batches: int = 1, fine: bool = True) -> None:
        self.batches = batches
        self.fine = fine
        self.batch_sizes: list[int] = []      # how many candidates each measure call received
        self.reviewed: list[tuple[str, int]] = []
        self.seen_scored: list[int] = []      # what each yield handed back

    def objective(self, request):
        return {"study": "sweep", "batches": self.batches}

    def search(self, state):
        for b in range(self.batches):
            got = yield [Candidate(name=f"d{b}-{i}", knobs={"size": i},
                                   meta={"strategy": "enumerate"})
                         for i in range(1, 5)]
            self.seen_scored.append(len(got))

    def build(self, cand, subgoal, state):
        if cand.knobs["size"] % 2:
            raise BuildError(f"size {cand.knobs['size']} is odd and will not build")
        return cand.knobs["size"]

    def judge(self, built, cand, subgoal, state):
        return Verdict(True, 0.0)

    def stages(self):
        return ["coarse", "fine"] if self.fine else ["coarse"]

    def analytic_stages(self):
        return frozenset({"coarse"})

    def measure_batch(self, cands, stage, state):
        self.batch_sizes.append(len(cands))
        step = 1.0 if stage == "coarse" else 0.9        # the fine stage is a little pessimistic
        return [{"value": c.knobs["size"] * step, "cost": float(c.knobs["size"]),
                 "note": f"{c.name}@{stage}"} for c in cands]

    def review(self, stage, batch, state):
        self.reviewed.append((stage, len(batch)))

    def frontier_axes(self):
        return (lambda p: p.metrics["value"], lambda p: p.metrics["cost"])


def test_a_batch_goes_through_the_gate_and_one_measurement_call(tmp_path):
    problem = Sweep()
    out = run_loop(problem, LoopRequest(db=str(tmp_path / "s.db"), steps=4, finalists=2),
                   log=lambda _m: None)
    # the gate refused the odd sizes, and only the survivors were measured -- in ONE call
    assert problem.batch_sizes == [2, 2], "the coarse stage took the batch; the fine stage the finalists"
    assert [name for name, _why in out.refused] == ["d0-1", "d0-3"]
    assert all("will not build" in why for _n, why in out.refused)
    assert [s.name for s in out.scored if s.stage == "coarse"] == ["d0-2", "d0-4"]
    assert problem.seen_scored == [2], "the generator is handed its own batch's results"
    assert problem.reviewed == [("coarse", 2), ("fine", 2)]
    # numbers are metrics; everything else the stage returned travels as the payload
    coarse = next(s for s in out.scored if s.stage == "coarse")
    assert set(coarse.metrics) == {"value", "cost"} and coarse.payload["note"] == "d0-2@coarse"
    assert out.decision is not None and out.decision.stage == "fine"


def test_the_standings_say_what_a_search_has_done_not_what_it_has_proven(monkeypatch):
    """The live panel (D418l) stands on parts proven, and a search has none: a study that has
    screened four designs must not read "not yet tried" for the whole run (D446)."""
    import flux_profile
    from flux_tui.panels import standings_lines

    seen: list[dict] = []
    monkeypatch.setattr(flux_profile, "publish",
                        lambda kind, payload: seen.append(payload) if kind == "standings" else None)
    run_loop(Sweep(), LoopRequest(steps=2, finalists=1), log=lambda _m: None)
    assert seen and all(p["searching"] for p in seen) and all(p["parts"] == [] for p in seen)
    last = seen[-1]
    assert last["gated"] == 2 and last["refused"] == 2
    assert last["measured"] == 3, "two on the coarse stage, one finalist on the fine one"
    lines, _roles = standings_lines(last)
    assert "2 gated, 2 refused" in lines[0] and "proven" not in lines[0]
    assert any("3 design(s) on the chain" in l for l in lines)


def test_the_frontier_and_the_finalists_are_the_problems_to_choose(tmp_path):
    """The default is the two declared axes and a spread along the cost axis; a problem with
    more objectives, or a report that must compare an incumbent on one stage, overrides them."""

    class Four(Sweep):
        def frontier(self, scored, state):
            return [s for s in scored if s.metrics["cost"] <= 2]      # a four-cost Pareto, here faked

        def finalists(self, front, state, stage=""):
            return list(front) + [s for s in state.scored if s.name == "d0-4"]   # + the incumbent

    problem = Four()
    out = run_loop(problem, LoopRequest(steps=2, finalists=1), log=lambda _m: None)
    assert [s.name for s in out.frontier] == ["d0-2"]
    assert sorted(s.name for s in out.confirmed) == ["d0-2", "d0-4"]


def test_one_stage_decides_on_the_first_and_screen_only_says_so(tmp_path):
    out = run_loop(Sweep(fine=False), LoopRequest(steps=2), log=lambda _m: None)
    assert out.decision is not None and out.decision.stage == "coarse" and not out.confirmed
    assert not any("must not be quoted" in n for n in out.not_established)
    held = run_loop(Sweep(), LoopRequest(steps=2, screen_only=True), log=lambda _m: None)
    assert not held.confirmed
    assert any("every number is from the coarse stage" in n for n in held.not_established)


def test_a_short_result_list_is_refused_rather_than_repaired(tmp_path):
    """The ABI's length invariant (D165), applied to the loop's own batch: results are paired to
    candidates positionally, so a short list would silently re-pair every result after the gap
    with the wrong candidate."""

    class Loses(Sweep):
        def measure_batch(self, cands, stage, state):
            return super().measure_batch(cands, stage, state)[:-1]

    with pytest.raises(RuntimeError, match="one per candidate, in order"):
        run_loop(Loses(), LoopRequest(steps=1), log=lambda _m: None)


def test_a_stage_that_cannot_measure_one_candidate_refuses_only_that_one(tmp_path):
    class Fails(Sweep):
        def measure_batch(self, cands, stage, state):
            return [{"error": "the tool crashed"} if c.knobs["size"] == 2 else got
                    for c, got in zip(cands, super().measure_batch(cands, stage, state))]

    out = run_loop(Fails(fine=False), LoopRequest(steps=1), log=lambda _m: None)
    assert [s.name for s in out.scored] == ["d0-4"]
    assert ("d0-2", "coarse: the tool crashed") in out.refused


def test_the_record_holds_the_problems_own_stages_and_the_gates_refusals(tmp_path):
    db = str(tmp_path / "r.db")
    problem = Sweep()
    out = run_loop(problem, LoopRequest(db=db, steps=2, finalists=1), log=lambda _m: None)
    from flux_records import Records

    rec = Records(db, objective=problem.objective(None))
    assert rec.resumed
    assert set(rec.stages()) == {"coarse", "fine"}, "the costed stages are the problem's own"
    assert len(rec.known(stage="coarse", metric="value")) == 2
    assert rec.known(stage="coarse", metric="value")[0][0]["size"] == 4, "knobs, for the extractor"
    refused = rec.refusal_rows(stage=StageNames.GATE)
    assert {r.candidate["name"] for r in refused} == {"d0-1", "d0-3"}
    assert rec.conclusions(limit=1)[0]["decision"] == out.decision.name
    assert {r.stage for r in rec.known_rows()} == {"coarse", "fine"}
    # the method tag: a modelled stage is ANALYTIC, a measured one SIMULATED (D446)
    from flux_evaluator_abi import Method
    from flux_store import CampaignStore

    with CampaignStore(db) as store:
        trials = {t.stage: t for t in store.trials(rec.campaign_id, status="ok")}
    assert trials["coarse"].result.metrics["value"].method == Method.ANALYTIC
    assert trials["fine"].result.metrics["value"].method == Method.SIMULATED
    assert trials["fine"].result.provenance.evaluator == "sweep@fine"


def test_a_failed_measurement_is_never_cached_as_an_answer(tmp_path):
    """The rule `flux_cache.CachedBatch` already had (D436), now on the loop's own per-candidate
    path: a tool that crashed once must not become this candidate's answer for every later run."""

    class Flaky(Sweep):
        def __init__(self) -> None:
            super().__init__(fine=False)
            self.calls = 0

        def measure(self, cand, stage, state):
            self.calls += 1
            return {"error": "the tool crashed"} if self.calls == 1 else {
                "value": 1.0, "cost": 1.0}

        def measure_batch(self, cands, stage, state):
            from flux_loop import cached_measure

            return [cached_measure(self, state, c, stage) for c in cands]

        def search(self, state):
            yield [Candidate(name="one", knobs={"size": 2})]

    db = str(tmp_path / "c.db")
    problem = Flaky()
    first = run_loop(problem, LoopRequest(db=db, steps=1), log=lambda _m: None)
    assert not first.scored and first.refused == [("one", "coarse: the tool crashed")]
    again = run_loop(problem, LoopRequest(db=db, steps=1), log=lambda _m: None)
    assert [s.name for s in again.scored] == ["one"], "the second run measured it again"
    assert problem.calls == 2
    third = run_loop(problem, LoopRequest(db=db, steps=1), log=lambda _m: None)
    assert [s.name for s in third.scored] == ["one"] and problem.calls == 2, (
        "the measurement that worked IS cached")


def test_an_empty_batch_is_a_step_that_measured_nothing(tmp_path):
    """A search round that proposes nothing (a model round that returned no legal candidate) is
    a spent step, not the end of the search."""

    class Quiet(Sweep):
        def search(self, state):
            yield []
            yield [Candidate(name="late", knobs={"size": 2})]

    out = run_loop(Quiet(fine=False), LoopRequest(steps=4), log=lambda _m: None)
    assert [s.name for s in out.scored] == ["late"]


def test_the_parts_path_is_untouched_by_the_search_hook(tmp_path):
    """`search` returning None keeps the loop on the parts path: a problem that writes one part
    at a time (NLU, the task document) is unchanged by D446."""

    class Parts(Problem):
        name = "parts"

        def objective(self, request):
            return {"study": "parts"}

        def subgoals(self):
            return ["a"]

        def generate(self, subgoal, method, state, human):
            return Candidate(name="c", artifact="x", subgoal=subgoal), "built", ""

        def build(self, cand, subgoal, state):
            return "built"

        def judge(self, built, cand, subgoal, state):
            return Verdict(True, 0.0)

        def compose(self, admitted, state):
            return admitted["a"]

        def measure(self, cand, stage, state):
            return {"value": 1.0}

    out = run_loop(Parts(), LoopRequest(steps=2), log=lambda _m: None)
    assert list(out.admitted) == ["a"] and out.decision is not None
    assert out.decision.metrics == {"value": 1.0}
