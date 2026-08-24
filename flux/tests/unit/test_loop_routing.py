"""Where an evaluator's result goes, and when a pass stops (docs/decisions.md D463).

Cedric's two notes on the drawing: an evaluator's result can go back to the GENERATOR to improve
the design in hand, or on to the ORCHESTRATOR for the next candidate -- and the fast/slow pair is
only an example, the chain can be any evaluators in any order. Plus the drawing's two edges the
loop did not have: the "input/problem valid?" gate, and stopping when a design is good enough or
the clock runs out (both optional).

What these pin: any stage in the chain can send a design back, an improved part replaces what was
admitted for it, routing that fails is not a gate, `improve` is a kind of work the orchestrator
chooses among, a mis-posed problem is refused before anything is spent, and both early stops are
OFF unless something turns them on.
"""

from __future__ import annotations

import time

import pytest
from flux_loop import (Candidate, Improve, LoopRequest, Problem, PromptProblem, Scored, TaskSpec,
                       Template, Verdict, rig, run_loop)


class Fabric(Problem):
    """One part, drafted by a template that widens the design each time it is asked."""

    name = "fabric"

    def __init__(self, *, target: float = 8.0, roles=None) -> None:
        self.target = target
        self._roles = roles
        self.drafts: list[str] = []
        self.sent_back: list[str] = []

    def objective(self, request):
        return {"study": "fabric"}

    def subgoals(self):
        return ["fabric"]

    def generator(self, subgoal, state):
        def render(attempt):
            width = 2.0 if attempt.prior is None else float(attempt.prior.knobs["width"]) * 2
            self.drafts.append(f"w{width:g}")
            return Candidate(name=f"w{width:g}", artifact=f"width={width:g}",
                             knobs={"width": width})
        return Template(render)

    def build(self, cand, subgoal, state):
        return cand.artifact

    def judge(self, built, cand, subgoal, state):
        return Verdict(True, 0.0)

    def compose(self, admitted, state):
        return admitted.get("fabric")

    def stages(self):
        return ["estimate"]

    def measure(self, cand, stage, state):
        return {"served": float(cand.knobs["width"])}

    def decide(self, pool, state):
        best = max(pool, key=lambda s: s.metrics["served"])
        return best, "the most served"

    def route(self, stage, scored, state):
        """The numbers say "close, but not there": back to the generator, not to the
        orchestrator."""
        out = []
        for s in scored:
            if s.metrics["served"] < self.target:
                self.sent_back.append(s.candidate.name)
                out.append(Improve(candidate=s.candidate, stage=stage, subgoal=s.candidate.subgoal,
                                   why=f"served {s.metrics['served']:g}, the target is "
                                       f"{self.target:g}: widen it"))
        return out


def _request(tmp_path, **kw):
    kw.setdefault("steps", 6)
    return LoopRequest(db=str(tmp_path / "r.db"), prototype=False, compute=False,
                       critique_rounds=0, **kw)


def test_a_stage_can_send_a_design_back_to_the_generator(tmp_path):
    problem = Fabric()
    out = run_loop(problem, _request(tmp_path), proposer=None, log=lambda _m: None)
    assert problem.drafts == ["w2", "w4", "w8"], (
        "each draft started from the design the numbers sent back")
    assert problem.sent_back == ["w2", "w4"], "and the one that met the target was not sent back"
    assert any("went back to the generator" in l for l in out.lessons), out.lessons
    best = max(s.metrics["served"] for s in out.scored)
    assert best == 8.0 and out.decision is not None


def test_an_improved_part_replaces_what_was_admitted_for_it(tmp_path):
    """The composition has to use the design the numbers approved of."""
    problem = Fabric()
    out = run_loop(problem, _request(tmp_path), proposer=None, log=lambda _m: None)
    assert out.admitted["fabric"].name == "w8", out.admitted
    assert out.decision.candidate.artifact == "width=8", out.decision.candidate


def test_routing_that_fails_is_not_a_gate(tmp_path):
    said: list[str] = []

    class Broken(Fabric):
        def route(self, stage, scored, state):
            raise RuntimeError("the routing rule is broken")

    out = run_loop(Broken(), _request(tmp_path, steps=2), proposer=None, log=said.append)
    assert any("routing did not run" in m for m in said), said
    assert out.decision is not None, "the run still decided on what it measured"


def test_improve_is_work_the_orchestrator_chooses_among(tmp_path):
    """With a design in hand and a part waiting, the no-model rule improves first (D463)."""
    problem = Fabric(roles=rig(orchestrator="rules"))
    out = run_loop(problem, _request(tmp_path), proposer=None, log=lambda _m: None)
    assert problem.drafts[:2] == ["w2", "w4"]
    assert out.decision is not None

    asked: list[str] = []

    class Asked:
        def propose(self, prompt: str) -> str:
            asked.append(prompt)
            return '{"next": "improve"}'

    class Searching(Fabric):
        def search(self, state):
            yield [Candidate(name="found", artifact="width=1", knobs={"width": 1.0})]

    run_loop(Searching(roles=rig(orchestrator="llm")), _request(tmp_path, steps=3),
             proposer=Asked(), log=lambda _m: None)
    assert any("improve:" in p and "sent back" in p for p in asked), asked


# ------------------------------------------------------- the chain is any chain
def test_the_chain_can_be_any_evaluators_and_says_which_are_modelled(tmp_path):
    import flux_profile

    class Chain(Fabric):
        def stages(self):
            return ["guess", "estimate", "simulate", "place"]

        def analytic_stages(self):
            return frozenset({"guess", "estimate"})

        def route(self, stage, scored, state):
            return []

        def measure(self, cand, stage, state):
            return {"served": float(cand.knobs["width"]), "stage": stage}

        def finalists(self, front, state, stage=""):
            return list(front)

    flux_profile.reset()
    out = run_loop(Chain(), _request(tmp_path, steps=1), proposer=None, log=lambda _m: None)
    assert [s.stage for s in out.scored] == ["guess", "estimate", "simulate", "place"], out.scored
    assert out.decision is not None and out.decision.stage == "place", "the last stage answers"
    paths = set(flux_profile.tree())
    assert ("evaluation", "analytical: guess") in paths
    assert ("evaluation", "analytical: estimate") in paths
    assert ("evaluation", "simulation: simulate") in paths, (
        "what is modelled is declared, not inferred from a stage's position")
    assert ("evaluation", "simulation: place") in paths


# --------------------------------------------------------------- stopping, optionally
def test_neither_early_stop_is_on_by_default(tmp_path):
    out = run_loop(Fabric(target=0.0), _request(tmp_path, steps=2), proposer=None,
                   log=lambda _m: None)
    assert out.stopped == "nothing left to do" and not out.cut_short, out.stopped


def test_a_wall_clock_stops_the_pass_and_says_so(tmp_path):
    class Slow(Fabric):
        def measure(self, cand, stage, state):
            time.sleep(0.05)
            return {"served": 1.0}

        def route(self, stage, scored, state):
            return [Improve(candidate=s.candidate, stage=stage, why="again") for s in scored]

    out = run_loop(Slow(), _request(tmp_path, steps=50, budget_s=0.1), proposer=None,
                   log=lambda _m: None)
    assert out.stopped == "the wall clock" and out.cut_short
    assert len(out.scored) < 50, "it did not run every step"


def test_good_enough_stops_the_pass_with_its_reason(tmp_path):
    class Enough(Fabric):
        def good_enough(self, state):
            best = max((s.metrics["served"] for s in state.scored), default=0.0)
            return f"served {best:g} reaches the target" if best >= self.target else None

    problem = Enough(target=4.0)
    out = run_loop(problem, _request(tmp_path, steps=9), proposer=None, log=lambda _m: None)
    assert out.stopped.startswith("good enough: served 4"), out.stopped
    assert not out.cut_short, "a target met is not a budget cut"
    assert any("good enough" in l for l in out.lessons)
    assert problem.drafts == ["w2", "w4"], "it stopped instead of widening again"


# ------------------------------------------------------- input/problem valid?
def test_a_mis_posed_problem_is_refused_before_anything_is_spent(tmp_path):
    class Impossible(Fabric):
        def validate(self, request):
            return ["the target is 12 and no stage measures anything above 8"]

    with pytest.raises(RuntimeError, match="cannot be answered as posed"):
        run_loop(Impossible(), _request(tmp_path), proposer=None, log=lambda _m: None)


def test_a_document_that_asks_for_what_it_cannot_measure_is_refused(tmp_path):
    doc = {"id": "typo", "statement": "write the word good", "extension": ".txt",
           "gate": {"test": ["true"]},
           "stages": [{"name": "size", "command": ["wc", "-c", "{artifact}"],
                      "metrics_re": {"bytes": r"(\d+)"}}],
           "objectives": [{"metric": "byets", "direction": "minimize"}]}
    problem = PromptProblem(TaskSpec.from_dict(doc))
    wrong = problem.validate(LoopRequest())
    assert wrong and "no stage measures" in wrong[0] and "bytes" in wrong[0], wrong
    with pytest.raises(RuntimeError, match="cannot be answered as posed"):
        run_loop(problem, _request(tmp_path), proposer=None, log=lambda _m: None)


def test_a_cutoff_on_a_metric_its_stage_does_not_measure_is_refused():
    doc = {"id": "typo2", "statement": "write it", "extension": ".txt",
           "gate": {"test": ["true"]},
           "stages": [{"name": "size", "command": ["wc", "-c", "{artifact}"],
                      "metrics_re": {"bytes": r"(\d+)"},
                      "cutoff": {"metric": "fmax_mhz", "at": 600}},
                     {"name": "lines", "command": ["wc", "-l", "{artifact}"],
                      "metrics_re": {"lines": r"(\d+)"}}]}
    wrong = PromptProblem(TaskSpec.from_dict(doc)).validate(LoopRequest())
    assert any("cuts on 'fmax_mhz'" in w for w in wrong), wrong
