"""D473: what a script can do so the model does not spend a round on it -- `wire`/`reg` to
`logic` (continuous inits kept continuous, line numbers kept), the `clk` port the driver
needs, the oracle adjusting a table request instead of refusing it, a table remembered
per operator and re-placed when called, and a hand-written copy of a table replaced."""

from __future__ import annotations

import json

import pytest


def test_wire_and_reg_become_logic_and_continuous_inits_stay_continuous():
    from flux_nlu.hygiene import normalize_rtl

    src = """module nlu_exp (input wire clk, input wire [15:0] x, output wire [15:0] y);
  wire        sign = x[15];            // the sign
  wire [4:0]  ex = x[14:10], mant_hi = x[9:5];
  wire signed [17:0] prod;
  reg  [15:0] y_reg;
  reg  [3:0]  cnt = 4'd0;
  assign prod = $signed({1'b0, ex}) * 18'sd3;
  always @* begin
    y_reg = f(mant_hi);    // a wire in a comment stays a wire
  end
  assign y = y_reg;
endmodule
"""
    out, notes = normalize_rtl(src)
    lines = out.splitlines()
    assert lines[0] == "module nlu_exp (input logic clk, input logic [15:0] x, output logic [15:0] y);"
    assert lines[1] == "  logic sign; assign sign = x[15];            // the sign"
    assert lines[2] == "  logic [4:0] ex, mant_hi; assign ex = x[14:10]; assign mant_hi = x[9:5];"
    assert lines[3] == "  logic signed [17:0] prod;"
    assert lines[4] == "  logic  [15:0] y_reg;"
    assert lines[5] == "  logic  [3:0]  cnt = 4'd0;"                 # a reg init keeps its meaning
    assert lines[8] == "    y_reg = f(mant_hi);    // a wire in a comment stays a wire"
    assert len(lines) == len(src.splitlines())                     # slang's line numbers still match
    assert notes == ["6 wire + 2 reg declaration(s) -> logic, 3 continuous init(s) split into assign"]
    assert normalize_rtl(out) == (out, [])                          # idempotent


def test_the_clk_port_is_added_when_the_model_dropped_it():
    from flux_nlu.hygiene import ensure_clk_port

    one_line = "module nlu_rsqrt (input logic [15:0] x, output logic [15:0] y);\nendmodule\n"
    out, note = ensure_clk_port(one_line, "nlu_rsqrt")
    assert out.startswith("module nlu_rsqrt (input logic clk, input logic [15:0] x, output logic [15:0] y);")
    assert "clk port added" in note
    multi = "module nlu_rsqrt (\n  input logic [15:0] x,\n  output logic [15:0] y\n);\nendmodule\n"
    out, _ = ensure_clk_port(multi, "nlu_rsqrt")
    assert out.startswith("module nlu_rsqrt (\n  input logic clk,\n  input logic [15:0] x,")
    has = "module nlu_rsqrt (input logic clk, input logic [15:0] x, output logic [15:0] y);\n"
    assert ensure_clk_port(has, "nlu_rsqrt") == (has, "")
    other = "module helper (input logic a);\nendmodule\n" + has
    assert ensure_clk_port(other, "nlu_rsqrt")[0] == other        # only the named module


def test_the_oracle_adjusts_a_request_it_can_fix_and_says_so():
    from flux_nlu.tables import parse_table_specs, render_table

    specs, refused = parse_table_specs([
        {"name": "LOG2M", "func": "log2", "lo": 0.5, "hi": 1.0, "entries": 8,
         "format": "ufixed", "frac_bits": 8},                      # negative on [0.5, 1)
        {"name": "TWO_POW_F", "func": "exp2", "lo": 0, "hi": 1, "entries": 8,
         "format": "ufixed", "frac_bits": 10, "width": 8},         # needs 11 bits
    ])
    assert not refused
    rendered = [render_table(sp) for sp in specs]
    assert specs[0].fmt == "sfixed" and specs[0].adjusted == [
        "LOG2M: negative values on the domain -> sfixed (two's complement)"]
    assert "signed two's-complement" in rendered[0]
    assert specs[1].width == 11 and specs[1].adjusted == [
        "TWO_POW_F: width 8 cannot hold the values -> widened to 11"]
    assert "function automatic [10:0] TWO_POW_F" in rendered[1]


def test_a_hand_written_copy_of_a_table_is_replaced_by_the_oracles():
    from flux_nlu.tables import inject_tables, parse_table_specs, render_table

    src = ("module nlu_exp (input logic clk, input logic [15:0] x, output logic [15:0] y);\n"
           "  function automatic [10:0] TWO_POW_F(input [2:0] i);\n"
           "    TWO_POW_F = 11'd1024;   // the model's sketch\n"
           "  endfunction\n"
           "  assign y = TWO_POW_F(x[2:0]);\n"
           "endmodule\n")
    specs, _ = parse_table_specs([{"name": "TWO_POW_F", "func": "exp2", "lo": 0, "hi": 1,
                                   "entries": 8, "format": "ufixed", "frac_bits": 10}])
    out = inject_tables(src, "nlu_exp", [render_table(specs[0])])
    assert out.count("function automatic") == 1 and "the model's sketch" not in out
    assert "// TABLE TWO_POW_F" in out


def _problem_and_state(tmp_path):
    from flux_loop import LoopRequest, LoopState
    from flux_nlu.problem import NluProblem

    prob = NluProblem(ops=("exp",), ulp_budget=1, clock_period_ps=1250.0, seed=1)
    said = []
    st = LoopState(request=LoopRequest(), say=said.append, proposer=None, feedback=None,
                   workdir=str(tmp_path))
    return prob, st, said


def test_apply_tools_runs_the_hygiene_pass_and_remembers_tables(tmp_path):
    from flux_loop import Candidate

    prob, st, said = _problem_and_state(tmp_path)
    first = Candidate("nlu_exp", "module nlu_exp (input wire [15:0] x, output wire [15:0] y);\n"
                                 "  wire [2:0] idx = x[2:0];\n"
                                 "  assign y = TWO_POW_F(idx);\nendmodule\n", subgoal="exp")
    reply = json.dumps({"source": first.artifact, "tables": [
        {"name": "TWO_POW_F", "func": "exp2", "lo": 0, "hi": 1, "entries": 8,
         "format": "ufixed", "frac_bits": 10}]})
    cand, note = prob.apply_tools("exp", first, reply, st)
    assert note == ""
    assert "input logic clk, input logic [15:0] x" in cand.artifact.splitlines()[0]
    assert "  logic [2:0] idx; assign idx = x[2:0];" in cand.artifact
    assert "// TABLE TWO_POW_F" in cand.artifact
    assert "TWO_POW_F" in prob.known_tables["exp"]
    assert any(m.startswith("  hygiene: ") and "clk port added" in m for m in said)

    # a rewrite that calls the table without requesting it again gets it re-placed
    said.clear()
    rewrite = Candidate("nlu_exp", "module nlu_exp (input logic clk, input logic [15:0] x, "
                                   "output logic [15:0] y);\n"
                                   "  assign y = {5'd0, TWO_POW_F(x[2:0])};\nendmodule\n",
                        subgoal="exp")
    cand2, note2 = prob.apply_tools("exp", rewrite, json.dumps({"source": rewrite.artifact}), st)
    assert note2 == "" and "// TABLE TWO_POW_F" in cand2.artifact
    assert any("re-placed from an earlier attempt: TWO_POW_F" in m for m in said)

    # nothing to do -> the candidate comes back untouched
    clean = Candidate("nlu_exp", cand2.artifact, meta={"tables": cand2.meta["tables"]}, subgoal="exp")
    assert prob.apply_tools("exp", clean, json.dumps({"source": clean.artifact}), st)[0].artifact == clean.artifact


def test_an_adjusted_table_is_told_to_the_model(tmp_path):
    from flux_loop import Candidate

    prob, st, said = _problem_and_state(tmp_path)
    cand = Candidate("nlu_exp", "module nlu_exp (input logic clk, input logic [15:0] x, "
                                "output logic [15:0] y);\n  assign y = T(x[2:0]);\nendmodule\n",
                     subgoal="exp")
    reply = json.dumps({"source": cand.artifact, "tables": [
        {"name": "T", "func": "exp2", "lo": 0, "hi": 1, "entries": 8, "format": "ufixed",
         "frac_bits": 10, "width": 4}]})
    out, note = prob.apply_tools("exp", cand, reply, st)
    assert "// TABLE T" in out.artifact
    assert note.startswith("table requests ADJUSTED by the harness")
    assert "T: width 4 cannot hold the values -> widened to 11" in note


def test_declarations_inside_a_block_are_hoisted_to_its_header():
    from flux_nlu.hygiene import normalize_rtl

    src = """module nlu_sigmoid (input logic clk, input logic [15:0] x, output logic [15:0] y);
  logic [15:0] y_reg;
  always @* begin
    if (x[15]) begin
      wire [4:0] exp_bits = x[14:10];
      reg  [9:0] frac_bits;
      frac_bits = x[9:0];
      y_reg = {exp_bits, frac_bits, 1'b0};
    end else begin
      y_reg = x;
    end
  end
  function automatic [4:0] f(input [4:0] a);
    wire [4:0] t = a + 5'd1;
    f = t;
  endfunction
  assign y = y_reg;
endmodule
"""
    out, notes = normalize_rtl(src)
    lines = out.splitlines()
    assert lines[2] == "  logic [4:0] exp_bits; logic [9:0] frac_bits; always @* begin"
    assert lines[4] == "      exp_bits = x[14:10];"
    assert lines[5] == "      "                                          # the reg's line, now empty
    assert lines[12] == "  function automatic [4:0] f(input [4:0] a); logic [4:0] t;"
    assert lines[13] == "    t = a + 5'd1;"
    assert len(lines) == len(src.splitlines())
    assert "3 declared inside a block hoisted to its header" in notes[0]


def test_compile_errors_are_explained_in_the_words_a_fix_needs():
    from flux_nlu.hygiene import explain_compile_error

    text = ("slang (strict SystemVerilog) refused:\n"
            "dut.sv:12:5: error: reference to non-constant variable 'is_nan' is not allowed in a constant expression\n"
            "dut.sv:13:7: error: expected a declaration name\n"
            "dut.sv:40:9: error: use of undeclared identifier 'TWO_POW_F'\n"
            "dut.sv:41:9: error: use of undeclared identifier 'TWO_POW_F'\n"
            "dut.sv:50:3: error: scalar type cannot be indexed\n")
    out = explain_compile_error(text, "module nlu_exp (input logic clk);\n  // Given the constraint of")
    assert out.startswith(text.rstrip()) and "WHAT TO FIX:" in out
    fixes = out.split("WHAT TO FIX:\n")[1].splitlines()
    assert fixes[0].startswith("* the source has NO `endmodule` -- it stops part-way, at: `// Given the constraint of`")
    assert any("sits at MODULE scope" in f for f in fixes)
    assert sum("'TWO_POW_F' is used but never declared" in f for f in fixes) == 1
    assert any("declared 1 bit wide is being indexed" in f for f in fixes)
    assert explain_compile_error("Verilator: %Error: something else", "module m; endmodule") == \
        "Verilator: %Error: something else"
    two = explain_compile_error("error: cannot have multiple continuous assignments to variable 'mc_q15'",
                                "module m; endmodule")
    assert "'mc_q15' is driven by two `assign`s" in two and "assign mc_q15 = cond ? a : b;" in two
