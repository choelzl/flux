"""D479: the family search -- knobs and a SPACE in the model's prototype, every member run
against all 65,536 inputs in one sandboxed pass, the cheapest member at 0 over selected by
the transpiler's measured widths and bound into the prototype the loop keeps."""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

import pytest

from flux_loop.pyint.family import bind, configurations, describe_search, parse_family_output, space_of

sys.path.insert(0, str(Path(__file__).parent))
from test_nlu_transpile import prototype_of  # noqa: E402
from flux_llm import Reply
from flux_loop import Kind
from flux_loop.prototype import history, paper_excerpts, prefers_edits, prefix_for, reminder_for, spec_prompt
from flux_loop import ladder
from flux_loop.ladder import part_depth


def knobbed_exp() -> str:
    """The exp fixture with its configuration turned into knobs and a SPACE."""
    src, _cfg, _model = prototype_of("exp")
    fam = re.sub(r"class cfg:\n((?:    .*\n)+)", "", src)
    fam = fam.replace("def design(x):\n    return exp_model(x, cfg)",
                      "class cfg:\n    xf = XF\n    lb = LB\n    t = T\n    m = M\n    cb = CB\n"
                      "    LN2_BITS = 10\n    ff = XF + LB\n\ndef design(x):\n    return exp_model(x, cfg)")
    return ("XF = 12\nLB = 14\nT = 5\nM = 12\nCB = 8\n"
            "SPACE = {\"T\": [4, 5, 6], \"M\": [11, 12, 13], \"CB\": [0, 4, 8]}\n" + fam)


def _better(fmax, area, old_fmax, old_area, goal=800.0):
    """The NLU's rule as it was before D511, now the objective vector's one rule."""
    from flux_loop import Objective, Objectives

    objs = Objectives([Objective("fmax_mhz", "maximize", goal=goal, tie=0.03), Objective("area_um2", "minimize")])
    return objs.better({"fmax_mhz": fmax, "area_um2": area}, {"fmax_mhz": old_fmax, "area_um2": old_area})


def test_the_space_is_read_and_bound_from_the_prototype_itself():
    code = "T = 6\nM = 13\nSPACE = {'T': [5, 6, 6], 'M': [12, 13], 'bad': [1.5]}\n\ndef design(x):\n    return x\n"
    assert space_of(code) == {"T": [5, 6], "M": [12, 13]}
    members, note = configurations(space_of(code))
    assert members == [{"M": 12, "T": 5}, {"M": 12, "T": 6}, {"M": 13, "T": 5}, {"M": 13, "T": 6}] and note == ""
    bound = bind(code, {"T": 5, "M": 12})
    assert "T = 5" in bound and "M = 12" in bound and "SPACE" in bound
    assert space_of("def design(x):\n    return x\n") == {}
    # the knob's own assignment is a choice too, first (D483)
    assert space_of("M = 10\nSPACE = {'M': [12, 13]}\n") == {"M": [10, 12, 13]}
    many, note = configurations({"a": list(range(20)), "b": list(range(20))}, cap=50)
    assert len(many) == 50 and "400 members" in note


def test_family_output_is_parsed_and_described():
    out = "CFG 0 over 12\nCFG 1 error boom\nCFG 2 over 0\nBEST 2\nOUT abcd\n"
    overs, best, hexout = parse_family_output(out)
    assert overs == {0: 12, 1: "error: boom", 2: 0} and best == 2 and hexout == "abcd"
    members = [{"T": 4}, {"T": 5}, {"T": 6}]
    text = describe_search(members, overs, 2, "", {2: 900})
    assert text.startswith("FAMILY SEARCH: 3 member(s) tried, 1 at 0 over, 1 raised an error.")
    assert "picked: {'T': 6} -> 0 over, cost proxy 900" in text and "first error: error: boom" in text


@pytest.mark.skipif(sys.platform != "linux", reason="the sandbox uses resource limits")
def test_the_check_searches_the_family_and_binds_the_cheapest_member(tmp_path):
    from flux_loop import LoopRequest, LoopState
    from nlu_fixtures import nlu_problem

    prob = nlu_problem(ops=("exp",), ulp_budget=1, clock_period_ps=1250.0, seed=1, test_rounds=0)
    said: list[str] = []
    st = LoopState(request=LoopRequest(), say=said.append, proposer=None, feedback=None,
                   workdir=str(tmp_path))
    v = prob.prototype_check(knobbed_exp(), "exp", st)
    assert v.ok and v.why.startswith("FAMILY SEARCH: 27 member(s) tried, 5 at 0 over.")
    assert v.payload["member"] == {"CB": 8, "M": 12, "T": 5}
    assert "T = 5" in v.payload["prototype"] and "M = 12" in v.payload["prototype"]
    assert any(m.startswith("  FAMILY SEARCH") for m in said)


@pytest.mark.skipif(sys.platform != "linux", reason="the sandbox uses resource limits")
def test_a_family_with_no_member_at_zero_reports_the_best_one(tmp_path):
    from flux_loop import LoopRequest, LoopState
    from nlu_fixtures import nlu_problem

    prob = nlu_problem(ops=("exp",), ulp_budget=1, clock_period_ps=1250.0, seed=1, test_rounds=0)
    st = LoopState(request=LoopRequest(), say=lambda _m: None, proposer=None, feedback=None,
                   workdir=str(tmp_path))
    code = (knobbed_exp().replace('SPACE = {"T": [4, 5, 6], "M": [11, 12, 13], "CB": [0, 4, 8]}',
                                  'SPACE = {"T": [2, 3], "CB": [0]}')     # too coarse to reach 0
            .replace("T = 5\n", "T = 2\n").replace("CB = 8\n", "CB = 0\n"))   # the defaults too (D483)
    v = prob.prototype_check(code, "exp", st)
    assert not v.ok and 0 < v.score < 65536
    assert "FAMILY SEARCH: 2 member(s) tried, 0 at 0 over" in v.why
    assert "(the best member of the family)" in v.why and "failures by input region" in v.why


@pytest.mark.skipif(sys.platform != "linux", reason="the sandbox uses resource limits")
def test_the_knowledge_task_says_what_the_pass_stands_on():
    """D487 (Cedric: "more details to the knowledge tasks"): `prepare` returns what it
    prepared and the loop puts it in the task pane -- the suite per operator, the gate, the
    toolkit and the verified operators, the mentor's sources."""
    from flux_loop import LoopRequest, LoopState
    from nlu_fixtures import nlu_problem

    prob = nlu_problem(ops=("exp", "recip"), ulp_budget=1, clock_period_ps=1250.0, seed=1, test_rounds=0)
    st = LoopState(request=LoopRequest(), say=lambda _m: None, proposer=None, feedback=None)
    st.prototypes = {"exp": "def design(x):\n    return x\n"}
    d = prob.prepare(st)
    assert d["operators"] == "exp, recip" and "65536 FP16 inputs" in d["gate"]
    assert d["fast-check suite"].startswith("exp: ") and "floor" in d["fast-check suite"]
    assert d["verified prototypes"] == "exp" and "verified operators exp as blocks" in d["toolkit"]
    assert "methods sheet" in d["knowledge"] and d["test author"].startswith("off")
    # D489 (Cedric: "could use more data/text/content"): the content itself -- the floor's
    # vectors, the reference, every block's signature, the sheet, the excerpts, the read-back
    assert d["reference"].startswith("exp: y = e^x; recip: y = 1/x")
    assert d["suite floor"].startswith(f"{prob.vectors['exp'].size} vectors") and "0x7c00" in d["suite floor"]
    assert "pack_fp16(sign, e_unb, v, frac_bits)" in d["toolkit"] and "recip_fixed(" in d["toolkit"]
    assert "piecewise-poly" in d["knowledge: methods sheet"]
    assert d["knowledge: library excerpts"] and d["knowledge: record read-back"]   # text or "(nothing to say)"
    assert not any(k.startswith("suite ") and k.endswith("authored") for k in d)   # nothing authored yet


def test_the_loop_keeps_the_bound_prototype(tmp_path):
    """The prototype the loop stores, records and transpiles is the chosen member, not the
    text with the knobs still open."""
    import json

    from flux_loop import LoopRequest, LoopState, _generate_with_model
    from nlu_fixtures import nlu_problem

    seen: list[str] = []

    class Proto:
        def propose(self, prompt, *, schema=None, tools=None, budget=None):
            seen.append(prompt)
            if "PROTOTYPE FIRST" in prompt:
                return Reply.of(json.dumps({"prototype": knobbed_exp()}))
            raise AssertionError("the model was asked to write RTL")

    prob = nlu_problem(ops=("exp",), ulp_budget=1, clock_period_ps=1250.0, seed=1, test_rounds=0)
    st = LoopState(request=LoopRequest(repair_attempts=1, prototype_attempts=2),
                   say=lambda _m: None, proposer=Proto(), feedback=None, workdir=str(tmp_path))
    cand, built, reason = _generate_with_model(prob, "exp", "table on the fraction of x*log2e", st, None)
    # D487: the plan's method reaches the prototype prompt
    assert any("YOUR PLAN for exp, from your planning step: table on the fraction of x*log2e" in p for p in seen)
    assert reason == "" and cand is not None and cand.name == "transpiled_exp"
    assert "T = 5\nM = 12\nCB = 8" in st.prototypes["exp"]
    # D482: every attempt is on disk -- prompt, reply, the code checked, the verdict
    traced = sorted(p.name for p in (tmp_path / "prototypes" / "exp" / "pass1").iterdir())
    assert traced == ["01.code.txt", "01.prompt.txt", "01.reply.txt", "01.verdict.txt"]
    assert (tmp_path / "prototypes" / "exp" / "pass1" / "01.verdict.txt").read_text().startswith("PASSES, score 0")


@pytest.mark.skipif(sys.platform != "linux", reason="the sandbox uses resource limits")
def test_the_prototype_stage_has_its_own_prefix_and_names_a_verilog_reply():
    """D491: the prototype prompt carried the DESIGN stage's static prefix -- the RTL
    interface and "reply with a SystemVerilog module" -- ahead of the prototype's own reply
    shape, and tanh answered the Python stage with Verilog in the `prototype` field. The
    prototype stage has its own prefix (sheet, library, the prototype contract), and a
    Verilog reply is named as such, not "does not parse"."""
    from flux_loop import LoopRequest, LoopState
    from nlu_fixtures import nlu_problem

    prob = nlu_problem(ops=("tanh",), ulp_budget=1, clock_period_ps=1250.0, seed=1, test_rounds=0)
    st = LoopState(request=LoopRequest(), say=lambda _m: None, proposer=None, feedback=None)
    design_prefix, proto_prefix = prob.prompt_prefix("tanh", st), prefix_for(prob, prob.prototype(), "tanh", st)
    assert "module nlu_tanh (input wire clk" in design_prefix and "SystemVerilog module" in design_prefix
    assert "METHODS, most promising first" in proto_prefix
    assert "THE STAGE: a PYTHON PROTOTYPE of `tanh`" in proto_prefix
    assert "input wire clk" not in proto_prefix and '"source"' not in proto_prefix
    v = prob.prototype_check("module nlu_tanh (input wire clk, input wire [15:0] x, output wire [15:0] y);\n"
                             "  logic [15:0] abs_x;\n  assign abs_x = x & 16'h7fff;\n  assign y = abs_x;\nendmodule\n",
                             "tanh", st)
    assert v.score == float("inf") and v.why.startswith("this is RTL -- the prototype stage wants PYTHON")


@pytest.mark.skipif(sys.platform != "linux", reason="the sandbox uses resource limits")
def test_an_admitted_transpiled_design_is_respelled_by_todays_transpiler_at_reload(tmp_path):
    """D495: sigmoid's admitted RTL carried `~2'(v)`, which Verilator read as meant and yosys
    as a cast of size ~2 -- the one operator of seven that failed the synthesis screen, and
    a transpiler fix could not reach a design admitted before it. The prototype is the
    design and the RTL is derived: at reload a transpiled design is re-spelled from its
    verified prototype by today's transpiler, judged, and kept when it passes."""
    from flux_loop import LoopRequest, LoopState, Candidate, Verdict, _reload
    from nlu_fixtures import nlu_problem
    from flux_records import Records
    from test_nlu_vectorize import SCALAR_EXP

    db = str(tmp_path / "r.db")
    rec = Records(db, objective={"score": 1})
    prob = nlu_problem(ops=("exp",), ulp_budget=1, clock_period_ps=1250.0, seed=1, test_rounds=0)
    st = LoopState(request=LoopRequest(db=db), say=lambda _m: None, proposer=None, feedback=None,
                   workdir=str(tmp_path), records=rec)
    v = prob.prototype_check(SCALAR_EXP, "exp", st)
    assert v.ok
    fresh = prob.transpile(v.payload["prototype"], "exp", st)
    stale = Candidate(fresh.name, "module nlu_exp (input logic clk, input logic [15:0] x, output logic [15:0] y);\n"
                      "  assign y = x;\nendmodule\n", knobs=dict(fresh.knobs), meta=dict(fresh.meta), subgoal="exp")
    rec.trial({"name": "prototype:exp", "artifact": v.payload["prototype"], "knobs": {}, "meta": {"kind": "prototype"},
               "subgoal": "exp", "score": 0.0, "why": ""}, "exp:prototype", stage="prototype", strategy="loop",
              metrics={"score": 0.0}, error=None, analytic=True, evaluator="prototype@python")
    doc = stale.to_record(); doc["subgoal"] = "exp"
    rec.trial(doc, "exp:transpiled_exp", stage="admit", strategy="loop", metrics={"score": 0.0}, error=None)
    rec.close("paused")
    said: list[str] = []
    rec2 = Records(db, objective={"score": 1})
    st2 = LoopState(request=LoopRequest(db=db), say=said.append, proposer=None, feedback=None,
                    workdir=str(tmp_path), records=rec2)
    _reload(prob, st2)
    assert "exp" in st2.admitted and st2.admitted["exp"].artifact == fresh.artifact     # today's spelling, not `assign y = x`
    assert any("re-verified, kept frozen (re-spelled by today's transpiler)" in m for m in said), said
    assert st2.prototypes["exp"] == v.payload["prototype"]                 # D496: the design behind the RTL
    # D500: the part's history from the record reaches the prompt, diagnosis first
    rec2.trial({"name": "prototype:exp", "artifact": "x", "knobs": {}, "meta": {"kind": "prototype"}, "subgoal": "exp",
                "score": 512.0, "why": "pattern: 512 failures are negative inputs -- a sign-dependent step\nmore"},
               "exp:prototype", stage="prototype", strategy="loop", metrics={"score": 512.0}, error="x", analytic=True, evaluator="prototype@python")
    hist = history(st2, "exp")
    assert hist.startswith("HISTORY of this part on record") and "  512 over: pattern: 512 failures are negative inputs" in hist
    assert "512 over: pattern: 512 failures" in reminder_for(prob.prototype(), "exp", st2)
    from flux_loop.prototype import paper_excerpts
    lib = ("FROM THE OPERATOR'S LIBRARY (papers):\n  * [fpnew_fma.sv]   logic norm_shamt;\n"
           "  * [PACE.pdf] piecewise polynomial approximation with 16 segments reaches 1 ULP\n  * [InputIEEE.cpp] vhdl << tab;\n")
    kept = paper_excerpts(lib)
    assert "PACE.pdf" in kept and "fpnew_fma.sv" not in kept and "InputIEEE.cpp" not in kept
    # D496: a PIPELINED admit keeps its register count through the re-spelling
    piped = prob.transpile(v.payload["prototype"], "exp", st, pipeline=2)
    doc = piped.to_record(); doc["subgoal"] = "exp"
    rec2.trial(doc, "exp:transpiled_exp_p2", stage="admit", strategy="loop", metrics={"score": 0.0}, error=None)
    rec2.close("paused")
    rec3 = Records(db, objective={"score": 1})
    st3 = LoopState(request=LoopRequest(db=db), say=lambda _m: None, proposer=None, feedback=None,
                    workdir=str(tmp_path), records=rec3)
    _reload(prob, st3)
    assert st3.admitted["exp"].meta.get("pipeline") == 2 and "latency 2" in st3.admitted["exp"].artifact
    # D504: a LATER verified prototype of the part (a redesign's alternative that passed 0 over
    # but did not stand) is not the admitted design's -- the re-spelling follows the digest
    # the RTL carries, not the last verified row
    from flux_loop import prototype_digest

    other = v.payload["prototype"].replace("T = 4", "T = 5")            # a different design, different RTL
    assert prob.transpile(other, "exp", st).meta["prototype_sha"] == prototype_digest(other) != piped.meta["prototype_sha"]
    rec3.trial({"name": "prototype:exp", "artifact": other, "knobs": {}, "meta": {"kind": "prototype"},
                "subgoal": "exp", "score": 0.0, "why": "0 over; an alternative that did not stand"}, "exp:prototype",
               stage="prototype", strategy="loop", metrics={"score": 0.0}, error=None, analytic=True, evaluator="prototype@python")
    rec3.close("paused")
    rec4 = Records(db, objective={"score": 1})
    st4 = LoopState(request=LoopRequest(db=db), say=lambda _m: None, proposer=None, feedback=None,
                    workdir=str(tmp_path), records=rec4)
    _reload(prob, st4)
    assert st4.prototypes["exp"] == v.payload["prototype"] and st4.admitted["exp"].artifact == piped.artifact
    # D504: the BEST MEASURED admitted design stands at a reload, not the last admitted --
    # a later design (the alternative, admitted at 341 MHz alone) does not displace the
    # record's 466 MHz design for being later
    later = prob.transpile(other, "exp", st, pipeline=2)
    for c, f in ((piped, 466.0), (later, 341.0)):
        doc = c.to_record(); doc["subgoal"] = "exp"
        rec4.trial(doc, f"exp:{c.name}", stage="screen", strategy="loop", metrics={"fmax_mhz": f, "area_um2": 500.0}, error=None)
    doc = later.to_record(); doc["subgoal"] = "exp"
    rec4.trial(doc, "exp:transpiled_exp_p2", stage="admit", strategy="loop", metrics={"score": 0.0}, error=None)
    rec4.close("paused")
    said5: list[str] = []
    rec5 = Records(db, objective={"score": 1})
    st5 = LoopState(request=LoopRequest(db=db), say=said5.append, proposer=None, feedback=None,
                    workdir=str(tmp_path), records=rec5)
    _reload(prob, st5)
    assert st5.admitted["exp"].artifact == piped.artifact and st5.prototypes["exp"] == v.payload["prototype"]
    assert any("stands over the last admitted" in m and "the record's screen numbers" in m for m in said5)


def _swept(prob, st, op="exp"):
    """Say the full register ladder was tried on the part's design (D506): otherwise a part
    pipelined at 16 is swept again first, up to 32 registers."""
    st.ledger.note(Kind.SWEEP, op, hashlib.sha256(st.prototypes[op].encode()).hexdigest()[:16])


@pytest.mark.skipif(sys.platform != "linux", reason="the sandbox uses resource limits")
def test_the_push_for_fmax_routes_the_deepest_parts_back_with_their_depth():
    """D496 (Cedric: "once the base loop completed we want to push for better fmax"): a
    composition measured below the clock asked for sends its deepest parts back to the
    generator with the numbers; in optimise mode the prototype check keeps 0 over as the
    gate and scores the logic depth; a shallower prototype passes, a broken one is 1e6+over."""
    from flux_loop import LoopRequest, LoopState, Candidate, Scored
    from nlu_fixtures import nlu_problem
    from flux_nlu.transpile import logic_depth
    from flux_nlu.blocks import with_prelude
    from test_nlu_vectorize import SCALAR_EXP

    prob = nlu_problem(ops=("exp", "recip"), ulp_budget=1, clock_period_ps=1250.0, seed=1, test_rounds=0)
    st = LoopState(request=LoopRequest(), say=lambda _m: None, proposer=None, feedback=None)
    v = prob.prototype_check(SCALAR_EXP, "exp", st)
    assert v.ok and "logic depth ~" in v.why and "critical path" in v.why
    st.prototypes["exp"] = v.payload["prototype"]
    st.admitted["exp"] = Candidate("transpiled_exp", "module nlu_exp ...", subgoal="exp", meta={"transpiled": True})
    d = part_depth(prob, "exp", st)
    assert d and d["depth"] > 20 and d["path"][0][1] > 0
    alone = prob.ladder().alone or prob.stages()[-1]                 # the parts' stage (D522: confirm)
    composed = Scored(Candidate("composed[exp, recip]", "...", meta={"composed": ["exp", "recip"]}), stage="screen",
                      metrics={"fmax_mhz": 44.0, "area_um2": 5000.0})
    # D498: the route screens each admitted part on its own first (cached); the fake
    # measure below stands in for synthesis
    prob.measure = lambda cand, stage, state: {"fmax_mhz": 123.0, "area_um2": 45.0}   # type: ignore[method-assign]
    # D535: the screen is shallower than the parts' stage -- it orders, it never sends back
    assert prob.route("screen", [composed], st) == []
    composed = Scored(composed.candidate, stage=alone, metrics=composed.metrics)
    back = prob.route(alone, [composed], st)
    assert len(back) == 1 and back[0].subgoal == "exp" and "44 MHz" in back[0].why and "800 MHz" in back[0].why
    assert st.part("exp").alone["fmax_mhz"] == 123.0 and "(123 MHz alone)" in back[0].why
    assert "123 MHz alone, 45 um2" in prob.standing(st)["parts"]["exp"]
    assert f"~{d['depth']} levels" in back[0].why
    assert prob.good_enough(st) is None
    # D522: 800 MHz means ROUTED; on the screen stage the goal carries the document's 3% margin
    o1 = prob.objectives()[0]
    at_screen = o1.goal_at("screen", prob.stages())
    assert o1.stage == prob.stages()[-1] and o1.margin == 0.03 and at_screen == (824.0 if o1.stage != "screen" else 800.0)
    st.scored.append(Scored(Candidate("composed[exp, recip]", "...", meta={"composed": True}), stage="screen", metrics={"fmax_mhz": 812.0, "area_um2": 1.0}))
    assert prob.good_enough(st) is None       # D543: area and power remain to shrink; the clock is a floor
    st.scored.append(Scored(Candidate("composed[exp, recip]", "...", meta={"composed": True}), stage="screen", metrics={"fmax_mhz": 830.0, "area_um2": 1.0}))
    assert prob.good_enough(st) is None and prob.objectives().goal.meets(st.scored[-1].metrics, "screen", prob.stages())
    # D497: the objectives the results table shows -- the goal, now, the whole, each part
    obj = prob.standing(st)
    assert obj["goal"].startswith("an FP16 NLU of 2 operators (exp, recip): each within 1 ULP")
    assert "800 MHz" in obj["goal"] and obj["composed"].startswith(f"1 um2 · 830 MHz (goal {at_screen:.0f}") and "MEETS THE GOAL" in obj["composed"]
    assert obj["parts"]["exp"].startswith("y = e^x, 1 ULP · combinational") and f"depth {d['depth']}" in obj["parts"]["exp"]
    assert obj["parts"]["recip"] == "y = 1/x, 1 ULP"
    # D501: a rewrite is refused near a good seed (and in optimise mode), never in a redesign
    assert prefers_edits(prob.prototype(), "exp", st, 12.0) is not None and "send" in prefers_edits(prob.prototype(), "exp", st, 12.0)
    assert prefers_edits(prob.prototype(), "exp", st, 9000.0) is None and prefers_edits(prob.prototype(), "exp", st, float("inf")) is None
    st.part("exp").redesign = "note"
    assert prefers_edits(prob.prototype(), "exp", st, 12.0) is None
    st.part("exp").redesign = None
    # D501: the campaign's own shortest verified prototype is the example in a fresh pass
    st.prototypes["recip"] = "def design(x):\n    return x\n"
    spec = spec_prompt(prob, prob.prototype(), "exp", st)
    assert "A VERIFIED PROTOTYPE OF THIS CAMPAIGN (recip, 0 over on every input" in spec
    # optimise mode: the gate is 0 over, the score is the depth
    st.part("exp").optimise = {"depth": d["depth"], "target": d["depth"] - 1}
    assert prefers_edits(prob.prototype(), "exp", st, float(d["depth"])) is not None
    assert prob.standing(st)["now"].startswith("pushing fmax: exp to the model for its logic depth")
    v = prob.prototype_check(SCALAR_EXP, "exp", st)
    assert not v.ok and v.score == float(d["depth"]) and "the goal is <=" in v.why and "SHALLOWER" in v.why
    broken = SCALAR_EXP.replace("return pack_fp16(0, k, p, M)", "return pack_fp16(0, k + 1, p, M)")
    v = prob.prototype_check(broken, "exp", st)
    assert v.score > 1e6 and "OPTIMISING for depth: 0 over stays the gate" in v.why


@pytest.mark.skipif(sys.platform != "linux", reason="the sandbox uses resource limits")
def test_a_part_that_cannot_be_made_fast_is_redesigned_with_another_algorithm(tmp_path, monkeypatch):
    """D499 (Cedric: "the tool should also consider ... rewrite/change the RTL/Python as
    there are multiple algorithms and maybe the current one is a bad pick"): a part that
    has been pipelined and had a depth pass and is still slow gets a fresh prototype pass
    asking for a DIFFERENT algorithm, under the same 0-over gate; what passes is pipelined
    and measured, and the better design stands."""
    import json

    from flux_loop import Improve, LoopRequest, LoopState, Candidate
    from nlu_fixtures import nlu_problem
    from test_nlu_vectorize import SCALAR_EXP

    prompts: list[str] = []

    class Other:                                     # the model: a (different) 0-over exp
        def propose(self, prompt, *, schema=None, tools=None, budget=None):
            prompts.append(prompt)
            return Reply.of(json.dumps({"prototype": SCALAR_EXP.replace("T = 5", "T = 4"), "why": "another split"}))

    prob = nlu_problem(ops=("exp",), ulp_budget=1, clock_period_ps=1250.0, seed=1, test_rounds=0)
    fmax_of = {0: 100.0, 2: 500.0, 4: 900.0, 8: 950.0, 16: 990.0}
    prob.measure = lambda cand, stage, state: {"fmax_mhz": fmax_of[int(cand.meta.get("pipeline", 0))],   # type: ignore[method-assign]
                                               "area_um2": 100.0 + 10 * int(cand.meta.get("pipeline", 0))}
    from flux_records import Records

    rec = Records(str(tmp_path / "r.db"), objective={"score": 1})
    st = LoopState(request=LoopRequest(prototype_attempts=2), say=lambda _m: None, proposer=Other(),
                   feedback=None, workdir=str(tmp_path), records=rec)
    v = prob.prototype_check(SCALAR_EXP, "exp", st)
    st.prototypes["exp"] = v.payload["prototype"]
    old = prob.transpile(st.prototypes["exp"], "exp", st, pipeline=16)     # pipelined, still "slow"
    st.admitted["exp"] = old
    _swept(prob, st)
    st.part("exp").alone = {"fmax_mhz": 100.0, "area_um2": 260.0}
    import hashlib
    st.ledger.note(Kind.DEPTH_PASS, "exp", hashlib.sha256(st.prototypes["exp"].encode()).hexdigest()[:16])   # D503: on record
    cand, built, reason = prob.improve(Improve(old, why="too slow", stage="screen", subgoal="exp"), st)
    assert reason == "" and cand is not None and cand.name.startswith("transpiled_exp_p")
    assert "ALTERNATIVE ALGORITHM (the 1st asked for)" in prompts[0] and "Do NOT refine it" in prompts[0]
    assert "T = 4" in st.prototypes["exp"] and int(cand.meta["pipeline"]) == 4     # the least registers at the goal
    assert st.part("exp").redesigns == 1 and not st.part("exp").redesign
    # a redesign that ends short of 0 over leaves its best refused attempt on record as the
    # seed of the next redesign pass (D499)
    from flux_records import Records

    class Short:                                    # a model whose alternative stays 1 short
        def propose(self, prompt, *, schema=None, tools=None, budget=None):
            return Reply.of(json.dumps({"prototype": "def design(x):\n    return x\n"}))

    rec = Records(str(tmp_path / "r.db"), objective={"score": 1})
    prob2 = nlu_problem(ops=("exp",), ulp_budget=1, clock_period_ps=1250.0, seed=1, test_rounds=0)

    def never_passes(code, subgoal, state):        # the problem's own check (D516), on the instance
        from flux_loop import Verdict
        return Verdict(False, 7.0, "7 short")

    prob2.prototype_check = never_passes
    st2 = LoopState(request=LoopRequest(db=str(tmp_path / "r.db"), prototype_attempts=2), say=lambda _m: None,
                    proposer=Short(), feedback=None, workdir=str(tmp_path), records=rec)
    st2.prototypes["exp"] = v.payload["prototype"]; st2.admitted["exp"] = old
    _swept(prob2, st2)
    st2.part("exp").alone = {"fmax_mhz": 100.0}
    monkeypatch.setattr(ladder, "part_depth", lambda problem, op, state: {"depth": 150, "path": []})
    cand, built, reason = prob2.improve(Improve(old, why="too slow", stage="screen", subgoal="exp"), st2)
    assert cand is None and st2.prototypes["exp"] == v.payload["prototype"]  # the record's design stands
    assert ladder.redesign_seed(st2, "exp") == (7.0, "def design(x):\n    return x\n", "7 short")


@pytest.mark.skipif(sys.platform != "linux", reason="the sandbox uses resource limits")
def test_a_depth_pass_that_stops_short_of_its_goal_still_takes_a_shallower_design(tmp_path):
    """D504: the depth goal is where the pass may stop, not the price of admission -- a
    0-over design shallower than the seed that misses the fifth asked for is transpiled and
    handed to the gate anyway (tanh's pass found 177 levels from 196 and the 196 stood)."""
    import json

    from flux_loop import Improve, LoopRequest, LoopState, Verdict
    from nlu_fixtures import nlu_problem
    from test_nlu_vectorize import SCALAR_EXP

    said: list[str] = []

    class Model:                                     # one edit that keeps 0 over (and is outside the family's knobs)
        def propose(self, prompt, *, schema=None, tools=None, budget=None):
            return Reply.of(json.dumps({"edits": [{"find": "return 15360", "replace": "return 15360 + 0"}], "why": "one level"}))

    def scripted_check(code, subgoal, state):            # the problem's OWN check replaces the world's (D516)
        v = base_check(code, subgoal, state)
        opt = state.part(subgoal).optimise
        if opt and v.score < 1e6 and "15360 + 0" in code:  # measurable, 0 over: one level short
            return Verdict(False, float(opt["depth"]) - 1, "one level shallower, short of the goal", v.payload)
        return v

    from flux_records import Records

    rec = Records(str(tmp_path / "r.db"), objective={"score": 1})
    prob = nlu_problem(ops=("exp",), ulp_budget=1, clock_period_ps=1250.0, seed=1, test_rounds=0)
    base_check = prob.prototype_check
    prob.prototype_check = scripted_check
    # the fake synthesis: the new design (its prototype says "+ 0") is faster at every count
    fmax_of = {0: 100.0, 2: 500.0, 4: 700.0, 8: 750.0, 16: 790.0}
    prob.measure = lambda cand, stage, state: {                                        # type: ignore[method-assign]
        "fmax_mhz": fmax_of[int(cand.meta.get("pipeline", 0))] * (1.1 if cand.meta.get("prototype_sha") != old_sha else 1.0),
        "area_um2": 100.0 + 10 * int(cand.meta.get("pipeline", 0))}
    st = LoopState(request=LoopRequest(prototype_attempts=2), say=said.append, proposer=Model(),
                   feedback=None, workdir=str(tmp_path), records=rec)
    v = prob.prototype_check(SCALAR_EXP, "exp", st)
    st.prototypes["exp"] = v.payload["prototype"]
    old = prob.transpile(st.prototypes["exp"], "exp", st, pipeline=16)     # pipelined, still slow
    old_sha = old.meta["prototype_sha"]
    st.admitted["exp"] = old
    _swept(prob, st)
    st.part("exp").alone = {"fmax_mhz": 790.0, "area_um2": 260.0}
    cand, built, reason = prob.improve(Improve(old, why="too slow", stage="screen", subgoal="exp"), st)
    # taken, SWEPT (8 registers reach the goal at 825 MHz) and measured better than the
    # record's 790: it stands
    assert reason == "" and cand is not None and cand.name == "transpiled_exp_p8" and cand.meta["prototype_sha"] != old_sha
    assert "15360 + 0" in st.prototypes["exp"]                                # the shallower design is the part now
    assert any("stopped short of" in m and "-- taking it" in m for m in said)
    assert any("against the record's 790" in m and "-- it stands" in m for m in said)
    assert "exp" not in st.proto_best and not st.part("exp").optimise
    # on record as the part's verified prototype (the reload re-spells the RTL from it, D495)
    ok = [t for t in rec.store.trials(rec.campaign_id, status="ok")
          if t.stage == "prototype" and (t.candidate or {}).get("subgoal") == "exp"]
    assert ok and "15360 + 0" in ok[-1].candidate["artifact"] and "logic depth 134 (from 135" in ok[-1].candidate["why"]


@pytest.mark.skipif(sys.platform != "linux", reason="the sandbox uses resource limits")
def test_a_shallower_design_already_on_record_is_taken_before_another_pass(tmp_path):
    """D504: tanh's depth passes found the same 177-level design four times over while the
    196 stood. The record's shallowest 0-over prototype below the admitted depth is
    re-checked and taken -- no model turn, no new pass -- and stands as the verified one."""
    from flux_loop import Improve, LoopRequest, LoopState
    from nlu_fixtures import nlu_problem
    from flux_records import Records
    from test_nlu_vectorize import SCALAR_EXP

    said: list[str] = []
    rec = Records(str(tmp_path / "r.db"), objective={"score": 1})
    prob = nlu_problem(ops=("exp",), ulp_budget=1, clock_period_ps=1250.0, seed=1, test_rounds=0)
    # the fake synthesis: the shallower design (by the proxy) is SLOWER when synthesised, like
    # log's 145-level design against its 163 (341 against 466 MHz)
    fmax_of = {0: 100.0, 2: 200.0, 4: 300.0, 8: 400.0, 16: 466.0}
    prob.measure = lambda cand, stage, state: {                                        # type: ignore[method-assign]
        "fmax_mhz": fmax_of[int(cand.meta.get("pipeline", 0))] * (0.75 if cand.meta.get("prototype_sha") != old_sha else 1.0),
        "area_um2": 500.0}
    st = LoopState(request=LoopRequest(prototype_attempts=2), say=said.append, proposer=None,
                   feedback=None, workdir=str(tmp_path), records=rec)
    v = prob.prototype_check(SCALAR_EXP, "exp", st)
    st.prototypes["exp"] = v.payload["prototype"]
    old = prob.transpile(st.prototypes["exp"], "exp", st, pipeline=16)
    old_sha = old.meta["prototype_sha"]
    st.admitted["exp"] = old
    _swept(prob, st)
    st.part("exp").alone = {"fmax_mhz": 466.0, "area_um2": 500.0}
    d0 = part_depth(prob, "exp", st)["depth"]
    shallower = st.prototypes["exp"].replace("return 15360", "return 15360 + 0")     # 0 over, "shallower" by its record
    rec.trial({"name": "prototype:exp", "artifact": shallower, "knobs": {}, "meta": {"kind": "prototype"},
               "subgoal": "exp", "score": float(d0 - 3), "why": f"0 over; logic depth ~{d0 - 3} levels on the critical path"},
              "exp:prototype", stage="prototype", strategy="loop", metrics={"score": float(d0 - 3)},
              error="refused", analytic=True, evaluator="prototype@python")
    rec.trial({"name": "prototype:exp", "artifact": "def design(x):\n    return x\n", "knobs": {}, "meta": {"kind": "prototype"},
               "subgoal": "exp", "score": 40.0, "why": "pattern: 40 of 65536 -- an over-count, not a depth"},
              "exp:prototype", stage="prototype", strategy="loop", metrics={"score": 40.0},
              error="refused", analytic=True, evaluator="prototype@python")
    cand, built, reason = prob.improve(Improve(old, why="too slow", stage="screen", subgoal="exp"), st)
    assert any(f"{d0 - 3}-level design that passes (the admitted one is {d0}) -- taking it" in m for m in said)   # no model was asked
    ok = [t for t in rec.store.trials(rec.campaign_id, status="ok") if t.stage == "prototype"]
    assert ok and "15360 + 0" in ok[-1].candidate["artifact"] and "taken from the record" in ok[-1].candidate["why"]
    # ... but SWEPT and synthesised it runs at 350 MHz against the record's 466: the record's
    # design stands, its prototype is the part's again, and the slower one is not taken twice
    assert reason == "" and cand is not None and cand.meta["prototype_sha"] == old_sha
    assert st.prototypes["exp"] == v.payload["prototype"] and st.part("exp").alone["fmax_mhz"] == 466.0
    assert any("against the record's 466" in m and "the record's stands" in m for m in said)
    assert st.ledger.count(Kind.NOT_FASTER, "exp", hashlib.sha256(shallower.encode()).hexdigest()[:16]) == 1
    # ... under the digest of the RECORD'S row as well as the bound text's (live: tanh's 177
    # was taken twice because only the bound text was noted)
    assert ladder.shallower_on_record(st, "exp", st.prototypes["exp"], d0) is None
    said.clear()
    st.ledger.note(Kind.DEPTH_PASS, "exp", hashlib.sha256(v.payload["prototype"].encode()).hexdigest()[:16])
    st.part("exp").redesigns = 2                                  # nothing else on the ladder is due
    cand2, _b, reason2 = prob.improve(Improve(old, why="too slow", stage="screen", subgoal="exp"), st)
    assert cand2 is None and "the design stands" in reason2 and not any("taking it" in m for m in said)
    # D505: the ladder as a menu -- the same steps the rules walk, with their lines
    opts = prob.improve_options(Improve(old, why="too slow", stage="screen", subgoal="exp"), st)
    assert [o.name for o in opts] == ["sweep", "take", "import", "depth", "contender", "redesign", "stand"]
    assert [o.due for o in opts] == [False] * 7                              # pipelined, nothing due
    assert "no shallower passing design is on record" in opts[1].why and "already taken" in opts[3].why
    assert "2 of 2 alternatives tried" in opts[5].why and "466 MHz alone" in opts[6].why


def test_an_alternative_that_stalls_twice_rests(tmp_path, monkeypatch):
    """D504/D505: sigmoid's alternative ended at 2 over three passes running (23:09, 01:52,
    03:50) -- each pass resumed it and spent 40 minutes reaching the same 2. A pass that ends
    where the last one did is noted on the record; after two such passes the redesign step is
    not due and the design stands, said in the ladder's lines."""
    from flux_loop import Improve, LoopRequest, LoopState, Candidate
    from nlu_fixtures import nlu_problem
    from flux_records import Records

    rec = Records(str(tmp_path / "r.db"), objective={"score": 1})
    prob = nlu_problem(ops=("exp",), ulp_budget=1, clock_period_ps=1250.0, seed=1, test_rounds=0)
    st = LoopState(request=LoopRequest(), say=lambda _m: None, proposer=None, feedback=None,
                   workdir=str(tmp_path), records=rec)
    st.prototypes["exp"] = "PIPELINE = 16\ndef design(x):\n    return x\n"
    st.admitted["exp"] = Candidate("transpiled_exp_p16", "module nlu_exp ...", subgoal="exp",
                                   meta={"transpiled": True, "pipeline": 16})
    _swept(prob, st)
    monkeypatch.setattr(ladder, "part_depth", lambda problem, op, state: {"depth": 150, "path": []})
    st.ledger.note(Kind.DEPTH_PASS, "exp", hashlib.sha256(st.prototypes["exp"].encode()).hexdigest()[:16])
    alt = (2.0, "def design(x):\n    return x + 1\n", "2 over")
    st.ledger.note(Kind.REDESIGN, "exp", hashlib.sha256(alt[1].encode()).hexdigest()[:16], score=float(alt[0]), artifact=alt[1], why=alt[2])
    item = Improve(st.admitted["exp"], why="too slow", stage="screen", subgoal="exp")
    opts = {o.name: o for o in prob.improve_options(item, st)}
    assert opts["redesign"].due and "the alternative on record stands at 2 over" in opts["redesign"].why
    digest = hashlib.sha256(alt[1].encode()).hexdigest()[:16]
    st.ledger.note(Kind.REDESIGN_STALLED, "exp", digest)
    opts = {o.name: o for o in prob.improve_options(item, st)}
    assert opts["redesign"].due and "1 pass(es) ended there without improving it" in opts["redesign"].why
    st.ledger.note(Kind.REDESIGN_STALLED, "exp", digest)
    opts = {o.name: o for o in prob.improve_options(item, st)}
    assert not opts["redesign"].due and "rests at 2 over after 2 passes" in opts["stand"].why
    cand, _b, reason = prob.improve(item, st)
    assert cand is None and "the design stands" in reason


def test_a_slower_alternative_is_a_contender_that_gets_its_own_depth_pass(tmp_path, monkeypatch):
    """D506 (Cedric: "alternate designs can be optimised further, pipelined and improved
    further"): a verified alternative that measured slower is a contender on record; the
    ladder gives it a depth pass of its own before asking for a third algorithm; the pass
    runs on the contender's prototype and the incumbent's stays the part's; a contender whose
    pass found nothing faster is not tried again."""
    from flux_loop import Improve, LoopRequest, LoopState, Candidate
    from nlu_fixtures import nlu_problem
    from flux_records import Records

    rec = Records(str(tmp_path / "r.db"), objective={"score": 1})
    prob = nlu_problem(ops=("exp",), ulp_budget=1, clock_period_ps=1250.0, seed=1, test_rounds=0)
    st = LoopState(request=LoopRequest(), say=lambda _m: None, proposer=None, feedback=None,
                   workdir=str(tmp_path), records=rec)
    incumbent = "PIPELINE = 16\ndef design(x):\n    return x\n"
    st.prototypes["exp"] = incumbent
    st.admitted["exp"] = Candidate("transpiled_exp_p16", "module nlu_exp ...", subgoal="exp",
                                   meta={"transpiled": True, "pipeline": 16})
    _swept(prob, st)
    st.part("exp").alone = {"fmax_mhz": 466.0}
    monkeypatch.setattr(ladder, "part_depth", lambda problem, op, state: {"depth": 150, "path": []})
    st.ledger.note(Kind.DEPTH_PASS, "exp", hashlib.sha256(incumbent.encode()).hexdigest()[:16])
    item = Improve(st.admitted["exp"], why="too slow", stage="screen", subgoal="exp")
    opts = {o.name: o for o in prob.improve_options(item, st)}
    assert not opts["contender"].due and "no verified alternative" in opts["contender"].why
    far = "PIPELINE = 16\ndef design(x):\n    return x + 2\n"
    st.ledger.note(Kind.CONTENDER, "exp", hashlib.sha256((far).encode()).hexdigest()[:16], artifact=far, value=200.0, fmax_mhz=200.0, area_um2=698.0, depth=300)     # 200 against 466, twice as deep: out of reach
    opts = {o.name: o for o in prob.improve_options(item, st)}
    assert not opts["contender"].due and "too far behind" in opts["contender"].why
    alt = "PIPELINE = 16\ndef design(x):\n    return x + 1\n"
    st.ledger.note(Kind.CONTENDER, "exp", hashlib.sha256((alt).encode()).hexdigest()[:16], artifact=alt, value=341.0, fmax_mhz=341.0, area_um2=698.0, depth=145)     # 73% of 466, and shallower: within reach
    opts = {o.name: o for o in prob.improve_options(item, st)}
    assert opts["contender"].due and "341 MHz alone (145 levels) against the standing 466 MHz" in opts["contender"].why
    # a row noted before D517 carries the metric by name and no `value` (live: gelu's) -- read, not a KeyError
    older = "PIPELINE = 16\ndef design(x):\n    return x + 3\n"
    st.ledger.note(Kind.CONTENDER, "exp", hashlib.sha256(older.encode()).hexdigest()[:16], artifact=older, fmax_mhz=350.0, area_um2=700.0, depth=140)
    opts = {o.name: o for o in prob.improve_options(item, st)}
    assert opts["contender"].due and "350 MHz alone (140 levels)" in opts["contender"].why
    st.ledger.note(Kind.NOT_FASTER, "exp", hashlib.sha256(older.encode()).hexdigest()[:16])   # out of the way again
    assert [o.name for o in prob.improve_options(item, st) if o.due] == ["contender", "redesign"]   # the rules: contender first
    seen: dict = {}

    def fake_depth(problem, ladder_, op, proto, admitted, state, why):
        seen["seed"] = proto; seen["part"] = state.prototypes.get(op)
        state.ledger.note(Kind.DEPTH_PASS, op, hashlib.sha256(proto.encode()).hexdigest()[:16])
        return None, None, "prototype did not reach the goal"

    monkeypatch.setattr(ladder, "step_depth", fake_depth)
    cand, _b, reason = prob.improve(item, st)
    assert cand is None and seen == {"seed": alt, "part": alt}                # the pass ran on the contender
    assert st.prototypes["exp"] == incumbent                                  # the incumbent's stays the part's
    opts = {o.name: o for o in prob.improve_options(item, st)}
    assert not opts["contender"].due and opts["redesign"].due                 # spent; a third algorithm next
    # at most two contender passes per incumbent (live: eight of log's alternatives in a day)
    for i in (3, 4):
        st.ledger.note(Kind.CONTENDER, "exp", hashlib.sha256((f"PIPELINE = 16\ndef design(x):\n    return x + {i}\n").encode()).hexdigest()[:16], artifact=f"PIPELINE = 16\ndef design(x):\n    return x + {i}\n", value=400.0, fmax_mhz=400.0, area_um2=600.0, depth=140)
    opts = {o.name: o for o in prob.improve_options(item, st)}
    assert opts["contender"].due
    prob.improve(item, st)
    opts = {o.name: o for o in prob.improve_options(item, st)}
    assert not opts["contender"].due and "2 contender passes were already spent" in opts["contender"].why
    # a redesign whose alternative passed but did not stand is spent too: two of those, it rests
    incumbent_digest = hashlib.sha256(incumbent.encode()).hexdigest()[:16]
    st.ledger.note(Kind.REDESIGN_STALLED, "exp", incumbent_digest)
    assert {o.name: o for o in prob.improve_options(item, st)}["redesign"].due
    st.ledger.note(Kind.REDESIGN_STALLED, "exp", incumbent_digest)
    opts = {o.name: o for o in prob.improve_options(item, st)}
    assert not opts["redesign"].due and not any(o.due for o in opts.values())


def test_a_hair_of_clock_does_not_buy_half_again_the_area():
    """D506, live: tanh's 233-level alternative at 511.6 MHz and 1,120 um2 displaced the
    134-level design at 507.3 MHz and 708 um2. One rule for every comparison: below the goal,
    faster by more than the tie band wins; within the band the smaller stands; at the goal
    the smaller of those that reach it."""
    from flux_loop import Candidate
    from nlu_fixtures import nlu_problem

    prob = nlu_problem(ops=("tanh",), ulp_budget=1, clock_period_ps=1250.0, seed=1, test_rounds=0)   # goal 800 MHz
    assert not _better(511.6, 1120.0, 507.3, 708.0)          # a tie in clock, bigger: no
    assert _better(507.3, 708.0, 511.6, 1120.0)              # a tie in clock, smaller: yes
    assert _better(560.0, 1120.0, 507.3, 708.0)              # clearly faster: yes, whatever the area
    assert not _better(400.0, 100.0, 507.3, 708.0)           # slower: no, however small
    assert _better(850.0, 900.0, 507.3, 708.0)               # reaches the goal: yes
    assert not _better(850.0, 900.0, 820.0, 700.0) and _better(850.0, 600.0, 820.0, 700.0)   # both at goal: smaller
    assert not _better(700.0, 100.0, 820.0, 700.0)           # the old reaches the goal, the new does not
    assert _better(1.0, None, None, None)                     # nothing measured to beat
    a = Candidate("a", "A", subgoal="tanh"); b = Candidate("b", "B", subgoal="tanh")
    rows = [(a, {"fmax_mhz": 507.3, "area_um2": 708.0}), (b, {"fmax_mhz": 511.6, "area_um2": 1120.0})]
    assert prob.prefer_admitted("tanh", rows, None) is a and prob.prefer_admitted("tanh", rows[::-1], None) is a
    assert prob.prefer_admitted("tanh", [(a, None), (b, {"fmax_mhz": 1.0})], None) is b   # unmeasured cannot win


def test_roundings_are_counted_along_a_path_not_across_branches():
    """D506: gelu's design rounded once on each branch of `if s == 0:` and was told it rounded
    twice -- the model spent a pass chasing a rounding that was not there. An if/else counts
    the larger branch; sequential roundings add; a helper the design calls counts."""
    from flux_nlu.world import World

    from nlu_fixtures import nlu_problem

    two_branches = ("def design(x):\n    if s == 0:\n        y = from_fixed(a, 24)\n    else:\n"
                    "        y = from_fixed(b, 24)\n        y = fp16_neg(y)\n    return y\n")
    assert World._roundings_on_a_path(two_branches) == 1
    in_series = "def design(x):\n    t = exp_fp16(x)\n    return from_fixed(to_fixed(t, 20, 3) >> 1, 20)\n"
    assert World._roundings_on_a_path(in_series) == 2
    helper = ("def step(v):\n    return from_fixed(v, 20)\n\ndef design(x):\n    a = step(x)\n"
              "    return fp16_mul(a, a)\n")
    assert World._roundings_on_a_path(helper) == 2
    assert World._roundings_on_a_path("def design(x:\n") >= 0            # unparseable: the text count
    rep = {"over_budget": 343, "max_ulp": 4}
    assert World._rounding_residue(two_branches, rep) == ""             # one rounding: not a floor
    assert "rounds to FP16 2 times" in World._rounding_residue(in_series, rep)


def test_a_depth_pass_that_never_ran_does_not_count_as_taken(tmp_path, monkeypatch):
    """D506: two depth passes ended at their first turn on a server fault (LocalAI 500) and
    the ladder moved on to a redesign as if the pass had been taken. A pass whose generation
    "did not run" voids its own note; the count is notes minus voids."""
    from flux_loop import Improve, LoopRequest, LoopState, Candidate
    from nlu_fixtures import nlu_problem
    from flux_records import Records

    rec = Records(str(tmp_path / "r.db"), objective={"score": 1})
    prob = nlu_problem(ops=("exp",), ulp_budget=1, clock_period_ps=1250.0, seed=1, test_rounds=0)
    st = LoopState(request=LoopRequest(), say=lambda _m: None, proposer=None, feedback=None,
                   workdir=str(tmp_path), records=rec)
    proto = "PIPELINE = 16\ndef design(x):\n    return x\n"
    st.prototypes["exp"] = proto
    st.admitted["exp"] = Candidate("transpiled_exp_p16", "module nlu_exp ...", subgoal="exp",
                                   meta={"transpiled": True, "pipeline": 16})
    _swept(prob, st)
    monkeypatch.setattr(ladder, "part_depth", lambda problem, op, state: {"depth": 150, "path": []})
    prob.generate = lambda op, method, state, why: (None, None, "prototype did not reach 0 over: prototype turn did not run (500)")  # type: ignore[method-assign]
    item = Improve(st.admitted["exp"], why="too slow", stage="screen", subgoal="exp")
    digest = hashlib.sha256(proto.encode()).hexdigest()[:16]
    cand, _b, reason = prob.improve(item, st)
    assert cand is None and "did not run" in reason
    assert st.ledger.count(Kind.DEPTH_PASS, "exp", digest) == 0        # noted, then voided
    assert {o.name: o for o in prob.improve_options(item, st)}["depth"].due     # still to be taken


@pytest.mark.skipif(sys.platform != "linux", reason="the sandbox uses resource limits")
def test_a_part_proved_in_a_sibling_campaign_is_imported_and_measured(tmp_path):
    """D506: the focused gelu campaign found a 716 MHz gelu the seven-operator campaign could
    not see -- two campaigns of the same study in one store. A verified design of the part in
    a sibling campaign is a step of the ladder: re-checked under today's rules, taken,
    swept and kept only when it measures better; imported once."""
    from flux_loop import Improve, LoopRequest, LoopState
    from nlu_fixtures import nlu_problem
    from flux_records import Records
    from test_nlu_vectorize import SCALAR_EXP

    db = str(tmp_path / "r.db")
    # the SIBLING campaign: exp alone, its exp admitted with placed numbers
    sib_rec = Records(db, objective={"study": "nlu", "ops": ["exp"], "ulp_budget": 1, "clock_period_ps": 1250.0})
    prob = nlu_problem(ops=("exp",), ulp_budget=1, clock_period_ps=1250.0, seed=1, test_rounds=0)
    st0 = LoopState(request=LoopRequest(), say=lambda _m: None, proposer=None, feedback=None, workdir=str(tmp_path))
    v = prob.prototype_check(SCALAR_EXP, "exp", st0)
    sib_proto = v.payload["prototype"].replace("return 15360", "return 15360 + 0")   # a different text of exp
    sib_cand = prob.transpile(sib_proto, "exp", st0, pipeline=8)
    sib_rec.trial({"name": "prototype:exp", "artifact": sib_proto, "knobs": {}, "meta": {"kind": "prototype"},
                   "subgoal": "exp", "score": 0.0, "why": ""}, "exp:prototype", stage="prototype", strategy="loop",
                  metrics={"score": 0.0}, error=None, analytic=True, evaluator="prototype@python")
    doc = sib_cand.to_record(); doc["subgoal"] = "exp"
    sib_rec.trial(doc, "exp:admit", stage="admit", strategy="loop", metrics={"score": 0.0}, error=None)
    doc2 = sib_cand.to_record(); doc2["subgoal"] = None
    sib_rec.trial(doc2, "exp:confirm", stage="confirm", strategy="loop", metrics={"fmax_mhz": 716.0, "area_um2": 500.0}, error=None)
    sib_rec.close("paused")
    # THIS campaign: two operators, its own exp slower
    rec = Records(db, objective={"study": "nlu", "ops": ["exp", "recip"], "ulp_budget": 1, "clock_period_ps": 1250.0})
    prob2 = nlu_problem(ops=("exp", "recip"), ulp_budget=1, clock_period_ps=1250.0, seed=1, test_rounds=0)
    said: list[str] = []
    st = LoopState(request=LoopRequest(), say=said.append, proposer=None, feedback=None, workdir=str(tmp_path), records=rec)
    st.prototypes["exp"] = v.payload["prototype"]
    old = prob2.transpile(st.prototypes["exp"], "exp", st, pipeline=8)
    old_sha = old.meta["prototype_sha"]
    st.admitted["exp"] = old
    _swept(prob2, st)
    st.part("exp").alone = {"fmax_mhz": 466.0, "area_um2": 520.0}
    fmax_of = {0: 100.0, 2: 300.0, 4: 500.0, 8: 650.0, 16: 700.0, 24: 720.0, 32: 730.0}
    prob2.measure = lambda cand, stage, state: {"fmax_mhz": fmax_of[int(cand.meta.get("pipeline", 0))] * (1.0 if cand.meta.get("prototype_sha") != old_sha else 0.6),   # type: ignore[method-assign]
                                                "area_um2": 500.0}
    item = Improve(old, why="too slow", stage="screen", subgoal="exp")
    opts = {o.name: o for o in prob2.improve_options(item, st)}
    assert opts["import"].due and "proved a exp that runs at 716 MHz alone there" in opts["import"].why
    assert [o.name for o in prob2.improve_options(item, st) if o.due][0] == "import"   # before a model pass
    cand, _b, reason = prob2.improve(item, st)
    assert reason == "" and cand is not None and cand.meta["prototype_sha"] != old_sha       # taken and standing
    assert "15360 + 0" in st.prototypes["exp"] and any("-- it stands" in m for m in said)
    assert any("import exp: campaign" in m and "-- taking it" in m for m in said)
    opts = {o.name: o for o in prob2.improve_options(Improve(cand, why="still slow", stage="screen", subgoal="exp"), st)}
    assert not opts["import"].due                                                          # imported once
