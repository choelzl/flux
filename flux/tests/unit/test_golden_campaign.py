"""THE GOLDEN CAMPAIGN (D531, review step 14): a two-part document in a tiny world, scripted
replies, a fake measurer -- which design stands, why, and that a second pass rests. Under a
minute, no tool on PATH needed; what the loop promises, end to end, pinned in one place."""

from __future__ import annotations

from flux_llm import ScriptedProposer
from flux_loop import LoopRequest, PromptProblem, TaskSpec, Verdict, run_loop


class _Tiny:
    """Two parts, `front` and `back`, each a short text; a design passes when it says its
    part's name; the whole is the two joined; a design's "fmax" is its length, its "area"
    the count of vowels -- measured, never modelled."""

    def __init__(self, problem):
        self.problem = problem
        self.measured: list[str] = []

    def build(self, cand, subgoal, state):
        return cand.artifact

    def judge(self, built, cand, subgoal, state):
        ok = subgoal in built
        return Verdict(ok, 0.0 if ok else 1.0, "" if ok else f"the text does not say {subgoal}")

    def measure(self, cand, stage, state):
        self.measured.append(f"{cand.name}@{stage}")
        text = cand.artifact
        return {"fmax_mhz": float(len(text)) * 10, "area_um2": float(sum(text.count(v) for v in "aeiou"))}

    def report(self, out):
        return [f"  the tiny world measured {len(self.measured)} time(s)"]


def test_the_golden_campaign_stands_on_the_numbers_and_rests(tmp_path):
    doc = {"id": "golden", "statement": "two texts that name their part", "parts": ["front", "back"],
           "world": __name__ + ":_Tiny",
           "objectives": [{"metric": "fmax_mhz", "direction": "maximize", "goal": 500, "unit": "MHz"},
                          {"metric": "area_um2", "direction": "minimize"}],
           "stages": [{"name": "screen", "metrics": ["fmax_mhz", "area_um2"]}],
           "budget": {"steps": 6, "repair_attempts": 2, "critique_rounds": 0, "prototype": False},
           "roles": {"orchestrator": "rules"},                 # the parts in the document's order, no plan turn
           "campaign": {"name": "golden", "study": "golden"}}
    db = str(tmp_path / "golden.db")
    said: list[str] = []
    prob = PromptProblem(TaskSpec.from_dict(doc))
    replies = ['{"artifact": "the front text is here, long enough to clear the goal", "why": "-"}',
               '{"artifact": "back", "why": "-"}']
    req = LoopRequest(db=db, steps=6, repair_attempts=2, critique_rounds=0, prototype=False)
    out = run_loop(prob, req, proposer=ScriptedProposer(replies), log=said.append)
    # both parts proven and admitted; the whole measured on the screen; the decision by the vector
    assert sorted(out.admitted) == ["back", "front"]
    assert out.decision is not None and out.decision.stage == "screen"
    whole = out.decision.metrics
    assert whole["fmax_mhz"] == 10.0 * len(out.decision.candidate.artifact) and whole["fmax_mhz"] >= 500
    assert out.decided_by == "the least area_um2 at fmax_mhz >= 500"
    assert any("decision" in m and "fmax_mhz=" in m for m in out.lessons)
    # the record holds it under the document's name (D524)
    from flux_records import Records

    rec = Records(db, objective={"study": "golden"}, name="golden")
    assert rec.resumed and rec.campaign_id == "golden"
    rec.close("paused")
    # a second pass changes nothing and says so (D518): the parts reload frozen, nothing is due
    said2: list[str] = []
    prob2 = PromptProblem(TaskSpec.from_dict(doc))
    out2 = run_loop(prob2, req, proposer=ScriptedProposer([]), log=said2.append)
    assert out2.at_rest and out2.stopped.startswith("at rest: nothing was due on any part")
    assert sorted(out2.admitted) == ["back", "front"] and out2.decision.candidate.artifact == out.decision.candidate.artifact
    assert any("kept frozen" in m or "reload" in m for m in said2)
    from flux_loop import task_report_lines

    lines = task_report_lines(prob2.task, out2, prob2)
    assert lines[0].startswith("TASK golden") and any("the tiny world measured" in ln for ln in lines)
