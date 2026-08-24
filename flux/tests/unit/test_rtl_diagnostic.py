"""D552: a compile error is re-said for the model that wrote the module -- its own line
number, the line quoted with a caret, a hint for a known slip."""

from __future__ import annotations


SOURCE = """module mul1(input logic signed [7:0] a, output logic signed [15:0] p);
wire [4:0] pp0;
assign pp_ext0 = {10{pp0[7]}, pp0};
endmodule"""


def test_the_diagnostic_names_the_models_line_quotes_it_and_hints():
    from flux_codegen_rtl_harness import explain_diagnostic

    msg = ("verilator exited 1:\n%Error: /tmp/x/dut.sv:5:29: syntax error, unexpected ',', expecting '}'\n"
           "%Error: Exiting due to 1 error(s)")
    got = explain_diagnostic(msg, SOURCE, prefix_lines=2)
    assert got.startswith("syntax error, unexpected ',', expecting '}' -- line 3 of your module, column 29:")
    assert "    assign pp_ext0 = {10{pp0[7]}, pp0};" in got
    assert got.splitlines()[2] == "    " + " " * 28 + "^"
    assert "hint: a replication inside a concatenation needs braces of its own" in got


def test_a_diagnostic_that_maps_to_no_line_is_said_as_it_was():
    from flux_codegen_rtl_harness import explain_diagnostic

    assert explain_diagnostic("%Error: /tmp/x/dut.sv:40:1: whatever", SOURCE, prefix_lines=2) == "%Error: /tmp/x/dut.sv:40:1: whatever"
    assert explain_diagnostic("nothing structured\nmore", SOURCE) == "nothing structured"
    got = explain_diagnostic("%Error: dut.sv:2:10: Can't find definition of variable: 'q'", SOURCE)
    assert got.startswith("Can't find definition of variable: 'q' -- line 2 of your module") and "declare the wire" in got


def test_the_harness_reads_a_fenced_module_and_screens_it_before_any_tool_runs():
    """D557: the fence reader, the lint pragmas and the rules screen are the harness's, not
    each world's."""
    from flux_codegen_rtl_harness import LINT_PRAGMA, fenced_module, lint_relaxed, sv_refusal

    reply = "IDEA: x\n```verilog\nmodule m(input a, output b);\nassign b = a;\nendmodule\n```\nthanks"
    assert fenced_module("m", reply) == "module m(input a, output b);\nassign b = a;\nendmodule\n"
    assert fenced_module("other", reply) is None and fenced_module("m", "module m(); no end") is None
    assert lint_relaxed("module m; endmodule").startswith(LINT_PRAGMA) and lint_relaxed(LINT_PRAGMA + "x").count("lint_off") == 2
    assert sv_refusal("always_ff @(posedge clk) q <= d;") == "sequential logic: the module must be combinational"
    assert sv_refusal("always_ff @(posedge clk) q <= d;", combinational=False) is None
    assert sv_refusal("assign x = 8'(y);").startswith("size casts") and sv_refusal("$display(1);") == "system tasks are not synthesizable"
    assert sv_refusal("assign p = $signed(a) * $signed(w);") is None
