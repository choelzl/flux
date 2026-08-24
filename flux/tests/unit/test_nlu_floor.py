"""D475: the floor generator -- a correct FP16 operator by construction, from a parametric
family searched exhaustively, emitted as RTL from the chosen configuration.

Three faces per family must agree: the integer numpy model (the D468 subset), the RTL, the
tables. The numpy face is checked here over the full domain in milliseconds; the RTL face
through the real gate when Verilator is on PATH, bit for bit against the numpy one."""

from __future__ import annotations

import shutil

import numpy as np
import pytest

from flux_nlu.floor import (FAMILIES, ExpConfig, RsqrtConfig, exp_model, floor, floor_ops,
                            rsqrt_model, search)
from flux_nlu.fp16 import all_inputs, reference, ulp_report


def test_every_family_has_members_at_zero_over_and_orders_them_by_cost():
    for op in floor_ops():
        found = search(op, limit=5)
        assert found, f"the {op} family has no configuration at 0 over"
        assert all(r["report"]["ok"] and r["report"]["over_budget"] == 0 for r in found)
        assert [r["cost"] for r in found] == sorted(r["cost"] for r in found)
        assert found[0]["tried"] >= len(found)


def test_the_models_are_integer_only_and_handle_every_special_case():
    xs = all_inputs()
    exp = exp_model(xs, ExpConfig())
    assert exp.dtype == np.uint16
    assert exp[0x7E00] == 0x7E00 and exp[0x7C00] == 0x7C00 and exp[0xFC00] == 0x0000
    assert exp[0x0000] == 0x3C00 and exp[0x8000] == 0x3C00               # exp(+-0) = 1
    assert exp[0x498C] == 0x7C00 and exp[0x498B] != 0x7C00               # the overflow edge
    assert (exp[0x0001:0x0400] == 0x3C00).all()                          # subnormal x -> 1.0
    rs = rsqrt_model(xs, RsqrtConfig(t=6, m=13, cb=8))
    assert rs[0x0000] == 0x7C00 and rs[0x8000] == 0xFC00                 # 1/sqrt(+-0) = +-Inf
    assert rs[0x7C00] == 0x0000 and rs[0x7E00] == 0x7E00
    assert rs[0xBC00] == 0x7E00                                          # negative -> NaN
    want = reference("rsqrt", xs)
    assert ulp_report("rsqrt", xs, rs)["ok"] and rs[0x3C00] == want[0x3C00] == 0x3C00


def test_the_hardware_subset_gate_accepts_what_the_families_do():
    """The floor's numpy is the subset the model is held to (D468): no float on the data path."""
    import inspect

    from flux_nlu.floor import exp_model as em, rsqrt_model as rm
    from flux_nlu.prototype_rules import hardware_subset_violations

    for fn in (em, rm):
        src = inspect.getsource(fn).replace(f"def {fn.__name__}(x: np.ndarray, cfg", "def design(x, cfg")
        assert hardware_subset_violations(src) == [], fn.__name__


def test_recip_is_a_family_too():
    """D476: the seeded reciprocal (D412) was the one operator not produced by the flow."""
    assert "recip" in floor_ops()
    f = floor("recip")
    assert f["config"] == {"t": 6, "m": 12, "cb": 4} and "RECIP_S" in f["source"]


def test_floor_returns_the_cheapest_source_with_its_alternatives():
    f = floor("rsqrt")
    assert f is not None and "module nlu_rsqrt" in f["source"] and "FLOOR DESIGN" in f["source"]
    assert f["config"] == {"t": 6, "m": 13, "cb": 8} and f["passing"] >= 3 and len(f["alternatives"]) == 3
    assert "RSQRT_T" in f["source"] and "RSQRT_S" in f["source"]


@pytest.mark.skipif(shutil.which("verilator") is None, reason="verilator not on PATH")
@pytest.mark.parametrize("op", sorted(FAMILIES))
def test_the_rtl_face_is_bit_identical_to_the_numpy_face(op, tmp_path):
    from flux_nlu.verify import build_sim

    f = floor(op)
    cfg = FAMILIES[op]["config"](**f["config"])
    xs = all_inputs()
    sim = build_sim(f["source"], top=f"nlu_{op}", latency=0, opcode=None, workdir=str(tmp_path))
    rtl = sim.run(xs)
    assert (rtl == FAMILIES[op]["model"](xs, cfg)).all()
    assert ulp_report(op, xs, rtl)["over_budget"] == 0


def test_the_problem_drafts_from_the_floor_when_asked(tmp_path):
    from flux_loop import LoopRequest, LoopState
    from flux_loop.sources import Attempt, Model, Template
    from flux_nlu.problem import NluProblem

    prob = NluProblem(ops=("exp", "rsqrt", "log"), ulp_budget=1, clock_period_ps=1250.0, seed=1)
    said = []
    on = LoopState(request=LoopRequest(params={"floor": True}), say=said.append, proposer=None,
                   feedback=None, workdir=str(tmp_path))
    src = prob.generator("rsqrt", on)
    assert isinstance(src, Template) and src.name == "floor"
    cand, why = src.draft(Attempt(subgoal="rsqrt", state=on, index=0))
    assert cand is not None and cand.name.startswith("floor_rsqrt[") and cand.meta["floor"]["t"] == 6
    assert any("floor rsqrt: 52 of 120 configurations reach 0 over" in m for m in said)
    nxt, _ = src.draft(Attempt(subgoal="rsqrt", state=on, index=1))
    assert nxt.meta["floor"] != cand.meta["floor"]                    # the next cheapest
    none, why = src.draft(Attempt(subgoal="rsqrt", state=on, index=999))
    assert none is None and "every one was tried" in why
    assert isinstance(prob.generator("log", on), (Model, type(None)))   # no family: the model
    off = LoopState(request=LoopRequest(), say=said.append, proposer=None, feedback=None,
                    workdir=str(tmp_path))
    assert isinstance(prob.generator("rsqrt", off), (Model, type(None)))


def test_the_checks_show_their_verdict_in_the_task_pane(tmp_path):
    """Cedric: "test: fast vectors (log) doesnt show much information for output". The
    fast check and the exhaustive proof put the counts, the trend and the failure text on
    their task row (D470's output slot)."""
    import flux_profile
    from flux_loop import Candidate, LoopRequest, LoopState
    from flux_nlu.problem import NluProblem
    from flux_tui.app import _PhaseTasks
    from flux_tui.events import EventBus

    class Identity:                       # y = x: wrong for exp everywhere but near 0
        def run(self, xs):
            return xs.astype(np.uint16)

    prob = NluProblem(ops=("exp",), ulp_budget=1, clock_period_ps=1250.0, seed=1)
    st = LoopState(request=LoopRequest(), say=lambda _m: None, proposer=None, feedback=None,
                   workdir=str(tmp_path))
    cand = Candidate("nlu_exp", "module nlu_exp; endmodule", subgoal="exp")
    bus = EventBus()
    flux_profile.set_listener(_PhaseTasks(bus))
    try:
        fails, text = prob.fast_check(Identity(), cand, "exp", st)
        fails2, _ = prob.fast_check(Identity(), cand, "exp", st)
        verdict = prob.judge(Identity(), cand, "exp", st)
    finally:
        flux_profile.clear_listener()
    tasks = [t for t in bus.snapshot()["tasks"] if t["name"].startswith(("test: fast", "prove:"))]
    fast, fast2, prove = tasks
    assert fails > 0 and fast["output"]["verdict"].startswith(f"FAILS: {fails} of ")
    assert "failures by input region" in fast["output"]["failures"]
    assert fast["params"]["design"] == "nlu_exp" and "trend" not in fast["output"]
    assert fast2["output"]["trend"] == f"previous check {fails} over -> now {fails2}"
    assert not verdict.ok and prove["output"]["verdict"].startswith("REFUSED: ")
    assert "65536" in prove["output"]["verdict"] and "failures" in prove["output"]
