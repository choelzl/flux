"""D895: a design of parts decides on the whole only. A part measured alone climbs (so it can be
improved), but is never the loop's answer: the NLU's decision was one operator -- sigmoid, rsqrt,
recip -- pass after pass, each measured alone and smaller and faster than the NLU itself."""

from __future__ import annotations

from flux_loop import Candidate, Improve, LoopRequest, Objectives, Problem, Template, Verdict, run_loop
from flux_loop.objective import Objective


class Two(Problem):
    """Two parts; each alone is cheap, the whole costs their sum."""
    name = "two"

    def __init__(self, whole=True):
        self.whole, self.sent = whole, False

    def route(self, stage, scored, state):
        """Part a of the first whole goes back once: improved, it is measured alone (as the NLU's are)."""
        if self.sent or not scored:
            return []
        self.sent = True
        a = state.admitted["a"]
        return [Improve(candidate=a, stage=stage, subgoal="a", why="make it smaller")]

    def objectives(self):
        return Objectives([Objective("cost", "minimize")])

    def subgoals(self):
        return ["a", "b"]

    def generator(self, subgoal, state):
        def render(attempt):
            n = 2 if attempt.prior is not None else 1                 # the improved a: a new design
            return Candidate(name=f"{subgoal}{n}", artifact="y" * n if subgoal == "a" else "xx")
        return Template(render)

    def build(self, cand, subgoal, state):
        return cand.artifact

    def judge(self, built, cand, subgoal, state):
        return Verdict(True, 0.0)

    def compose(self, admitted, state):
        if not self.whole:
            return None
        return Candidate("ab", "".join(c.artifact for c in admitted.values()))

    def stages(self):
        return ["screen"]

    def measure(self, cand, stage, state):
        return {"cost": float(len(cand.artifact))}


def test_the_decision_is_the_whole_never_a_part(tmp_path):
    out = run_loop(Two(), LoopRequest(db=str(tmp_path / "t.db"), prototype=False, critique_rounds=0, steps=4),
                   proposer=None, log=lambda _m: None)
    assert out.decision is not None and out.decision.candidate.name == "ab", out.decision
    alone = [s for s in out.scored if s.candidate.subgoal == "a" and not (s.candidate.meta or {}).get("composed")]
    assert alone and min(s.metrics["cost"] for s in alone) < out.decision.metrics["cost"], "a part alone was cheaper"


def test_parts_without_a_whole_decide_nothing(tmp_path):
    said: list[str] = []
    out = run_loop(Two(whole=False), LoopRequest(db=str(tmp_path / "t.db"), prototype=False, critique_rounds=0, steps=4),
                   proposer=None, log=said.append)
    assert out.decision is None, out.decision


def test_the_results_say_which_piece_each_design_is(tmp_path):
    """D896: the web's results name each design's group -- the whole, or its part -- so the charts
    can colour them apart."""
    from flux_web.results import _designs

    db = str(tmp_path / "t.db")
    run_loop(Two(), LoopRequest(db=db, prototype=False, critique_rounds=0, steps=4), proposer=None, log=lambda _m: None)
    got = _designs(db, [{"name": "screen"}], None, 100)
    groups = {d["name"]: d["group"] for d in got["designs"]}
    assert all(g == "whole" for n, g in groups.items() if n.startswith("ab")) and groups.get("a2") == "a", groups
