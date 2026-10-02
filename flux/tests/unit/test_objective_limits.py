"""Several limits, an order and a balance in one objective vector (D658); several gates on one
stage (D657)."""

from __future__ import annotations

import pytest

from flux_loop import Candidate, Objectives, PromptProblem, Scored, TaskError, TaskSpec

DOC = [{"metric": "fmax_mhz", "direction": "maximize", "goal": 1000},
       {"metric": "area_um2", "direction": "minimize", "goal": 80},
       {"metric": "power_w", "direction": "minimize"}]


def _sc(name, stage="confirm", **metrics) -> Scored:
    return Scored(Candidate(name, ""), stage, metrics, {})


def test_every_goal_is_a_limit_and_the_goal_less_one_decides_among_those_meeting_them():
    objs = Objectives.from_doc(DOC)
    assert [o.metric for o in objs.limits] == ["fmax_mhz", "area_um2"] and objs.goal is objs[0]
    pool = [_sc("fast-big", fmax_mhz=1200, area_um2=95, power_w=0.1),      # misses area
            _sc("slow-small", fmax_mhz=900, area_um2=50, power_w=0.1),     # misses fmax
            _sc("ok-hot", fmax_mhz=1100, area_um2=70, power_w=0.9),
            _sc("ok-cool", fmax_mhz=1010, area_um2=79, power_w=0.4)]
    pick, why = objs.decide(pool)
    assert pick.candidate.name == "ok-cool"
    assert why == "the least power_w at fmax_mhz >= 1000 and area_um2 <= 80"
    # the pairwise rule agrees: meeting both beats missing one, however cool
    assert objs.better(pool[3].metrics, pool[0].metrics) and not objs.better(pool[0].metrics, pool[3].metrics)
    assert objs.better(pool[3].metrics, pool[2].metrics)
    # every limit met with power still to shrink: not good enough; every limit alone: it is
    assert objs.good_enough(pool[3].metrics) is None
    two = Objectives.from_doc(DOC[:2])
    assert two.good_enough(pool[3].metrics) == "fmax_mhz is 1010, the 1000 asked for; area_um2 is 79, the 80 asked for"
    assert two.good_enough(pool[0].metrics) is None


def test_none_meets_every_limit_the_closest_wins_and_the_words_say_so():
    objs = Objectives.from_doc(DOC)
    pool = [_sc("far", fmax_mhz=500, area_um2=200, power_w=0.1),        # misses both
            _sc("near", fmax_mhz=1100, area_um2=84, power_w=0.9),       # misses area by 5%
            _sc("nearer-miss", fmax_mhz=990, area_um2=60, power_w=0.5)]  # misses fmax by 1%
    pick, why = objs.decide(pool)
    assert pick.candidate.name == "nearer-miss"
    assert why == "nothing meets every limit (fmax_mhz >= 1000 and area_um2 <= 80); the closest misses fmax_mhz (990 for 1000)"
    assert objs.better(pool[1].metrics, pool[0].metrics)                 # fewer limits missed
    assert objs.better(pool[2].metrics, pool[1].metrics)                 # the smaller relative shortfall


def test_the_balance_is_the_knee_among_the_designs_meeting_every_limit():
    objs = Objectives.from_doc([{"metric": "power_w", "direction": "minimize", "goal": 1.0},
                                {"metric": "fmax_mhz", "direction": "maximize", "balance": True},
                                {"metric": "area_um2", "direction": "minimize", "balance": True}])
    pool = [_sc("hot-knee", fmax_mhz=900, area_um2=40, power_w=2.0),     # the knee overall, but misses power
            _sc("fast", fmax_mhz=1000, area_um2=100, power_w=0.5),
            _sc("knee", fmax_mhz=800, area_um2=45, power_w=0.5),
            _sc("small", fmax_mhz=300, area_um2=30, power_w=0.5)]
    pick, why = objs.decide(pool)
    assert pick.candidate.name == "knee" and why == "the knee of fmax_mhz / area_um2 at power_w <= 1"
    assert objs.describe() == "power_w at most 1, then the balance of fmax_mhz and area_um2"
    with pytest.raises(ValueError, match="balance objective has no goal"):
        Objectives.from_doc([{"metric": "x", "goal": 1, "balance": True}])


def test_keep_is_still_a_limit_resolved_over_the_pool():
    objs = Objectives.from_doc([{"metric": "speedup", "direction": "maximize", "keep": 0.9, "above": 1.0},
                                {"metric": "bytes", "direction": "minimize", "goal": 4096},
                                {"metric": "cost", "direction": "minimize"}])
    pool = [_sc("best", speedup=1.5, bytes=8000, cost=1), _sc("kept", speedup=1.46, bytes=2000, cost=5),
            _sc("cheap", speedup=1.2, bytes=100, cost=0), _sc("keeps-more", speedup=1.47, bytes=3000, cost=3)]
    pick, why = objs.decide(pool)                     # 1.0 + 0.9 * 0.5 = 1.45 kept, and within 4096 bytes
    assert pick.candidate.name == "keeps-more" and "speedup >= 1.45 and bytes <= 4096" in why
    assert objs.describe() == "speedup within 90% of the best's gain over 1, bytes at most 4096, then least cost"


def test_describe_and_the_document_round_trip():
    objs = Objectives.from_doc(DOC)
    assert objs.describe() == "fmax_mhz at least 1000, area_um2 at most 80, then least power_w"
    assert Objectives.from_doc([{"metric": "fmax_mhz", "balance": True},
                                {"metric": "area_um2", "direction": "minimize", "balance": True}]).describe() \
        == "the balance of fmax_mhz and area_um2"
    doc = [*DOC, {"metric": "energy_pj", "direction": "minimize", "balance": True}]
    back = Objectives.from_doc([o.to_doc() for o in Objectives.from_doc(doc)])
    assert back == Objectives.from_doc(doc) and back[3].to_doc()["balance"] is True
    assert "balance" not in back[0].to_doc()


def _spec(cutoff):
    return TaskSpec.from_dict({"id": "t",
                               "statement": "s",
                               "objectives": [{"metric": "fmax_mhz"}, {"metric": "area_um2", "direction": "minimize"}],
                               "flow": {"test": {"test": ["true"]},
                                        "measure": {"screen": {"command": ["true"],
                                                               "metrics": ["fmax_mhz", "area_um2"],
                                                               "cutoff": cutoff},
                                                    "place": {"command": ["true"],
                                                              "metrics": ["fmax_mhz", "area_um2"]}}}})


def test_a_stage_with_two_gates_cuts_on_either_and_says_which():
    gates = [{"metric": "fmax_mhz", "at": 1000}, {"metric": "area_um2", "below": 80}]
    spec = _spec(gates)
    assert spec.stages[0].cutoffs == tuple(gates)
    assert TaskSpec.from_dict(spec.to_dict()).stages[0].cutoff == spec.stages[0].cutoff
    assert spec.to_dict()["flow"]["measure"]["screen"]["cutoff"] == gates
    scored = [_sc("slow", "screen", fmax_mhz=900, area_um2=50), _sc("big", "screen", fmax_mhz=1200, area_um2=90),
              _sc("good", "screen", fmax_mhz=1100, area_um2=70)]
    kept, why = PromptProblem(spec).cutoff("screen", scored, None)
    assert [s.candidate.name for s in kept] == ["good"]
    assert why == "fmax_mhz below 1000 (slow); area_um2 above 80 (big)"
    with pytest.raises(TaskError, match=r"cutoff\[1\] needs exactly one"):
        _spec([gates[0], {"metric": "area_um2"}])
    bad = _spec([gates[0], {"metric": "power_w", "below": 1}])
    assert any("cuts on 'power_w'" in w for w in PromptProblem(bad).validate(None))


def test_the_single_dict_gate_is_still_one_gate():
    spec = _spec({"metric": "fmax_mhz", "at": 1000})
    assert spec.stages[0].cutoff == {"metric": "fmax_mhz", "at": 1000} and len(spec.stages[0].cutoffs) == 1
    assert spec.to_dict()["flow"]["measure"]["screen"]["cutoff"] == {"metric": "fmax_mhz", "at": 1000}
    kept, why = PromptProblem(spec).cutoff("screen", [_sc("slow", "screen", fmax_mhz=900, area_um2=1),
                                                      _sc("fast", "screen", fmax_mhz=1100, area_um2=1)], None)
    assert [s.candidate.name for s in kept] == ["fast"] and why == "fmax_mhz below 1000"


def test_every_display_names_every_limit(tmp_path):
    """The task-check line, the standing the orchestrator reads, and the DSE prompt say every
    limit, not the first alone (D660)."""
    from flux_llm import ScriptedProposer
    from flux_loop import LoopRequest, PromptProblem, TaskSpec, run_loop

    stage = ["{python}", "-c", "import sys; x = int(sys.argv[1]); print(f'fmax_mhz={900 + 50 * x}'); "
             "print(f'area_um2={40 + 10 * x}')", "{x}"]
    doc = {"id": "two",
           "statement": "s",
           "objectives": [{"metric": "fmax_mhz", "direction": "maximize", "goal": 1000}, {"metric": "area_um2", "direction": "minimize", "goal": 60}],
           "flow": {"orchestrate": {"by": "model", "space": {"x": [0, 1, 2, 3]}},
                    "test": {"test": ["true"]},
                    "measure": {"run": {"command": stage, "metrics": ["fmax_mhz", "area_um2"]}}}}
    prob = PromptProblem(TaskSpec.from_dict(doc))
    words = prob.objectives().describe()
    assert "fmax_mhz at least 1000" in words and "area_um2 at most 60" in words
    assert prob.standing(None)["goal"] == words
    model = ScriptedProposer(['{"points": [{"x": 2}], "why": "-"}'])
    run_loop(prob, LoopRequest(steps=1, finalists=0, screen_only=True, prototype=False), proposer=model,
             log=lambda _m: None)
    assert "area_um2 at most 60" in model.prompts[0], "the DSE prompt names the second limit"


def test_the_design_the_objectives_choose_always_climbs():
    """D798: one finalist spread along fmax-vs-area took a curve end; the smallest design that
    makes the clock -- what the objectives choose -- stayed screened and was never placed."""
    from flux_loop import Candidate, LoopRequest, LoopState, PromptProblem, Scored, TaskSpec

    doc = {"id": "pe", "statement": "s",
           "objectives": [{"metric": "fmax_mhz", "direction": "maximize", "goal": 1000},
                          {"metric": "area_um2", "direction": "minimize"}],
           "flow": {"test": "true", "measure": {"screen": "echo x", "confirm": "echo x"}}}
    for st in doc["flow"]["measure"]:
        doc["flow"]["measure"][st] = {"command": "echo x", "metrics": ["fmax_mhz", "area_um2"]}
    prob = PromptProblem(TaskSpec.from_dict(doc))
    rows = [Scored(Candidate(n, n), "screen", {"fmax_mhz": f, "area_um2": a})
            for n, f, a in (("small_slow", 880, 268), ("leader", 1204, 325), ("big_fast", 1400, 600))]
    state = LoopState(request=LoopRequest(finalists=1), say=lambda _m: None, proposer=None, feedback=None)
    front = prob.frontier(rows, state)
    assert [s.name for s in prob.finalists(front, state, "confirm")] == ["leader"]
