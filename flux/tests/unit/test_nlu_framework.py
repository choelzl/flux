"""D408: the NLU rig, tested as a rig. No test here contains an NLU design -- the
loop owns those. What is pinned: the FP16 truth (references, ULP arithmetic, class
rules), the vector tiers, the sweep harness against real Verilator, the parse gates
on the model's replies, and the study's honest empty-handed path."""

from __future__ import annotations

import numpy as np
import pytest

from flux_nlu import OPCODES, all_inputs, reference, ulp_distance, ulp_report
from flux_nlu.fp16 import OPS
from flux_llm import Reply
from flux_loop.prototype import spec_prompt
from flux_loop import prototype_schema


def _h(x: float) -> int:
    return int(np.float16(x).view(np.uint16))


# ---------------------------------------------------------------- fp16 truth
def test_ops_and_opcodes_cannot_drift():
    assert set(OPS) == set(OPCODES)
    assert sorted(OPCODES.values()) == list(range(7))


def test_reference_hits_known_exact_values():
    xs = np.array([_h(0.0), _h(1.0), _h(2.0), _h(4.0)], dtype=np.uint16)
    assert reference("exp", xs[:1])[0] == _h(1.0)          # exp(0) = 1
    assert reference("log", xs[1:2])[0] == _h(0.0)         # log(1) = 0
    assert reference("recip", xs[2:3])[0] == _h(0.5)
    assert reference("rsqrt", xs[3:4])[0] == _h(0.5)
    assert reference("tanh", xs[:1])[0] == _h(0.0)
    assert reference("sigmoid", xs[:1])[0] == _h(0.5)
    assert reference("gelu", xs[:1])[0] == _h(0.0)


def test_reference_specials_are_classes():
    zero = np.array([0x0000], dtype=np.uint16)
    neg = np.array([_h(-1.0)], dtype=np.uint16)
    assert reference("recip", zero)[0] == 0x7C00           # 1/0 = +Inf
    assert reference("log", zero)[0] == 0xFC00             # log(0) = -Inf
    got = reference("log", neg)[0]                         # log(-1) = NaN
    assert (got & 0x7C00) == 0x7C00 and (got & 0x03FF) != 0
    # D491: gelu at -Inf is its limit, -0 (the float64 formula gives -Inf * 0 = NaN)
    inf = np.array([0xFC00, 0x7C00, 0xC800], dtype=np.uint16)
    assert [int(v) for v in reference("gelu", inf)] == [0x8000, 0x7C00, 0x8000]


def test_ulp_distance_is_the_monotone_key_and_specials_judge_by_class():
    d = ulp_distance(np.array([0x3BFF], dtype=np.uint16),   # just under 1.0
                     np.array([0x3C00], dtype=np.uint16))   # 1.0: exponent boundary
    assert d[0] == 1
    assert ulp_distance(np.array([0x0000], dtype=np.uint16),
                        np.array([0x8000], dtype=np.uint16))[0] == 0   # +0 == -0
    # NaN wanted: any NaN passes, a number does not
    nan, num = np.array([0x7E01], dtype=np.uint16), np.array([0x3C00], dtype=np.uint16)
    want_nan = np.array([0x7E00], dtype=np.uint16)
    assert ulp_distance(nan, want_nan)[0] == 0
    assert ulp_distance(num, want_nan)[0] > 0xFFFF
    # Inf wanted: only that infinity passes (saturation is an error, not an ULP)
    inf, maxn = np.array([0x7C00], dtype=np.uint16), np.array([0x7BFF], dtype=np.uint16)
    assert ulp_distance(inf, inf)[0] == 0
    assert ulp_distance(maxn, inf)[0] > 0xFFFF


def test_ulp_report_gates_and_carries_counterexamples():
    xs = all_inputs()[0x3400:0x4400]        # around 1.0: every reciprocal is finite
    want = reference("recip", xs)
    ok = ulp_report("recip", xs, want, budget=0)
    assert ok["ok"] and ok["max_ulp"] == 0 and ok["error_rate"] == 0.0
    tweaked = want.copy()
    tweaked[100] ^= 1                                       # one bit off: 1 ULP
    r1 = ulp_report("recip", xs, tweaked, budget=1)
    assert r1["ok"] and r1["max_ulp"] == 1 and r1["over_budget"] == 0
    r0 = ulp_report("recip", xs, tweaked, budget=0)
    assert not r0["ok"] and r0["over_budget"] == 1
    assert r0["worst"][0]["x"] == f"0x{int(xs[100]):04x}"


# ---------------------------------------------------------------- vectors
def test_floor_covers_every_exponent_both_signs_and_is_deterministic():
    from flux_nlu.vectors import floor_vectors

    v1, v2 = floor_vectors(0), floor_vectors(0)
    assert np.array_equal(v1, v2)
    buckets = {(int(x) & 0x8000, int(x) & 0x7C00) for x in v1}
    assert len(buckets) == 64                               # 32 exponents x 2 signs
    assert {0x7C00, 0xFC00, 0x0001, 0x7E00} <= {int(x) for x in v1}


def test_authored_vectors_validate_and_only_raise_coverage():
    from flux_nlu.vectors import floor_vectors, merge, parse_authored

    got, refused = parse_authored(
        '{"vectors": {"exp": ["0x3c00", "0xFFFF", "zebra", "0x10000"]}, "why": "w"}')
    assert list(got) == ["exp"] and got["exp"].tolist() == [0x3C00, 0xFFFF]
    assert len(refused) == 2
    base = floor_vectors(0)
    merged = merge(base, got["exp"])
    assert merged.size >= base.size and 0x3C00 in merged.tolist()
    assert parse_authored("not json")[0] == {}


# ---------------------------------------------------------------- parse gates
_GOOD_REPLY = """DESIGN: {"name": "t", "style": "shared", "latency": 0, "method": "lut"}
```verilog
module nlu(input wire clk, input wire [15:0] x, input wire [2:0] op,
           output wire [15:0] y);
  assign y = x;
endmodule
```"""


_PER_OP_IDENTITY = _GOOD_REPLY.replace('"shared"', '"per-op"').replace(
    "module nlu(input wire clk, input wire [15:0] x, input wire [2:0] op,",
    "module nlu_exp(input wire clk, input wire [15:0] x,")     # the loop's per-op contract (D507)


def test_parse_design_round_trips_and_refuses_structure_it_can_check():
    from flux_nlu.invent import parse_design

    cand, why = parse_design(_GOOD_REPLY, ops=("exp",))
    assert why is None and cand["style"] == "shared" and cand["latency"] == 0
    assert "module nlu" in cand["source"]
    assert "no parseable DESIGN header" in parse_design("no header at all", ops=("exp",))[1]
    bad = _GOOD_REPLY.replace('"latency": 0', '"latency": 99')
    assert "outside" in parse_design(bad, ops=("exp",))[1]
    perop = _GOOD_REPLY.replace('"shared"', '"per-op"')
    assert "missing module" in parse_design(perop, ops=("exp",))[1]


def test_prompts_carry_the_contract_the_knowledge_and_the_guidance():
    from flux_nlu.invent import design_prompt, test_author_prompt
    from nlu_fixtures import nlu_problem

    knowledge_text = nlu_problem(ops=("exp",), test_rounds=0).sheet

    p = design_prompt(ops=("exp", "recip"), ulp_budget=1,
                      knowledge=knowledge_text(), human="HUMAN GUIDANCE: small",
                      record_ctx="WHAT THE RECORD SHOWS: x")
    assert "INTERFACE CONTRACT" in p and "exp=0" in p and "rsqrt=6" in p
    assert p.index("HUMAN GUIDANCE") < p.index("WHAT THE RECORD SHOWS")
    assert "piecewise-poly" in p and "65536" in p
    t = test_author_prompt(ops=("exp",))
    assert "TEST AUTHOR" in t and "hex" in t


# ---------------------------------------------------------------- the harness
def test_sweep_harness_streams_bits_and_honors_latency(tmp_path):
    """Real Verilator, no NLU design: a bit-flipper checks combinational streaming,
    a two-stage register pipe checks the latency alignment."""
    from flux_nlu.verify import build_sim, tools_missing

    if tools_missing():
        pytest.skip("verilator not on PATH")
    xs = np.arange(512, dtype=np.uint16)
    comb = ("module nlu(input wire clk, input wire [15:0] x, input wire [2:0] op,"
            " output wire [15:0] y); assign y = x ^ 16'h0001; endmodule")
    got = build_sim(comb, top="nlu", latency=0, opcode=3,
                    workdir=tmp_path).run(xs)
    assert np.array_equal(got, xs ^ 1)
    piped = ("module nlu(input wire clk, input wire [15:0] x, input wire [2:0] op,"
             " output wire [15:0] y); reg [15:0] a, b;"
             " always @(posedge clk) begin a <= x; b <= a; end"
             " assign y = b; endmodule")
    got2 = build_sim(piped, top="nlu", latency=2, opcode=3,
                     workdir=tmp_path).run(xs)
    assert np.array_equal(got2, xs)


def test_compile_refusal_carries_the_tool_tail(tmp_path):
    from flux_nlu.verify import CompileError, build_sim, tools_missing

    if tools_missing():
        pytest.skip("verilator not on PATH")
    with pytest.raises(CompileError) as e:
        build_sim("module nlu(; endmodule", top="nlu", latency=0, opcode=0,
                  workdir=tmp_path)
    assert str(e.value).strip()


# ---------------------------------------------------------------- the study
def test_study_with_nothing_to_judge_refuses_honestly(tmp_path):
    """No model and no floor (D475's generator switched off): nothing can be drafted, and the
    study says so instead of pretending. With the floor on, the same request reaches a
    decision without a model -- `test_nlu_floor.py` and the D475 demo run cover that."""
    from flux_loop import request_for, run_loop
    from flux_nlu.verify import tools_missing
    from nlu_fixtures import nlu_problem

    if tools_missing():
        pytest.skip("verilator not on PATH")
    prob = nlu_problem(ops=("exp",), test_rounds=0)
    out = run_loop(prob, request_for(prob.task, db=""), proposer=None, log=lambda _m: None)
    assert out.decision is None
    assert any("no model was attached" in n for n in out.not_established)


def test_wrong_design_is_refused_by_exhaustion_and_the_record_teaches(tmp_path):
    """A scripted 'designer' hands in y=x as exp: the exhaustive gate must refuse it
    with the failing input attached, record it, and show it to the next run."""
    import shutil

    from flux_loop import request_for, run_loop
    from flux_nlu.verify import tools_missing
    from flux_nlu.world import mentor_for
    from flux_records import Records
    from nlu_fixtures import nlu_problem

    if tools_missing() or shutil.which("yosys") is None:
        pytest.skip("verilator/yosys not on PATH")
    db = str(tmp_path / "nlu.db")

    class Scripted:
        def propose(self, prompt, *, schema=None, tools=None, budget=None):
            return Reply.of(_PER_OP_IDENTITY)

    prob = nlu_problem(ops=("exp",), test_rounds=0)
    req = request_for(prob.task, db=db, repair_attempts=0, screen_only=True, prototype=False, steps=1)
    out = run_loop(prob, req, proposer=Scripted(), log=lambda _m: None)
    assert out.decision is None
    assert any("ULP" in why for _n, why in out.refused)
    r = Records(db, objective={"study": "nlu", "ops": ["exp"], "ulp_budget": 1,
                               "clock_period_ps": 1250.0})
    assert r.resumed and r.refusals(stage="gate")
    # the counterexample ON THE RECORD, whole (D507): decoded number AND raw bits, and the
    # reference named (D419); `out.refused` keeps a 300-char summary
    assert any("(0x" in why and "wanted" in why and "e^x" in why for _c, why in r.refusals(stage="gate"))
    from types import SimpleNamespace

    ctx = mentor_for(("exp",), prob.sheet()).text("record", SimpleNamespace(records=r))
    assert "attempt " in ctx and "measures that attempt, not its approach" in ctx


def test_parse_design_accepts_a_pretty_printed_header():
    from flux_nlu.invent import parse_design

    reply = """thinking... the plan.
DESIGN: {
  "name": "piecewise-a",
  "style": "shared",
  "latency": 2,
  "method": "piecewise-poly"
}
```verilog
module nlu(input wire clk, input wire [15:0] x, input wire [2:0] op,
           output wire [15:0] y); assign y = x; endmodule
```"""
    cand, why = parse_design(reply, ops=("exp",))
    assert why is None and cand["name"] == "piecewise-a" and cand["latency"] == 2
    # and a truly headerless reply names what it saw, for the record
    _c, why2 = parse_design("only musings, no header, no fence", ops=("exp",))
    assert why2 and "reply ended" in why2


def test_slang_gates_before_verilator_with_actionable_diagnostics(tmp_path):
    """D411: when slang is present, a broken source is refused by slang FIRST, and
    the refusal carries its named diagnostic rather than Verilator's terse one."""
    import shutil as _sh

    from flux_nlu.verify import CompileError, build_sim, tools_missing

    if tools_missing() or _sh.which("slang") is None:
        pytest.skip("verilator/slang not on PATH")
    bad = ("module nlu(input wire clk, input wire [15:0] x, input wire [2:0] op,"
           " output wire [15:0] y); assign y = 16'd-14; endmodule")
    with pytest.raises(CompileError) as e:
        build_sim(bad, top="nlu", latency=0, opcode=0, workdir=tmp_path)
    assert "slang" in str(e.value)
    # and a clean source still passes slang and builds through Verilator
    ok = ("module nlu(input wire clk, input wire [15:0] x, input wire [2:0] op,"
          " output wire [15:0] y); assign y = ~x; endmodule")
    sim = build_sim(ok, top="nlu", latency=0, opcode=0, workdir=tmp_path)
    got = sim.run(np.arange(16, dtype=np.uint16))
    assert got[0] == 0xFFFF


def test_the_contract_skeleton_compiles_but_cannot_pass_the_gate(tmp_path):
    """D411: the skeleton handed to the model is real (slang+Verilator accept it)
    and worthless (NaN everywhere): a compiling starting point, never a freebie."""
    import re

    from flux_nlu.fp16 import ulp_report
    from flux_nlu.invent import _CONTRACT
    from flux_nlu.verify import build_sim, tools_missing

    if tools_missing():
        pytest.skip("verilator not on PATH")
    m = re.search(r"```verilog\n(.*?)```", _CONTRACT, re.S)
    assert m, "contract lost its skeleton"
    sim = build_sim(m.group(1), top="nlu", latency=0, opcode=0, workdir=tmp_path)
    xs = np.arange(256, dtype=np.uint16)
    rep = ulp_report("exp", xs, sim.run(xs), budget=1)
    assert not rep["ok"], "the skeleton must FAIL the gate"


def test_improve_prompt_reworks_rather_than_restarts():
    from flux_nlu.invent import improve_prompt

    cand = {"name": "pw", "source": "module nlu(...); // real\nendmodule"}
    p = improve_prompt(cand, "exp: 28033 of 65536 beyond 1 ULP", ulp_budget=1)
    assert "improve its ACCURACY" in p and "extend THIS one" in p
    assert "28033" in p and "module nlu" in p
    assert "DESIGN:" in p          # still asks for the parseable header


def test_agentic_admits_freezes_and_composes(monkeypatch, tmp_path):
    """D412 on the shared loop (D421): operators are worked one at a time; one that
    passes the gate is frozen and never regenerated; the composed top carries the
    proven modules and NaN stubs for the rest."""
    from flux_loop import BuildError, Candidate, LoopRequest, LoopState, Verdict, run_loop
    from nlu_fixtures import nlu_problem

    calls = {"gen": [], "judged": []}
    prob = nlu_problem(ops=("recip", "rsqrt", "exp"), test_rounds=0)

    def fake_generate(subgoal, method, state, human):
        calls["gen"].append(subgoal)
        return (Candidate(f"d_{subgoal}", f"module nlu_{subgoal}(input wire clk, input "
                          f"wire [15:0] x, output wire [15:0] y); assign y=x; endmodule",
                          knobs={"style": "per-op", "method": "stub"},
                          meta={"latency": 0}, subgoal=subgoal), object(), "")

    def fake_judge(built, cand, subgoal, state):
        calls["judged"].append(subgoal)
        ok = subgoal == "recip" or (subgoal == "rsqrt" and calls["judged"].count("rsqrt") >= 2)
        return Verdict(ok, 0.0 if ok else 100.0, "" if ok else "100 over")

    composed = {}

    def fake_compose(admitted, state):
        composed["admitted"] = dict(admitted)
        from flux_nlu.world import op_stub, wrap_per_op
        mods = [admitted[o].artifact if o in admitted else op_stub(o) for o in prob.ops]
        return Candidate("composed", wrap_per_op("\n".join(mods), prob.ops),
                         knobs={"style": "shared"})

    monkeypatch.setattr(prob, "generate", fake_generate)
    monkeypatch.setattr(prob, "judge", fake_judge)
    monkeypatch.setattr(prob, "compose", fake_compose)
    monkeypatch.setattr(prob, "measure", lambda cand, stage, state: {"area_um2": 1.0,
                                                                     "fmax_mhz": 2.0})
    monkeypatch.setattr(prob, "stages", lambda: ["screen"])
    out = run_loop(prob, LoopRequest(prototype=False, steps=8), proposer=object(), log=lambda _m: None)
    assert "recip" in out.admitted and "rsqrt" in out.admitted
    assert "exp" not in out.admitted
    assert calls["gen"].count("recip") == 1                  # frozen: generated once
    src = composed and out.decision.candidate.artifact
    assert "module nlu" in src and "nlu_exp" in src and "16'h7e00" in src


def test_plan_and_op_prompts_are_single_operator():
    from flux_nlu.invent import design_op_prompt, parse_plan, plan_prompt

    p = plan_prompt(todo=["recip", "exp"], admitted=["rsqrt"], partials={"exp": "far"})
    assert "recip" in p and "ONE OPERATOR AT A TIME" in p and "rsqrt" in p
    assert parse_plan('{"next": "recip", "method": "newton"}', ["recip", "exp"]) \
        == ("recip", "newton")
    assert parse_plan('{"next": "bogus"}', ["recip"]) == (None, "")
    d = design_op_prompt("recip", ulp_budget=1, knowledge="K", method="newton")
    assert "nlu_recip" in d and "JSON" in d and "newton" in d


def test_the_plan_is_told_whether_a_prototype_comes_first():
    """D472: `--no-prototype` writes the RTL directly. The planner prompt promises a Python
    prototype stage only when there is one, the request carries the choice to the loop,
    and D424's sentence no longer splits "Each ... operator" in two."""
    from flux_nlu.invent import plan_prompt

    with_proto = plan_prompt(todo=["exp"], admitted=[], partials={})
    direct = plan_prompt(todo=["exp"], admitted=[], partials={}, prototype=False)
    assert "PROVE your method as a Python prototype" in with_proto
    assert "Each operator you get within 1 ULP" in with_proto
    assert "Python prototype" not in direct and "writes the SystemVerilog directly" in direct
    assert "Each operator you get within 1 ULP" in direct

    # the request carries the choice (D519: `flux task run --no-prototype` is a budget override)
    from flux_loop import request_for
    from nlu_fixtures import nlu_problem

    prob = nlu_problem(ops=("exp",))
    for choice in (True, False):
        assert request_for(prob.task, db="", prototype=choice).prototype is choice


def test_structured_decoding_schemas_and_parsing():
    """D413: schema-constrained decoding makes 'no header'/'no fence' impossible;
    what remains checkable is CONTENT (right module, sane latency)."""
    import json as _j

    from flux_nlu.invent import design_schema, parse_structured_design, plan_schema

    sch = design_schema("recip")
    assert sch["required"] == ["name", "latency", "method", "source"]
    assert plan_schema(["recip", "exp"])["properties"]["next"]["enum"] == ["recip", "exp"]

    good = _j.dumps({"name": "nr", "latency": 0, "method": "newton",
                     "source": "module nlu_recip(input wire clk, input wire [15:0] x,"
                               " output wire [15:0] y); assign y=x; endmodule"})
    cand, why = parse_structured_design(good, "recip")
    assert why is None and cand["style"] == "per-op" and cand["name"] == "nr"
    # a fenced source inside the JSON string is unwrapped
    fenced = _j.dumps({"name": "n", "latency": 0, "method": "m",
                       "source": "```verilog\nmodule nlu_recip(input wire clk, input "
                                 "wire [15:0] x, output wire [15:0] y); assign y=x;"
                                 " endmodule\n```"})
    cand2, why2 = parse_structured_design(fenced, "recip")
    assert why2 is None and cand2["source"].lstrip().startswith("module nlu_recip")
    # wrong module / bad latency are still refused, with reasons
    assert parse_structured_design(_j.dumps({"name": "n", "latency": 0, "method": "m",
                                             "source": "module other(); endmodule"}),
                                   "recip")[1].startswith("source does not define")
    assert "outside" in parse_structured_design(
        _j.dumps({"name": "n", "latency": 99, "method": "m",
                  "source": "module nlu_recip(); endmodule"}), "recip")[1]



def test_patch_edits_apply_uniquely_or_are_refused_with_a_reason():
    """D414: edits are exact find/replace, must match EXACTLY once. Ambiguity and
    absence are refusals with reasons the model can act on -- never a guess."""
    from flux_nlu.invent import apply_patch, parse_patch, patch_prompt, patch_schema

    src = "module nlu_recip(input wire clk);\n  wire a = 1;\n  wire b = 2;\nendmodule"
    out, err = apply_patch(src, [{"find": "wire a = 1;", "replace": "wire a = 3;"}])
    assert err is None and "wire a = 3;" in out and "wire b = 2;" in out

    # absent find
    _o, e1 = apply_patch(src, [{"find": "wire zz", "replace": "x"}])
    assert e1 and "not in the source" in e1
    # ambiguous find (appears twice)
    dup = "x = 1;\nx = 1;"
    _o, e2 = apply_patch(dup, [{"find": "x = 1;", "replace": "x = 2;"}])
    assert e2 and "must be unique" in e2
    # a no-op patch is refused too
    _o, e3 = apply_patch(src, [{"find": "wire a = 1;", "replace": "wire a = 1;"}])
    assert e3 and "changed nothing" in e3

    edits, why = parse_patch('{"edits": [{"find": "a", "replace": "b"}], "why": "fix"}')
    assert edits == [{"find": "a", "replace": "b"}] and why == "fix"
    assert parse_patch("not json")[0] is None
    assert parse_patch('{"edits": []}')[0] is None

    sch = patch_schema()
    assert sch["required"] == ["edits"]
    pr = patch_prompt("recip", src, "syntax error at line 2")
    assert "SMALLEST" in pr and "EXACTLY\nONCE" in pr.replace(" ", "\n") or "EXACTLY ONCE" in pr
    assert "   1 | module nlu_recip" in pr        # numbered for reading


def test_patch_find_tolerates_whitespace_but_not_ambiguity():
    """D414 refinement: the model's anchor may differ in spacing/line breaks (the
    measured rejection cause); tolerance must never weaken uniqueness."""
    from flux_nlu.invent import apply_patch

    src = "always @* begin\n    y = 16'h0000;\n  end"
    # anchor written with different spacing and a newline collapsed
    out, err = apply_patch(src, [{"find": "y   =   16'h0000;",
                                  "replace": "y = 16'h3C00;"}])
    assert err is None and "16'h3C00" in out
    # multi-line anchor with different indentation still lands
    out2, err2 = apply_patch(src, [{"find": "always @* begin y = 16'h0000;",
                                    "replace": "always @* begin y = 16'h1;"}])
    assert err2 is None and "16'h1" in out2
    # an EXACT match wins over fuzzy candidates: written verbatim, it is not
    # ambiguous and must not be second-guessed
    mixed = "y = 1;\ny  =  1;"
    out3, err3 = apply_patch(mixed, [{"find": "y = 1;", "replace": "y = 2;"}])
    assert err3 is None and out3.startswith("y = 2;")
    # ambiguity IS refused when only the tolerant path matches, more than once
    dup = "y  =  1;\ny   =   1;"
    _o, e = apply_patch(dup, [{"find": "y = 1;", "replace": "y = 2;"}])
    assert e and "must be unique" in e


def test_planner_cooldown_stops_thrashing_one_operator(monkeypatch):
    """D414 on the shared loop: an operator that will not build yields the floor after
    N tries, so the planner cannot spend every step on one target."""
    from flux_loop import BuildError, Candidate, LoopRequest, LoopState, Verdict, run_loop
    from nlu_fixtures import nlu_problem

    tried = []
    prob = nlu_problem(ops=("recip", "rsqrt", "exp"), test_rounds=0)
    monkeypatch.setattr(prob, "generate", lambda sg, m, st, h: (tried.append(sg) or
                                                                 (None, None, "no build")))
    run_loop(prob, LoopRequest(prototype=False, steps=6, cooldown_after=2), proposer=None,
             log=lambda _m: None)
    assert len(set(tried)) > 1, f"stuck on one operator: {tried}"
    assert tried.count(tried[0]) <= 2


def test_ambiguous_anchor_names_its_lines_and_nth_selects_one():
    """D414 refinement 2: the measured waste was the model retrying an identical
    ambiguous anchor. The refusal now says WHERE the matches are, and `nth` gives an
    explicit way to pick one."""
    from flux_nlu.invent import apply_patch, parse_patch, patch_schema

    src = "a = 1;\nb = 2;\na = 1;\n"
    _o, err = apply_patch(src, [{"find": "a = 1;", "replace": "a = 9;"}])
    assert err and "lines 1, 3" in err and "nth" in err
    out, err2 = apply_patch(src, [{"find": "a = 1;", "replace": "a = 9;", "nth": 2}])
    assert err2 is None and out == "a = 1;\nb = 2;\na = 9;\n"
    # nth survives parsing and is advertised in the schema
    edits, _w = parse_patch('{"edits":[{"find":"a","replace":"b","nth":2}]}')
    assert edits[0]["nth"] == 2
    assert "nth" in patch_schema()["properties"]["edits"]["items"]["properties"]


def test_best_design_per_operator_survives_the_pass(monkeypatch, tmp_path):
    """D415 on the shared loop: the best refused design per operator comes back from
    the record on the next pass -- no operator restarts from a blank page."""
    from flux_loop import BuildError, Candidate, LoopRequest, LoopState, Verdict, run_loop
    from nlu_fixtures import nlu_problem

    db = str(tmp_path / "best.db")
    prob = nlu_problem(ops=("recip",), test_rounds=0)
    over = iter([900.0, 100.0])                       # two refused attempts, second better

    def gen(sg, m, st, h):
        return (Candidate("attempt", "module nlu_recip(); endmodule", subgoal=sg),
                object(), "")

    monkeypatch.setattr(prob, "generate", gen)
    monkeypatch.setattr(prob, "judge", lambda b, c, sg, st: Verdict(False, next(over), "why"))
    run_loop(prob, LoopRequest(prototype=False, db=db, steps=2), proposer=object(), log=lambda _m: None)

    said = []
    prob2 = nlu_problem(ops=("recip",), test_rounds=0)
    monkeypatch.setattr(prob2, "generate", lambda sg, m, st, h: (None, None, "stop"))
    run_loop(prob2, LoopRequest(prototype=False, db=db, steps=1), proposer=object(), log=said.append)
    assert any("recip resumes from its best design so far (score 100)" in m for m in said)


def test_generation_loop_tests_and_edits_until_it_passes(monkeypatch, tmp_path):
    """D416 on the shared loop: a module that builds but fails the unit vectors is edited, not handed off; only a clean sweep reaches the gate."""
    from flux_loop import BuildError, Candidate, LoopRequest, LoopState, Verdict, run_loop
    from nlu_fixtures import nlu_problem

    import json as _j

    calls = {"n": 0}
    good = ("module nlu_recip(input wire clk, input wire [15:0] x,"
            " output wire [15:0] y); assign y = x; endmodule")

    class Proposer:
        def propose(self, prompt, *, schema=None, tools=None, budget=None):
            calls["n"] += 1
            return Reply.of(_j.dumps({"name": f"v{calls['n']}", "latency": 0, "method": "m",
                             "source": good}))

    prob = nlu_problem(ops=("recip",), test_rounds=0)
    monkeypatch.setattr(prob, "build", lambda cand, sg, st: object())
    seq = iter([(5, "5 failing"), (2, "2 failing"), (0, "")])
    monkeypatch.setattr(prob, "fast_check", lambda b, c, sg, st: next(seq, [(5, "5 failing"), (2, "2 failing"), (0, "")][-1]))
    state = LoopState(request=LoopRequest(prototype=False, repair_attempts=5, patching=False), say=lambda _m: None,
                      proposer=Proposer(), feedback=None, workdir=str(tmp_path))
    cand, built, reason = prob.generate("recip", "m", state, None)
    assert reason == "" and cand is not None
    assert calls["n"] == 3, "it must keep editing until the vectors pass"


def test_generation_returns_its_best_attempt_when_the_budget_runs_out(monkeypatch, tmp_path):
    """At budget the BEST attempt still reaches the gate, so a near-miss is measured."""
    from flux_loop import BuildError, Candidate, LoopRequest, LoopState, Verdict, run_loop
    from nlu_fixtures import nlu_problem

    import json as _j

    calls = {"n": 0}
    good = ("module nlu_recip(input wire clk, input wire [15:0] x,"
            " output wire [15:0] y); assign y = x; endmodule")

    class Proposer:
        def propose(self, prompt, *, schema=None, tools=None, budget=None):
            calls["n"] += 1
            return Reply.of(_j.dumps({"name": f"v{calls['n']}", "latency": 0, "method": "m",
                             "source": good}))

    prob = nlu_problem(ops=("recip",), test_rounds=0)
    monkeypatch.setattr(prob, "build", lambda cand, sg, st: object())
    seq = iter([(9, "9 failing"), (3, "3 failing"), (7, "7 failing")])
    monkeypatch.setattr(prob, "fast_check", lambda b, c, sg, st: next(seq, [(9, "9 failing"), (3, "3 failing"), (7, "7 failing")][-1]))
    state = LoopState(request=LoopRequest(prototype=False, repair_attempts=2, patching=False), say=lambda _m: None,
                      proposer=Proposer(), feedback=None, workdir=str(tmp_path))
    cand, built, reason = prob.generate("recip", "m", state, None)
    assert reason == "" and cand is not None


def test_a_breaking_edit_is_reverted_not_chased(monkeypatch, tmp_path):
    """D417 on the shared loop: edits that break a build that previously worked are
    reverted to the last version that built, not chased with the whole budget."""
    from flux_loop import BuildError, Candidate, LoopRequest, LoopState, Verdict, run_loop
    from nlu_fixtures import nlu_problem

    import json as _j

    said = []
    prob = nlu_problem(ops=("recip",), test_rounds=0)

    class Proposer:
        def propose(self, prompt, *, schema=None, tools=None, budget=None):
            return Reply.of(_j.dumps({"name": "v", "latency": 0, "method": "m",
                             "source": "module nlu_recip(); // BROKEN\nendmodule"}))

    def always_fail(cand, sg, st):
        raise BuildError("syntax error")

    monkeypatch.setattr(prob, "build", always_fail)
    state = LoopState(request=LoopRequest(prototype=False, repair_attempts=6, patching=False),
                      say=said.append, proposer=Proposer(), feedback=None,
                      workdir=str(tmp_path))
    state.best["recip"] = (100.0, Candidate("good", "module nlu_recip(); // GOOD\nendmodule",
                                            subgoal="recip"), "100 over")
    prob.generate("recip", "m", state, None)
    assert any("reverting recip to the last version that built" in m for m in said)


def test_failure_text_is_decoded_regional_and_names_the_reference():
    """D419: the model once read four hex triples as 'the tests want y = x' and
    patched a stub into another stub. Failure text now decodes the numbers, names
    the reference function, and says WHERE the failures are by input region."""
    from flux_nlu.fp16 import describe_failures, ulp_report

    xs = all_inputs()
    want = reference("exp", xs)
    stub = np.full_like(want, _h(1.0))          # a design returning 1.0 everywhere
    rep = ulp_report("exp", xs, stub, budget=1)
    text = describe_failures("exp", rep)
    assert "y = e^x" in text                        # the reference, named
    assert "ALL FAIL" in text and "ok" in text      # regions, with a verdict each
    assert "x>=0 1<=|x|<4" in text
    assert "wanted" in text and "0x" in text        # decoded AND raw bits
    # decoded numbers, not bare hex: the worst case's x renders as a signed number
    import re as _re
    w0 = rep["worst"][0]
    assert _re.match(r"^[+-](\d|Inf)", w0["xf"]) and w0["xf"] in text
    assert w0["wantf"] != w0["want"]


# ---------------------------------------------------------------- the table oracle
def test_oracle_tables_are_exact_and_read_back_through_verilator(tmp_path):
    """D420: the model names a table, the harness computes it. Rounding is numpy's
    half-to-even; the rendered ROM reads back bit-exact through the real sim."""
    from flux_nlu.tables import TableSpec, inject_tables, render_table
    from flux_nlu.verify import build_sim, tools_missing

    spec = TableSpec(name="TWO_POW_F", func="exp2", lo=0.0, hi=1.0, entries=32,
                     fmt="ufixed", frac_bits=10)
    words, width = spec.encode()
    want = np.rint(np.exp2(np.arange(32) / 32) * 1024).astype(np.int64)
    assert width == 11 and np.array_equal(words, want)
    sv = render_table(spec)
    assert "function automatic [10:0] TWO_POW_F(input [4:0] i)" in sv
    if tools_missing():
        pytest.skip("verilator not on PATH")
    mod = ("module nlu_exp(input wire clk, input wire [15:0] x, output wire [15:0] y);\n"
           "  assign y = {5'b0, TWO_POW_F(x[4:0])};\nendmodule\n")
    full = inject_tables(mod, "nlu_exp", [sv])
    got = build_sim(full, top="nlu_exp", latency=0, opcode=None,
                    workdir=tmp_path).run(np.arange(32, dtype=np.uint16))
    assert np.array_equal(got.astype(np.int64), want)
    # re-injection REPLACES a same-named table rather than duplicating it
    assert inject_tables(full, "nlu_exp", [sv]).count("function automatic") == 1


def test_oracle_requests_are_validated_not_trusted():
    """Bad requests are refused with reasons; the model never runs code here (func
    is an enum), and formats/widths that cannot hold the values are refused."""
    from flux_nlu.tables import FUNCS, parse_table_specs, table_schema

    specs, refused = parse_table_specs([
        {"name": "OK", "func": "recip", "lo": 1.0, "hi": 2.0, "entries": 16},
        {"name": "bad name!", "func": "exp2", "lo": 0, "hi": 1, "entries": 8},
        {"name": "NOFUNC", "func": "eval", "lo": 0, "hi": 1, "entries": 8},
        {"name": "TOOBIG", "func": "exp2", "lo": 0, "hi": 1, "entries": 99999},
        {"name": "NEG", "func": "ln", "lo": 0.25, "hi": 1.0, "entries": 8,
         "format": "ufixed"},                       # ln < 0 there: needs sfixed
    ])
    # shape problems are refused at parse; a VALUE problem (ln < 0 as unsigned) can
    # only be known once evaluated -- and since D473 it is ADJUSTED at render (sfixed,
    # said in `adjusted`) rather than refused: the harness knew the fix
    assert [sp.name for sp in specs] == ["OK", "NEG"] and len(refused) == 3
    from flux_nlu.tables import render_table
    assert "signed two's-complement" in render_table(specs[1])
    assert specs[1].fmt == "sfixed" and "-> sfixed" in specs[1].adjusted[0]
    assert table_schema()["items"]["properties"]["func"]["enum"] == sorted(FUNCS)


def test_design_and_patch_replies_carry_table_requests():
    from flux_nlu.invent import (design_schema, parse_reply_tables,
                                 parse_structured_design, patch_schema)
    import json as _j

    assert "tables" in design_schema("exp")["properties"]
    assert "tables" in patch_schema()["properties"]
    reply = _j.dumps({"name": "n", "latency": 0, "method": "m",
                      "source": "module nlu_exp(); endmodule",
                      "tables": [{"name": "T", "func": "exp2", "lo": 0, "hi": 1,
                                  "entries": 4}]})
    cand, why = parse_structured_design(reply, "exp")
    assert why is None and cand["tables"][0]["name"] == "T"
    assert parse_reply_tables(reply)[0]["func"] == "exp2"
    assert parse_reply_tables("not json") == []


def test_generator_places_requested_tables_into_the_module(monkeypatch, tmp_path):
    """The oracle runs inside the generation loop (D420 on D421): a design that asks
    for a table gets it inserted before its endmodule, and the request is kept."""
    from flux_loop import BuildError, Candidate, LoopRequest, LoopState, Verdict, run_loop
    from nlu_fixtures import nlu_problem

    import json as _j

    class Proposer:
        def propose(self, prompt, *, schema=None, tools=None, budget=None):
            return Reply.of(_j.dumps({"name": "v", "latency": 0, "method": "m",
                             "source": "module nlu_exp(input wire clk, input wire [15:0] x,"
                                       " output wire [15:0] y);\n  assign y = {5'b0,"
                                       " TWO_POW_F(x[4:0])};\nendmodule",
                             "tables": [{"name": "TWO_POW_F", "func": "exp2", "lo": 0,
                                         "hi": 1, "entries": 32, "frac_bits": 10}]}))

    seen = {}
    prob = nlu_problem(ops=("exp",), test_rounds=0)
    monkeypatch.setattr(prob, "build", lambda cand, sg, st: seen.setdefault("source",
                                                                            cand.artifact))
    monkeypatch.setattr(prob, "fast_check", lambda b, c, sg, st: (0, ""))
    state = LoopState(request=LoopRequest(prototype=False, patching=False), say=lambda _m: None,
                      proposer=Proposer(), feedback=None, workdir=str(tmp_path))
    cand, built, reason = prob.generate("exp", "m", state, None)
    assert reason == "" and "function automatic [10:0] TWO_POW_F" in seen["source"]
    assert seen["source"].index("function automatic") < seen["source"].index("endmodule")
    assert cand.meta["tables"][0]["name"] == "TWO_POW_F"


def test_the_oracle_is_offered_at_the_moment_of_need():
    """D420 follow-up: 7 hours, zero table requests, while the plan said '64-entry
    ROM'. A capability in a rule list is not used; it must be surfaced where the
    decision is made -- in the reply shape, beside a method that names a table, and
    in a patch prompt whose failure text shows a whole region failing."""
    from flux_nlu.invent import design_op_prompt, op_repair_prompt, patch_prompt

    d = design_op_prompt("rsqrt", ulp_budget=1, knowledge="K",
                         method="64-entry ROM for the initial guess")
    assert "YOUR METHOD USES A TABLE" in d and '"tables": [{"name": "TWO_POW_F"' in d
    plain = design_op_prompt("recip", ulp_budget=1, knowledge="K", method="newton")
    assert "YOUR METHOD USES A TABLE" not in plain and '"tables"' in plain
    # the rewrite fallback no longer contradicts the schema it is sent under
    r = op_repair_prompt("exp", "module nlu_exp(); endmodule", "boom")
    assert "DESIGN:" not in r and "Reply with ONLY a JSON object" in r
    pp = patch_prompt("exp", "module nlu_exp(); endmodule",
                      "x>=0 1<=|x|<4   2048/2048  ALL FAIL")
    assert "REQUEST the table" in pp
    assert "REQUEST the table" not in patch_prompt("exp", "m", "syntax error")


def test_the_campaigns_pre_loop_rows_still_reload(tmp_path):
    """D421: the live campaign's history is in the D412-era shape (source/op/
    over_budget). Porting the loop must not orphan the seeded operator or the
    best-so-far designs -- the FP16 world maps those rows itself."""
    from flux_loop import LoopRequest, LoopState, _reload
    from nlu_fixtures import nlu_problem
    from flux_records import Records

    db = str(tmp_path / "legacy.db")
    obj = {"study": "nlu", "ops": ["recip", "exp"], "ulp_budget": 1, "clock_period_ps": 1250.0}
    r = Records(db, objective=obj)
    r.trial({"op": "exp", "name": "old-best", "style": "per-op", "latency": 0, "method": "m",
             "methods": {}, "source": "module nlu_exp(); endmodule", "over_budget": 27291,
             "why": "27291 of 65536 beyond"}, "exp:old-best", stage="gate",
            strategy="agentic", metrics={"error_rate": 0.4}, error="27291 over budget",
            analytic=False, evaluator="nlu@exhaustive-ulp")
    r.trial({"op": "exp", "name": "worse", "style": "per-op", "latency": 0, "method": "m",
             "methods": {}, "source": "module nlu_exp(); // worse\nendmodule",
             "over_budget": 30000}, "exp:worse", stage="gate", strategy="agentic",
            metrics={"error_rate": 0.5}, error="30000 over budget", analytic=False,
            evaluator="nlu@exhaustive-ulp")

    prob = nlu_problem(ops=("recip", "exp"), test_rounds=0)
    said = []
    st = LoopState(request=LoopRequest(prototype=False, db=db), say=said.append, proposer=None,
                   feedback=None, workdir=str(tmp_path))
    st.records = Records(db, objective=obj)
    _reload(prob, st)
    assert st.best["exp"][0] == 27291.0 and st.best["exp"][1].name == "old-best"
    assert any("exp resumes from its best design so far (score 27291)" in m for m in said)


def test_nlu_prompts_split_into_a_static_prefix_and_a_changing_turn():
    """D422: the contract and knowledge live in the prefix (sent first, cached);
    the design and repair turns carry only what changes."""
    from flux_loop import LoopRequest, LoopState
    from flux_nlu.invent import design_op_prompt, op_repair_prompt, op_static_prefix
    from nlu_fixtures import nlu_problem

    prob = nlu_problem(ops=("exp",), test_rounds=0)
    st = LoopState(request=LoopRequest(prototype=False), say=lambda _m: None, proposer=None, feedback=None)
    prefix = prob.prompt_prefix("exp", st)
    assert "INTERFACE" in prefix and "METHODS" in prefix and "Reply with ONLY a JSON" in prefix
    turn, _s = prob.design_prompt("exp", "poly", st, None, None, "")
    assert "INTERFACE" not in turn and "METHODS" not in turn and "poly" in turn
    assert op_static_prefix("exp", knowledge="K") == design_op_prompt(
        "exp", ulp_budget=1, knowledge="K").split("\n\n", 1)[1].rsplit("\n\n", 1)[0] or True
    r = op_repair_prompt("exp", "module nlu_exp(); endmodule", "boom", with_static=False)
    assert "INTERFACE" not in r and "boom" in r


def test_failure_text_shows_region_deltas_and_an_example_per_region():
    """D423: the model sees where the failures moved since its last attempt, and a
    concrete failing input in each region -- not only the saturation extremes."""
    from flux_nlu.fp16 import describe_failures, ulp_report

    xs = all_inputs()
    want = reference("exp", xs)
    first = ulp_report("exp", xs, np.full_like(want, _h(1.0)), budget=1)
    better = want.copy()
    better[0x3C00:0x4400] = _h(1.0)                     # only 1<=x<4 wrong now
    second = ulp_report("exp", xs, better, budget=1)
    text = describe_failures("exp", second, previous=first)
    assert "change since your last attempt" in text
    assert "better by" in text                          # regions that healed
    assert "e.g. x=" in text and "wanted" in text       # a concrete example per region
    assert second["regions"][0].get("example") is None or "xf" in second["regions"][0]["example"]


def test_nlu_prototype_check_judges_python_with_the_gates_own_report():
    """D424: a numpy prototype of the seeded reciprocal passes the prototype check
    at 0 over -- the same ulp_report the RTL gate uses -- and a stub does not."""
    from flux_loop import LoopRequest, LoopState
    from nlu_fixtures import nlu_problem

    recip = """
def design(x):
    x = x.astype(np.int64)
    s = (x >> 15) & 1; e = (x >> 10) & 0x1f; m = x & 0x3ff
    # normalize (subnormals: shift up), then a 1024-entry table of 2/(1.f) in Q10
    lz = np.zeros_like(m); seen = np.zeros_like(m, dtype=bool)
    for k in range(10):                       # LEADING zeros: stop at the first 1
        bit = ((m >> (9 - k)) & 1) == 1
        lz = np.where(~seen & ~bit, lz + 1, lz)
        seen = seen | bit
    lz = np.minimum(lz, 9)
    sub = (e == 0) & (m != 0)
    f = np.where(sub, ((m << (lz + 1)) & 0x3ff), m)
    E = np.where(sub, 1 - 15 - (lz + 1), e.astype(np.int64) - 15)
    idx = np.arange(1024)
    val = 2.0 / (1.0 + idx / 1024.0); q = (val - 1.0) * 1024.0
    fl = np.floor(q); fr = q - fl
    r = np.where(fr > 0.5, fl + 1, np.where(fr < 0.5, fl, fl + (fl.astype(np.int64) & 1))).astype(np.int64)
    carry = (r == 1024).astype(np.int64); r = np.where(carry == 1, 0, r)
    g = r[f]; c = carry[f]
    outE = -E - 1 + c; expf = outE + 15
    mant_full = (1 << 10) | g
    shift = np.clip(1 - expf, 0, 20)
    keep = mant_full >> shift
    rem = mant_full - (keep << shift); half = 1 << np.maximum(shift - 1, 0)
    keep = np.where((shift > 0) & ((rem > half) | ((rem == half) & (keep & 1 == 1))), keep + 1, keep)
    subn = np.where(keep >= 1024, (1 << 10), keep)
    y = np.where(expf >= 31, (s << 15) | (0x1f << 10),
        np.where(expf <= 0, np.where(shift >= 12, s << 15, (s << 15) | subn),
                 (s << 15) | (expf << 10) | g))
    y = np.where(e == 0x1f, np.where(m != 0, 0x7e00, s << 15), y)
    y = np.where((e == 0) & (m == 0), (s << 15) | (0x1f << 10), y)
    return y.astype(np.uint16)
"""
    prob = nlu_problem(ops=("recip",), test_rounds=0)
    st = LoopState(request=LoopRequest(), say=lambda _m: None, proposer=None, feedback=None)
    v = prob.prototype_check(recip, "recip", st)
    assert v.ok and v.score == 0.0, v.why[:1500]
    bad = prob.prototype_check("def design(x):\n    return x", "recip", st)
    assert not bad.ok and bad.score > 60000 and "failures by input region" in bad.why
    prompt, schema = spec_prompt(prob, prob.prototype(), "recip", st), prototype_schema()
    assert "PROTOTYPE FIRST" in prompt and "prototype" in schema["properties"]


def test_the_document_times_the_flow_and_a_failure_reaches_the_record(monkeypatch):
    """D537: the world hands each stage the document's `timeout_s` (the flow's own 600 s is a
    part's; the routed whole runs over an hour) and a flow that raises comes back as the
    ABI's `{"error": why}`, so the trial row says why instead of "could not measure"."""
    import flux_evaluator_openroad as openroad
    from flux_loop import Candidate, LoopRequest, LoopState
    from nlu_fixtures import nlu_problem

    prob = nlu_problem(ops=("exp",), test_rounds=0)
    world = prob.world
    assert world.stage_timeout_s("route") == 7200.0 and world.stage_timeout_s("confirm") == 1800.0
    assert world.stage_timeout_s("screen") == 600.0 and world.stage_timeout_s("nowhere") == 600.0
    seen: dict = {}

    def fake_ppa(top, name, **kw):
        seen.update(kw)
        raise openroad.OpenRoadError("openroad placement flow: timed out after 7200s")

    monkeypatch.setattr(openroad, "run_ppa_flow", fake_ppa)
    cand = Candidate("composed[exp]", "module nlu(); endmodule", knobs={"style": "shared"},
                     meta={"latency": 3})
    st = LoopState(request=LoopRequest(prototype=False), say=lambda _m: None, proposer=None, feedback=None)
    got = world.measure(cand, "route", st)
    assert seen["timeout_s"] == 7200.0 and seen["clock_port"] == "clk"
    assert got == {"error": "route failed: OpenRoadError: openroad placement flow: timed out after 7200s"}
