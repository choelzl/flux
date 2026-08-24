"""D529/D530 (review steps 12 and 11): the NLU's mentor has the mined source; the world's
instruments -- error_map, compare, quantisation -- answer on a prototype in about a second,
and a prototype turn offers them as tools beside the loop's."""

from __future__ import annotations

import sys
import time

import pytest

from flux_loop import LoopRequest, LoopState


def test_the_nlu_mentor_reads_the_mined_source_too():
    from flux_knowledge import Mined
    from nlu_fixtures import nlu_problem

    prob = nlu_problem(ops=("exp",), test_rounds=0)
    mentor = prob.knowledge()
    assert any(isinstance(s, Mined) for s in mentor.sources), "D529: the AI half of knowledge is a declared source"
    st = LoopState(request=LoopRequest(db=""), say=lambda _m: None, proposer=None, feedback=None)
    assert mentor.text("mined", st) == "", "nothing to mine without a record: an empty block, never an error"


@pytest.mark.skipif(sys.platform != "linux", reason="the sandbox uses resource limits")
def test_the_instruments_answer_on_the_exp_prototype_in_about_a_second(tmp_path):
    from flux_nlu.instruments import compare, error_map, quantisation
    from nlu_fixtures import nlu_problem
    from test_nlu_vectorize import SCALAR_EXP

    prob = nlu_problem(ops=("exp",), ulp_budget=1, clock_period_ps=1250.0, seed=1, test_rounds=0)
    st = LoopState(request=LoopRequest(), say=lambda _m: None, proposer=None, feedback=None, workdir=str(tmp_path))
    world = prob.world
    t0 = time.monotonic()
    text = error_map(world, SCALAR_EXP, "exp", st)
    t_map = time.monotonic() - t0
    assert text.startswith("exp: 0 of 65536 beyond 1 ULP") and "exact" in text
    assert "by sign and binade" in text or "exact on every binade" in text
    broken = SCALAR_EXP.replace("return pack_fp16(0, k, p, M)", "return pack_fp16(0, k + 1, p, M)")
    t0 = time.monotonic()
    text = compare(world, SCALAR_EXP, broken, "exp", st)
    t_cmp = time.monotonic() - t0
    assert text.startswith("A and B differ on ") and "A: 0 over budget" in text and "B: " in text
    assert "A is closer to the reference on" in text and "first differing inputs: x=0x" in text
    t0 = time.monotonic()
    text = quantisation(world, SCALAR_EXP, "exp", st)
    t_q = time.monotonic() - t0
    assert text.startswith("1 table(s) built") or text.startswith("2 table(s) built") or text[0].isdigit()
    assert "rom(" in text and "zero-order (lookup) error up to" in text and "output ULP" in text
    assert max(t_map, t_cmp / 2, t_q) < 6.0, f"an instrument took too long: map {t_map:.2f}s, compare {t_cmp:.2f}s, quant {t_q:.2f}s"
    # a prototype that does not run says so instead of raising
    assert "did not produce output" in error_map(world, "def design(x):\n    return nope(x)\n", "exp", st) or "cannot be made" in error_map(world, "def design(x):\n    return nope(x)\n", "exp", st)
    # the turn's tools: the loop's and the world's
    names = [t.name for t in prob.tools("exp", st, "prototype", None)]
    assert names[:2] == ["compute", "history"] and {"error_map", "compare", "quantisation"} <= set(names)
    assert "error_map" not in [t.name for t in prob.tools("exp", st, "generate", None)]


@pytest.mark.skipif(sys.platform != "linux", reason="the sandbox uses resource limits")
def test_the_family_tool_reports_every_member_and_binds_nothing(tmp_path):
    """D535 (review §1.3.9): the family search as an instrument -- the model reads every
    member's over-count and cost and the one the check would bind; its own text stays."""
    from flux_nlu.instruments import family
    from nlu_fixtures import nlu_problem
    from test_nlu_family import knobbed_exp

    prob = nlu_problem(ops=("exp",), ulp_budget=1, clock_period_ps=1250.0, seed=1, test_rounds=0)
    st = LoopState(request=LoopRequest(), say=lambda _m: None, proposer=None, feedback=None, workdir=str(tmp_path))
    code = knobbed_exp()
    text = family(prob.world, code, "exp", st)
    assert text.startswith("FAMILY SEARCH:") and "member(s) tried" in text
    assert "every member, best first" in text and "<- the check would bind this" in text
    assert "knob" in text                                  # which knobs never changed the outcome
    assert "declares no SPACE" in family(prob.world, "def design(x):\n    return x\n", "exp", st)
    names = [t.name for t in prob.tools("exp", st, "prototype", None)]
    assert "family" in names
