"""A compile error is re-said for the model that wrote the module: its own line number, the line
quoted with a caret, a hint for a known slip (D552)."""

from __future__ import annotations


SOURCE = """module mul1(input logic signed [7:0] a, output logic signed [15:0] p);
wire [4:0] pp0;
assign pp_ext0 = {10{pp0[7]}, pp0};
endmodule"""


def test_the_diagnostic_names_the_models_line_quotes_it_and_hints():
    from rtl import explain_diagnostic

    msg = ("verilator exited 1:\n%Error: /tmp/x/dut.sv:5:29: syntax error, unexpected ',', expecting '}'\n"
           "%Error: Exiting due to 1 error(s)")
    got = explain_diagnostic(msg, SOURCE, prefix_lines=2)
    assert got.startswith("syntax error, unexpected ',', expecting '}' -- line 3 of your module, column 29:")
    assert "    assign pp_ext0 = {10{pp0[7]}, pp0};" in got
    assert got.splitlines()[2] == "    " + " " * 28 + "^"
    assert "hint: a replication inside a concatenation needs braces of its own" in got


def test_a_diagnostic_that_maps_to_no_line_is_said_as_it_was():
    from rtl import explain_diagnostic

    assert explain_diagnostic("%Error: /tmp/x/dut.sv:40:1: whatever", SOURCE, prefix_lines=2) == "%Error: /tmp/x/dut.sv:40:1: whatever"
    assert explain_diagnostic("nothing structured\nmore", SOURCE) == "nothing structured"
    got = explain_diagnostic("%Error: dut.sv:2:10: Can't find definition of variable: 'q'", SOURCE)
    assert got.startswith("Can't find definition of variable: 'q' -- line 2 of your module") and "declare the wire" in got


def test_lint_pragmas_and_macarrays_rules_screen():
    """The lint pragmas a generated module is given (rtl.py), and the rules screen macarray's
    invented multiplier passes before any tool runs (D557; macarray's own since D956)."""
    from flux_macarray.invent import sv_refusal
    from rtl import LINT_PRAGMA, lint_relaxed

    assert lint_relaxed("module m; endmodule").startswith(LINT_PRAGMA) and lint_relaxed(LINT_PRAGMA + "x").count("lint_off") == 2
    assert sv_refusal("always_ff @(posedge clk) q <= d;") == "sequential logic: the module must be combinational"
    assert sv_refusal("always_ff @(posedge clk) q <= d;", combinational=False) is None
    assert sv_refusal("assign x = 8'(y);").startswith("size casts") and sv_refusal("$display(1);") == "system tasks are not synthesizable"
    assert sv_refusal("assign p = $signed(a) * $signed(w);") is None
