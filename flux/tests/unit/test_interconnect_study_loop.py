"""The interconnect study's own half of the loop (docs/decisions.md D446): the menu it registers,
and the result it hands back.

The search itself places silicon -- minutes of Yosys and OpenROAD per step -- so what is checked
here is everything around it: that the five actions are built against what the store actually
holds, that the problem's gate refuses a fabric that cannot route before a placement is spent on
it, and that a run which placed nothing reports no decision with its limits stated rather than
raising or inventing one.
"""

from __future__ import annotations

import pytest
from flux_loop import BuildError, LoopResult, LoopRequest, LoopState


@pytest.fixture
def problem(tmp_path):
    from flux_interconnect.loop import InterconnectProblem
    from flux_interconnect.study import InterconnectRequest

    return InterconnectProblem(InterconnectRequest(db=str(tmp_path / "ic.db"), llm_round=0,
                                                   rounds=1, decide_on_finalists=2))


def test_the_menu_is_built_against_what_the_store_holds(problem):
    from flux_interconnect.flow import SCOPE_KEYS, StudyContext, build_menu

    ctx = StudyContext(args=problem.request, ask=None, results=[], series=1)
    actions = {a.name: a for a in build_menu(ctx)}
    assert set(actions) == {"anneal", "enumerate", "propose", "measure", "repair"}
    # enumerate is the one action with a fixed variant set: the families, and only those
    assert {v["family"] for v in actions["enumerate"].variants} == set(SCOPE_KEYS)
    # an empty store has nothing to repair and nothing yet worth placing, so neither action
    # claims outstanding work it cannot do
    assert actions["repair"].variants == () and actions["measure"].variants == ()
    assert actions["propose"].key_of({"count": 6}) is None, "asking again is never a repeat"
    assert actions["anneal"].key_of({"from": "", "chains": 16, "steps": 1200, "seed": 0}) \
        != actions["anneal"].key_of({"from": "", "chains": 16, "steps": 1200, "seed": 1}), (
        "a second ensemble at a new seed is a new search, not a repeat")


def test_a_fabric_that_cannot_route_is_refused_before_a_placement_is_spent(problem):
    """D319/D324, at the loop's gate: the screen stops new unroutable fabrics entering, and the
    gate re-checks the ones a warm store already holds -- the demo once reported one as its
    smallest fabric meeting timing while the decision stage kept failing to place it."""
    from flux_loop import Candidate

    state = LoopState(request=LoopRequest(), say=lambda _m: None, proposer=None, feedback=None)
    # 28 clients into 4 banks through one 4x4 stage: the last stage cannot reach 32 banks
    narrow = {"kind": "xbar_staged", "clients": 28, "banks": 32, "width_bits": 128,
              "stages": [{"switches": 1, "in": 28, "out": 4}]}
    problem.specs["narrow"] = narrow
    with pytest.raises(BuildError):
        problem.build(Candidate(name="narrow"), None, state)
    unbuildable = {"kind": "not-a-family"}
    problem.specs["nonsense"] = unbuildable
    with pytest.raises(BuildError):
        problem.build(Candidate(name="nonsense"), None, state)


def test_every_report_section_runs_against_a_store_that_holds_nothing(problem, capsys):
    """The sections that print AFTER the search -- coverage, where the time went, the lessons,
    the tally, the measured table, the qualifying set -- run at the very end of a run that has
    already spent hours. A NameError in one of them is the failure mode this repo keeps hitting
    (D317/D321/D323: a defect in a nested function nothing could import, with the suite green),
    so they are called here over an empty store, where every branch is the empty one."""
    from flux_loop import SearchReport
    from flux_interconnect.flow import print_search_summary, qualifying_fabrics

    empty = SearchReport(steps=[], unexplored=[], stopped_because="nothing to do")
    assert print_search_summary(problem.request, empty, None, 0) == []
    measured, rejected, qualifying, corrected = qualifying_fabrics(problem.request)
    assert (measured, rejected, qualifying, corrected) == ({}, {}, {}, {})
    printed = capsys.readouterr().out
    assert "COVERAGE" in printed and "WHERE THE TIME WENT" in printed
    assert "FABRICS ATTEMPTED" in printed and "MEASURED by" in printed


def test_a_run_that_placed_nothing_reports_no_decision_and_says_why(problem, monkeypatch):
    from flux_interconnect import flow

    monkeypatch.setattr(flow, "bank_area_mm2", lambda: (0.5, "a note"), raising=False)
    empty = LoopResult(decision=None, decided_by="nothing was placed", frontier=[], confirmed=[],
                       scored=[], admitted={}, refused=[("some-fabric", "cannot route")],
                       lessons=[], not_established=["nothing was measured; there is no frontier"],
                       notes=[], provenance={})
    out = problem.report(empty)
    assert out.decision is None and not out.met_requirement
    assert out.refused["some-fabric"] == "cannot route"
    assert any("optimistic on frequency" in n for n in out.not_established)
    assert not any("nothing was measured" in n for n in out.not_established), (
        "the loop's generic line is replaced by this study's own limits")
    assert out.provenance["steps"] == 0 and out.provenance["clients"] == 28
    assert "no fabric cleared the requirement" in out.summary()
