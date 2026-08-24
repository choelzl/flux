"""D479: the family search -- knobs and a SPACE in the model's prototype, every member run
against all 65,536 inputs in one sandboxed pass, the cheapest member at 0 over selected by
the transpiler's measured widths and bound into the prototype the loop keeps."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

from flux_nlu.family import bind, configurations, describe_search, parse_family_output, space_of

sys.path.insert(0, str(Path(__file__).parent))
from test_nlu_transpile import prototype_of  # noqa: E402


def knobbed_exp() -> str:
    """The exp fixture with its configuration turned into knobs and a SPACE."""
    src, _cfg, _model = prototype_of("exp")
    fam = re.sub(r"class cfg:\n((?:    .*\n)+)", "", src)
    fam = fam.replace("def design(x):\n    return exp_model(x, cfg)",
                      "class cfg:\n    xf = XF\n    lb = LB\n    t = T\n    m = M\n    cb = CB\n"
                      "    LN2_BITS = 10\n    ff = XF + LB\n\ndef design(x):\n    return exp_model(x, cfg)")
    return ("XF = 12\nLB = 14\nT = 5\nM = 12\nCB = 8\n"
            "SPACE = {\"T\": [4, 5, 6], \"M\": [11, 12, 13], \"CB\": [0, 4, 8]}\n" + fam)


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
    from flux_nlu.problem import NluProblem

    prob = NluProblem(ops=("exp",), ulp_budget=1, clock_period_ps=1250.0, seed=1, test_rounds=0)
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
    from flux_nlu.problem import NluProblem

    prob = NluProblem(ops=("exp",), ulp_budget=1, clock_period_ps=1250.0, seed=1, test_rounds=0)
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
    from flux_nlu.problem import NluProblem

    prob = NluProblem(ops=("exp", "recip"), ulp_budget=1, clock_period_ps=1250.0, seed=1, test_rounds=0)
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
    from flux_nlu.problem import NluProblem

    seen: list[str] = []

    class Proto:
        def propose(self, prompt, schema=None):
            seen.append(prompt)
            if "PROTOTYPE FIRST" in prompt:
                return json.dumps({"prototype": knobbed_exp()})
            raise AssertionError("the model was asked to write RTL")

    prob = NluProblem(ops=("exp",), ulp_budget=1, clock_period_ps=1250.0, seed=1, test_rounds=0)
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
    from flux_nlu.problem import NluProblem

    prob = NluProblem(ops=("tanh",), ulp_budget=1, clock_period_ps=1250.0, seed=1, test_rounds=0)
    st = LoopState(request=LoopRequest(), say=lambda _m: None, proposer=None, feedback=None)
    design_prefix, proto_prefix = prob.prompt_prefix("tanh", st), prob.prototype_prefix("tanh", st)
    assert "module nlu_tanh (input wire clk" in design_prefix and "SystemVerilog module" in design_prefix
    assert "METHODS, most promising first" in proto_prefix
    assert "THE STAGE: a PYTHON PROTOTYPE of `tanh`" in proto_prefix
    assert "input wire clk" not in proto_prefix and '"source"' not in proto_prefix
    v = prob.prototype_check("module nlu_tanh (input wire clk, input wire [15:0] x, output wire [15:0] y);\n"
                             "  logic [15:0] abs_x;\n  assign abs_x = x & 16'h7fff;\n  assign y = abs_x;\nendmodule\n",
                             "tanh", st)
    assert v.score == float("inf") and v.why.startswith("this is SystemVerilog -- the prototype stage wants PYTHON")


@pytest.mark.skipif(sys.platform != "linux", reason="the sandbox uses resource limits")
def test_an_admitted_transpiled_design_is_respelled_by_todays_transpiler_at_reload(tmp_path):
    """D495: sigmoid's admitted RTL carried `~2'(v)`, which Verilator read as meant and yosys
    as a cast of size ~2 -- the one operator of seven that failed the synthesis screen, and
    a transpiler fix could not reach a design admitted before it. The prototype is the
    design and the RTL is derived: at reload a transpiled design is re-spelled from its
    verified prototype by today's transpiler, judged, and kept when it passes."""
    from flux_loop import LoopRequest, LoopState, Candidate, Verdict, _reload
    from flux_nlu.problem import NluProblem
    from flux_records import Records
    from test_nlu_vectorize import SCALAR_EXP

    db = str(tmp_path / "r.db")
    rec = Records(db, objective={"score": 1})
    prob = NluProblem(ops=("exp",), ulp_budget=1, clock_period_ps=1250.0, seed=1, test_rounds=0)
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
    hist = prob.prototype_history("exp", st2)
    assert hist.startswith("HISTORY of this part on record") and "  512 over: pattern: 512 failures are negative inputs" in hist
    assert "512 over: pattern: 512 failures" in prob.prototype_reminder("exp", st2)
    from flux_nlu.invent import prototype_library
    lib = ("FROM THE OPERATOR'S LIBRARY (papers):\n  * [fpnew_fma.sv]   logic norm_shamt;\n"
           "  * [PACE.pdf] piecewise polynomial approximation with 16 segments reaches 1 ULP\n  * [InputIEEE.cpp] vhdl << tab;\n")
    kept = prototype_library(lib)
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

    other = v.payload["prototype"].replace("return 15360", "return 15360 + 0")
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


@pytest.mark.skipif(sys.platform != "linux", reason="the sandbox uses resource limits")
def test_the_push_for_fmax_routes_the_deepest_parts_back_with_their_depth():
    """D496 (Cedric: "once the base loop completed we want to push for better fmax"): a
    composition measured below the clock asked for sends its deepest parts back to the
    generator with the numbers; in optimise mode the prototype check keeps 0 over as the
    gate and scores the logic depth; a shallower prototype passes, a broken one is 1e6+over."""
    from flux_loop import LoopRequest, LoopState, Candidate, Scored
    from flux_nlu.problem import NluProblem
    from flux_nlu.transpile import logic_depth
    from flux_nlu.blocks import with_prelude
    from test_nlu_vectorize import SCALAR_EXP

    prob = NluProblem(ops=("exp", "recip"), ulp_budget=1, clock_period_ps=1250.0, seed=1, test_rounds=0)
    st = LoopState(request=LoopRequest(), say=lambda _m: None, proposer=None, feedback=None)
    v = prob.prototype_check(SCALAR_EXP, "exp", st)
    assert v.ok and "logic depth ~" in v.why and "critical path" in v.why
    st.prototypes["exp"] = v.payload["prototype"]
    st.admitted["exp"] = Candidate("transpiled_exp", "module nlu_exp ...", subgoal="exp", meta={"transpiled": True})
    d = prob._part_depth("exp", st)
    assert d and d["depth"] > 20 and d["path"][0][1] > 0
    composed = Scored(Candidate("composed[exp, recip]", "..."), stage="screen",
                      metrics={"fmax_mhz": 44.0, "area_um2": 5000.0})
    # D498: the route screens each admitted part on its own first (cached); the fake
    # measure below stands in for synthesis
    prob.measure = lambda cand, stage, state: {"fmax_mhz": 123.0, "area_um2": 45.0}   # type: ignore[method-assign]
    back = prob.route("screen", [composed], st)
    assert len(back) == 1 and back[0].subgoal == "exp" and "44 MHz" in back[0].why and "800 MHz" in back[0].why
    assert st._fmax == {"exp": 123.0} and "(123 MHz alone)" in back[0].why
    assert "123 MHz alone, 45 um2" in prob.objectives(st)["parts"]["exp"]
    assert f"~{d['depth']} levels" in back[0].why
    assert prob.good_enough(st) is None
    st.scored.append(Scored(Candidate("composed[exp, recip]", "..."), stage="screen", metrics={"fmax_mhz": 812.0, "area_um2": 1.0}))
    assert prob.good_enough(st).startswith("the composition runs at 812 MHz")
    # D497: the objectives the results table shows -- the goal, now, the whole, each part
    obj = prob.objectives(st)
    assert obj["goal"].startswith("an FP16 NLU of 2 operators (exp, recip): each within 1 ULP")
    assert "800 MHz" in obj["goal"] and obj["composed"].startswith("1 um2 · 812 MHz (goal 800)") and "MEETS THE GOAL" in obj["composed"]
    assert obj["parts"]["exp"].startswith("y = e^x, 1 ULP · combinational") and f"depth {d['depth']}" in obj["parts"]["exp"]
    assert obj["parts"]["recip"] == "y = 1/x, 1 ULP"
    # D501: a rewrite is refused near a good seed (and in optimise mode), never in a redesign
    assert prob.prefers_edits("exp", st, 12.0, "x") is not None and "send" in prob.prefers_edits("exp", st, 12.0, "x")
    assert prob.prefers_edits("exp", st, 9000.0, "x") is None and prob.prefers_edits("exp", st, float("inf"), "x") is None
    st._redesign = {"exp": "note"}
    assert prob.prefers_edits("exp", st, 12.0, "x") is None
    st._redesign = {}
    # D501: the campaign's own shortest verified prototype is the example in a fresh pass
    st.prototypes["recip"] = "def design(x):\n    return x\n"
    spec, _schema = prob.prototype_spec("exp", st)
    assert "A VERIFIED PROTOTYPE OF THIS CAMPAIGN (recip, 0 over on every input" in spec
    # optimise mode: the gate is 0 over, the score is the depth
    st._optimise = {"exp": {"depth": d["depth"], "target": d["depth"] - 1}}
    assert prob.prefers_edits("exp", st, float(d["depth"]), "x") is not None
    assert prob.objectives(st)["now"].startswith("pushing fmax: exp to the model for its logic depth")
    v = prob.prototype_check(SCALAR_EXP, "exp", st)
    assert not v.ok and v.score == float(d["depth"]) and "the goal is <=" in v.why and "SHALLOWER" in v.why
    broken = SCALAR_EXP.replace("return pack_fp16(0, k, p, M)", "return pack_fp16(0, k + 1, p, M)")
    v = prob.prototype_check(broken, "exp", st)
    assert v.score > 1e6 and "OPTIMISING for depth: 0 over stays the gate" in v.why


@pytest.mark.skipif(sys.platform != "linux", reason="the sandbox uses resource limits")
def test_a_part_that_cannot_be_made_fast_is_redesigned_with_another_algorithm(tmp_path):
    """D499 (Cedric: "the tool should also consider ... rewrite/change the RTL/Python as
    there are multiple algorithms and maybe the current one is a bad pick"): a part that
    has been pipelined and had a depth pass and is still slow gets a fresh prototype pass
    asking for a DIFFERENT algorithm, under the same 0-over gate; what passes is pipelined
    and measured, and the better design stands."""
    import json

    from flux_loop import Improve, LoopRequest, LoopState, Candidate
    from flux_nlu.problem import NluProblem
    from test_nlu_vectorize import SCALAR_EXP

    prompts: list[str] = []

    class Other:                                     # the model: a (different) 0-over exp
        def propose(self, prompt, schema=None):
            prompts.append(prompt)
            return json.dumps({"prototype": SCALAR_EXP.replace("T = 5", "T = 4"), "why": "another split"})

    prob = NluProblem(ops=("exp",), ulp_budget=1, clock_period_ps=1250.0, seed=1, test_rounds=0)
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
    st._fmax, st._area = {"exp": 100.0}, {"exp": 260.0}
    import hashlib
    prob._note_pass(st, "depth_pass", "exp", hashlib.sha256(st.prototypes["exp"].encode()).hexdigest()[:16])   # D503: on record
    cand, built, reason = prob.improve(Improve(old, why="too slow", stage="screen", subgoal="exp"), st)
    assert reason == "" and cand is not None and cand.name.startswith("transpiled_exp_p")
    assert "ALTERNATIVE ALGORITHM (the 1st asked for)" in prompts[0] and "Do NOT refine it" in prompts[0]
    assert "T = 4" in st.prototypes["exp"] and int(cand.meta["pipeline"]) == 4     # the least registers at the goal
    assert st._redesigns == {"exp": 1} and "exp" not in (getattr(st, "_redesign", None) or {})
    # a redesign that ends short of 0 over leaves its best refused attempt on record as the
    # seed of the next redesign pass (D499)
    from flux_records import Records

    class Short:                                    # a model whose alternative stays 1 short
        def propose(self, prompt, schema=None):
            return json.dumps({"prototype": "def design(x):\n    return x\n"})

    class P(NluProblem):
        def prototype_check(self, code, subgoal, state):
            from flux_loop import Verdict
            return Verdict(False, 7.0, "7 short")   # never passes

    rec = Records(str(tmp_path / "r.db"), objective={"score": 1})
    prob2 = P(ops=("exp",), ulp_budget=1, clock_period_ps=1250.0, seed=1, test_rounds=0)
    st2 = LoopState(request=LoopRequest(db=str(tmp_path / "r.db"), prototype_attempts=2), say=lambda _m: None,
                    proposer=Short(), feedback=None, workdir=str(tmp_path), records=rec)
    st2.prototypes["exp"] = v.payload["prototype"]; st2.admitted["exp"] = old
    st2._fmax, st2._depth_passes = {"exp": 100.0}, {"exp": 1}
    prob2._part_depth = lambda op, state: {"depth": 150, "path": []}          # type: ignore[method-assign]
    cand, built, reason = prob2.improve(Improve(old, why="too slow", stage="screen", subgoal="exp"), st2)
    assert cand is None and st2.prototypes["exp"] == v.payload["prototype"]  # the record's design stands
    assert prob2._redesign_seed("exp", st2) == (7.0, "def design(x):\n    return x\n", "7 short")


@pytest.mark.skipif(sys.platform != "linux", reason="the sandbox uses resource limits")
def test_a_depth_pass_that_stops_short_of_its_goal_still_takes_a_shallower_design(tmp_path):
    """D504: the depth goal is where the pass may stop, not the price of admission -- a
    0-over design shallower than the seed that misses the fifth asked for is transpiled and
    handed to the gate anyway (tanh's pass found 177 levels from 196 and the 196 stood)."""
    import json

    from flux_loop import Improve, LoopRequest, LoopState, Verdict
    from flux_nlu.problem import NluProblem
    from test_nlu_vectorize import SCALAR_EXP

    said: list[str] = []

    class Model:                                     # one edit that keeps 0 over (and is outside the family's knobs)
        def propose(self, prompt, schema=None):
            return json.dumps({"edits": [{"find": "return 15360", "replace": "return 15360 + 0"}], "why": "one level"})

    class P(NluProblem):
        def prototype_check(self, code, subgoal, state):
            v = super().prototype_check(code, subgoal, state)
            opt = (getattr(state, "_optimise", None) or {}).get(subgoal)
            if opt and v.score < 1e6 and "15360 + 0" in code:  # measurable, 0 over: one level short
                return Verdict(False, float(opt["depth"]) - 1, "one level shallower, short of the goal", v.payload)
            return v

    from flux_records import Records

    rec = Records(str(tmp_path / "r.db"), objective={"score": 1})
    prob = P(ops=("exp",), ulp_budget=1, clock_period_ps=1250.0, seed=1, test_rounds=0)
    st = LoopState(request=LoopRequest(prototype_attempts=2), say=said.append, proposer=Model(),
                   feedback=None, workdir=str(tmp_path), records=rec)
    v = prob.prototype_check(SCALAR_EXP, "exp", st)
    st.prototypes["exp"] = v.payload["prototype"]
    old = prob.transpile(st.prototypes["exp"], "exp", st, pipeline=16)     # pipelined, still slow
    st.admitted["exp"] = old
    cand, built, reason = prob.improve(Improve(old, why="too slow", stage="screen", subgoal="exp"), st)
    assert reason == "" and cand is not None and cand.name == "transpiled_exp_p16"    # at the part's register count
    assert "15360 + 0" in st.prototypes["exp"]                                # the shallower design is the part now
    assert any("stopped short of" in m and "-- taking it" in m for m in said)
    assert "exp" not in st.proto_best and "exp" not in (getattr(st, "_optimise", None) or {})
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
    from flux_nlu.problem import NluProblem
    from flux_records import Records
    from test_nlu_vectorize import SCALAR_EXP

    said: list[str] = []
    rec = Records(str(tmp_path / "r.db"), objective={"score": 1})
    prob = NluProblem(ops=("exp",), ulp_budget=1, clock_period_ps=1250.0, seed=1, test_rounds=0)
    st = LoopState(request=LoopRequest(prototype_attempts=2), say=said.append, proposer=None,
                   feedback=None, workdir=str(tmp_path), records=rec)
    v = prob.prototype_check(SCALAR_EXP, "exp", st)
    st.prototypes["exp"] = v.payload["prototype"]
    old = prob.transpile(st.prototypes["exp"], "exp", st, pipeline=16)
    st.admitted["exp"] = old
    d0 = prob._part_depth("exp", st)["depth"]
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
    assert reason == "" and cand is not None and cand.name == "transpiled_exp_p16"        # no model was asked; the
    assert int(cand.meta["pipeline"]) == 16 and "15360 + 0" in st.prototypes["exp"]        # part's register count stays
    assert cand.meta["prototype_sha"] != old.meta["prototype_sha"]
    assert any(f"{d0 - 3}-level design at 0 over (the admitted one is {d0}) -- taking it" in m for m in said)
    ok = [t for t in rec.store.trials(rec.campaign_id, status="ok") if t.stage == "prototype"]
    assert ok and "15360 + 0" in ok[-1].candidate["artifact"] and "taken from the record" in ok[-1].candidate["why"]
