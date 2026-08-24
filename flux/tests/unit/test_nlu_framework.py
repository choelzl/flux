"""D408: the NLU rig, tested as a rig. No test here contains an NLU design -- the
loop owns those. What is pinned: the FP16 truth (references, ULP arithmetic, class
rules), the vector tiers, the sweep harness against real Verilator, the parse gates
on the model's replies, and the study's honest empty-handed path."""

from __future__ import annotations

import numpy as np
import pytest

from flux_nlu import OPCODES, all_inputs, reference, ulp_distance, ulp_report
from flux_nlu.fp16 import OPS


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


def test_parse_design_round_trips_and_refuses_structure_it_can_check():
    from flux_nlu.invent import parse_design

    cand, why = parse_design(_GOOD_REPLY, ops=("exp",))
    assert why is None and cand["style"] == "shared" and cand["latency"] == 0
    assert "module nlu" in cand["source"]
    assert parse_design("no header at all", ops=("exp",))[1] == "no DESIGN: header line"
    bad = _GOOD_REPLY.replace('"latency": 0', '"latency": 99')
    assert "outside" in parse_design(bad, ops=("exp",))[1]
    perop = _GOOD_REPLY.replace('"shared"', '"per-op"')
    assert "missing module" in parse_design(perop, ops=("exp",))[1]


def test_prompts_carry_the_contract_the_knowledge_and_the_guidance():
    from flux_nlu.invent import design_prompt, test_author_prompt
    from flux_nlu.knowledge import knowledge_text

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
    from flux_nlu import NluRequest, run_study
    from flux_nlu.verify import tools_missing

    if tools_missing():
        pytest.skip("verilator not on PATH")
    out = run_study(NluRequest(db="", ops=("exp",), llm_rounds=0, test_rounds=0),
                    log=lambda _m: None)
    assert out.decision is None
    assert any("no model was attached" in n for n in out.not_established)


def test_wrong_design_is_refused_by_exhaustion_and_the_record_teaches(tmp_path):
    """A scripted 'designer' hands in y=x as exp: the exhaustive gate must refuse it
    with the failing input attached, record it, and show it to the next run."""
    import shutil

    from flux_nlu import NluRequest, run_study
    from flux_nlu.flow import _record_context
    from flux_nlu.verify import tools_missing
    from flux_records import Records

    if tools_missing() or shutil.which("yosys") is None:
        pytest.skip("verilator/yosys not on PATH")
    db = str(tmp_path / "nlu.db")

    class Scripted:
        def propose(self, prompt: str) -> str:
            return _GOOD_REPLY

    req = NluRequest(db=db, ops=("exp",), llm_rounds=1, test_rounds=0,
                     repair_attempts=0, screen_only=True)
    out = run_study(req, proposer=Scripted(), log=lambda _m: None)
    assert out.decision is None
    assert any("ULP" in why for _n, why in out.refused)
    assert any("x=0x" in why for _n, why in out.refused)    # the counterexample
    r = Records(db, objective={"study": "nlu", "ops": ["exp"], "ulp_budget": 1,
                               "clock_period_ps": 1250.0})
    assert r.resumed and r.refusals(rung="gate")
    ctx = _record_context(r, ("exp",))
    assert "refused earlier" in ctx
