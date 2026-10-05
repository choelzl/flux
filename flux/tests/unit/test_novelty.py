"""D839: away from the first design. An exploring pass asks for a NEW design with the standing one
shown to beat, not handed over to be edited; a draft that repeats a measured design is refused
before it is built and told so; a fresh draft reads what was tried, the best first."""

from __future__ import annotations

from test_agent_sessions import _digits, _fake, _turns

from flux_loop import PromptProblem, request_for, run_loop
from flux_loop.novelty import tried_block, twin
from flux_loop.types import Candidate, Improve, LoopState, Scored

DIGITS = "\n".join(str(i) for i in range(10)) + "\n"


def _state(task, tmp_path):
    return LoopState(request=request_for(task, db=""), say=lambda _m: None, proposer=None, feedback=None,
                     workdir=str(tmp_path / "trace"))


def test_an_exploring_pass_asks_for_a_new_design_not_an_edit(tmp_path):
    from flux_loop.loop import _improve_step

    task = _digits(_fake(tmp_path), role="genok")
    prob = PromptProblem(task)
    state = _state(task, tmp_path)
    standing = Candidate("digits#1", "0\n1\n2\n3\n4\n5\n6\n7\n8\n9")
    _improve_step(prob, state, Improve(standing, "It meets the goal; make it better.", subgoal=None, explore=True))
    asked = _turns(tmp_path)[0]["text"]
    assert "Write a NEW design that beats it" in asked and "THE STANDING DESIGN, digits#1" in asked
    assert "do not edit or resend it" in asked and "THE LAST DRAFT" not in asked, "shown to beat, not handed over"


def test_a_draft_that_repeats_a_measured_design_is_refused_before_it_is_built(tmp_path):
    from flux_loop.sources import iterate

    task = _digits(_fake(tmp_path), role="genok")
    prob = PromptProblem(task)
    state = _state(task, tmp_path)
    state.scored.append(Scored(Candidate("digits#7", DIGITS), "test", {"score": 1.0}))
    said: list[str] = []
    state.say = said.append
    source = prob.generator(None, state)
    cand, _built, why = iterate(prob, source, None, state)
    assert cand is None, "the agent wrote it again each time"
    assert any("is digits#7 again, already measured" in m for m in said), said
    first, again = _turns(tmp_path)[:2]
    assert "TRIED SO FAR (1 design(s) measured" in first["text"] and "digits#7" in first["text"]
    assert "This design is digits#7 again" in again["text"] and "Write a DIFFERENT design" in again["text"]


def test_what_was_tried_is_listed_the_best_first(tmp_path):
    task = _digits(_fake(tmp_path))
    prob = PromptProblem(task)
    state = _state(task, tmp_path)
    for i, (score, why) in enumerate([(3.0, "a table"), (9.0, "a loop over the digits"), (5.0, "")]):
        state.scored.append(Scored(Candidate(f"d#{i}", f"text {i}", meta={"why": why}), "test", {"score": score}))
    assert twin(state, Candidate("x", "text   1")) is state.scored[1], "spacing aside"
    assert twin(state, Candidate("x", "text 4")) is None
    block = tried_block(prob, state, None)
    lines = block.splitlines()
    assert lines[0].startswith("TRIED SO FAR (3 design(s)") and len(lines) == 4
    assert lines[1].startswith("- d#2"), "without objectives: the latest first"
    assert "d#1" in block and "a loop over the digits" in block and "a table" in block
    body, _schema = prob.design_prompt(None, "", state, None, None, "")
    assert "TRIED SO FAR" in body


def test_an_exploring_pass_with_nothing_to_explore_from_says_so(tmp_path):
    """D842: not a silent "nothing left to do" pass after pass."""
    from flux_loop.loop import _explore_items

    task = _digits(_fake(tmp_path))
    state = _state(task, tmp_path)
    said: list[str] = []
    state.say = said.append
    assert _explore_items(PromptProblem(task), state) == []
    assert any("nothing to explore from -- no design admitted yet" in m for m in said), said


def test_a_coding_agent_orchestrating_needs_no_model(tmp_path):
    """D843: `orchestrate: opencode` is a coding agent's turns (D640): no start refused for want of
    Flux's model; a box the model answers still needs it."""
    from flux_loop import load_task
    from flux_loop.task import model_use

    (tmp_path / "problem.yaml").write_text("statement: s\nflow:\n  test: 'true'\n  generate: opencode\n  orchestrate: opencode\nobjectives: []\n")
    assert model_use(load_task(str(tmp_path / "problem.yaml"))) == ""
    (tmp_path / "problem.yaml").write_text("statement: s\nflow:\n  test: 'true'\n  generate: opencode\n  plan: model\nobjectives: []\n")
    assert model_use(load_task(str(tmp_path / "problem.yaml"))) == "plan: model"
    (tmp_path / "problem.yaml").write_text("statement: s\nflow:\n  test: 'true'\n  orchestrate: opencode\nobjectives: []\n")
    assert model_use(load_task(str(tmp_path / "problem.yaml"))) == "it writes the candidates", "no generate: the model drafts"


def test_a_pass_with_a_standing_design_builds_one_by_a_direction(tmp_path):
    """D845: one pass, one design -- a loop whose design stands does not rest: the pass refines or
    explores and ends once a design passed the gate."""
    db = str(tmp_path / "d.db")
    task = _digits(_fake(tmp_path), role="genok")
    first = run_loop(PromptProblem(task), request_for(task, db=db), proposer=None, log=lambda _m: None)
    assert first.decision is not None and first.stopped == "one design a pass", first.stopped
    said: list[str] = []
    again = run_loop(PromptProblem(task), request_for(task, db=db), proposer=None, log=said.append)
    assert any("direction: refine" in m for m in said), said
    # the fake agent writes the same digits each time: refused as a repeat, tried again, never a rest
    assert any("is digits#1 again, already measured" in m for m in said)
    assert again.stopped.startswith("no new design passed the gate") and not again.at_rest, again.stopped


def test_the_rules_refine_while_the_decision_moves_then_explore_in_turn():
    from types import SimpleNamespace

    from flux_loop.direction import choose

    def state(decisions):
        rows = [{"decision": d} for d in decisions]                    # newest first
        return SimpleNamespace(records=SimpleNamespace(conclusions=lambda limit=50: rows),
                               request=SimpleNamespace(explore_after=2))

    prob = SimpleNamespace(roles=lambda: SimpleNamespace(orchestrator=None))
    standing = Candidate("p#3", "x")
    assert choose(prob, state(["p#3", "p#2"]), standing, "")[0] == "refine"
    assert choose(prob, state(["p#3", "p#3"]), standing, "")[0] == "refine", "stood one pass: fewer than two"
    assert choose(prob, state(["p#3", "p#3", "p#3"]), standing, "")[0] == "explore"
    assert choose(prob, state(["p#3"] * 4), standing, "")[0] == "refine", "then in turn"
    asked = SimpleNamespace(roles=lambda: SimpleNamespace(orchestrator=SimpleNamespace(direction=lambda p, s, lines: ("explore", "the front is flat"))))
    assert choose(asked, state(["p#3", "p#2"]), standing, "") == ("explore", "the orchestrator: the front is flat")
