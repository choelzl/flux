"""D526 (review step 8): the placed critical path as DATA -- parsed from OpenROAD's report,
kept on the part, said in the depth pass's ask and the route's why, and answered by the
`timing` tool. The report is one captured from openroad 26Q2 on a placed NLU part."""

from __future__ import annotations

from pathlib import Path

from flux_evaluator_openroad import parse_critical_path
from flux_loop import LoopRequest, LoopState
from flux_loop.timing import describe

REPORT = (Path(__file__).parent / "fixtures_timing_report.txt").read_text()


def test_the_report_parses_into_steps_with_their_nets_delays_and_the_slack():
    path = parse_critical_path("noise before\n" + REPORT)
    assert path["startpoint"] == "_6597_" and path["endpoint"] == "_6698_"
    assert path["slack_ps"] < -700 and path["met"] is False
    assert path["arrival_ps"] > 1900 and path["required_ps"] == 1242.059
    steps = path["steps"]
    assert steps[0]["pin"] == "_6597_/CLK" and steps[0]["delay_ps"] == 0.0
    assert steps[1]["pin"] == "_6597_/QN" and steps[1]["cell"].startswith("DFFHQNx1") and steps[1]["fanout"] == 2
    assert steps[1]["net"] == "_0024_", "the net is the line after its pin (`-fields {net}`)"
    assert all(s["time_ps"] >= 0 for s in steps) and steps[-1]["time_ps"] <= path["arrival_ps"] + 1e-6
    assert parse_critical_path("no path here") is None


def test_the_words_name_the_costliest_steps_and_say_when_the_nets_are_nameless():
    path = parse_critical_path(REPORT)
    text = describe(path, top=3)
    assert text.startswith("the placed critical path: ") and "ps of logic against 1242 ps required" in text
    assert "violated" in text and "the costliest steps: " in text and "NOR2xp33 driving 8" in text
    assert "drive 8 or more loads" in text
    assert "synthesis kept no signal names" in text, "abc renamed every net on this path; the words say so instead of guessing"
    # a net that kept an RTL name is named by its block
    named = {**path, "steps": [dict(s, net="u_exp.interp1__12_34[5]") for s in path["steps"]]}
    assert "the signals named: interp1" in describe(named) and "synthesis kept no" not in describe(named)
    assert describe(None) == "" and describe({}) == ""


def test_the_part_remembers_its_placed_path_and_the_tool_answers_with_it():
    from flux_loop import Candidate, Ladder, Objective, Objectives, Problem
    from flux_loop.ladder import measure_alone
    from flux_loop.tools import loop_tools

    path = parse_critical_path(REPORT)

    class P(Problem):
        name = "p"

        def subgoals(self):
            return ["exp"]

        def stages(self):
            return ["confirm"]

        def objectives(self):
            return Objectives([Objective("fmax_mhz", "maximize", goal=800)])

        def ladder(self):
            return Ladder()

        def measure(self, cand, stage, state):
            return {"fmax_mhz": 489.0, "area_um2": 323.0, "critical_path": path}

    st = LoopState(request=LoopRequest(), say=lambda _m: None, proposer=None, feedback=None)
    assert not any(t.name == "timing" for t in loop_tools(P(), "exp", st))
    m = measure_alone(P(), Candidate("exp_p4", "module", subgoal="exp"), st)
    assert m["fmax_mhz"] == 489.0 and st.part("exp").alone == {"fmax_mhz": 489.0, "area_um2": 323.0}
    assert st.part("exp").timing is path
    tool = next(t for t in loop_tools(P(), "exp", st) if t.name == "timing")
    out = tool.run({"steps": 4})
    assert out.startswith("the placed critical path") and "the path, in order" in out and out.count("\n  ") == 4
