"""D864: py2sv spells a pipeline. A clocked golden's LATENCY cuts the spelled datapath into that
many register stages, balanced by an estimated logic depth; `done` is `start` through as many
registers. Checked two ways: the harness's start/done protocol on every input, and a stream -- a
new input every cycle, each output compared LATENCY cycles later -- which the harness (inputs held
steady per vector) would pass even with a value crossing a stage unregistered."""

from __future__ import annotations

import random
import shutil
import subprocess

import pytest
from test_py2sv import CASES, PORTS8

from flux_codegen_rtl_harness import Golden, check_rtl
from flux_loop.golden_proto import exhaustive, table_functions
from flux_loop.py2sv import spell


def _spell(code, golden, latency, ports=PORTS8):
    g = Golden(ports=tuple(ports), fn=golden, count=64)
    return spell(code, list(ports), exhaustive(g), "m", table_functions, latency=latency)


@pytest.mark.parametrize("latency", [1, 2, 3])
@pytest.mark.parametrize("case", sorted(CASES))
def test_a_pipelined_spelling_passes_the_clocked_golden(case, latency):
    code, golden = CASES[case]
    sv = _spell(code, golden, latency)
    assert "always_ff" in sv and "input logic clk" in sv
    g = Golden(ports=PORTS8, fn=golden, count=64, clocked=True, latency=latency)
    got = check_rtl(sv, g, module="m")
    assert got.ok and got.latency == latency, (got.error, got.lines[:3], sv[-1500:])


def test_latency_zero_is_the_combinational_spelling():
    code, golden = CASES["branches and early returns"]
    assert "always_ff" not in _spell(code, golden, 0)


@pytest.mark.skipif(shutil.which("verilator") is None, reason="needs Verilator")
@pytest.mark.parametrize("latency", [2, 3])
def test_a_new_input_every_cycle_comes_out_latency_cycles_later(tmp_path, latency):
    code, golden = CASES["a table, bit_length, // and % by constants"]
    sv = _spell(code, golden, latency)
    rng = random.Random(7)
    xs = [rng.randrange(256) for _ in range(200)]
    want = [golden(a)["y"] for a in xs]
    tb = ["module tb;", "  logic clk = 0, rst_n = 0, start = 0, done;", "  logic [7:0] a; logic [15:0] y;",
          "  integer bad = 0;", "  m dut(.clk(clk), .rst_n(rst_n), .start(start), .done(done), .a(a), .y(y));",
          "  always #5 clk = ~clk;", "  initial begin", "    @(posedge clk); #1 rst_n = 1;"]
    for i, a in enumerate(xs + [0] * latency):
        tb.append(f"    a = 8'd{a}; @(posedge clk); #1;")
        j = i - latency + 1                      # the input whose answer is out after this edge
        if 0 <= j < len(xs):
            tb.append(f"    if (y !== 16'd{want[j]}) begin bad = bad + 1; $display(\"BAD %0d y=%0d\", {j}, y); end")
    tb += ["    $display(\"BAD_TOTAL %0d\", bad);", "    $finish;", "  end", "endmodule"]
    (tmp_path / "m.sv").write_text(sv)
    (tmp_path / "tb.sv").write_text("\n".join(tb) + "\n")
    build = subprocess.run(["verilator", "--binary", "--timing", "-Wno-fatal", "-Wno-lint", "--top-module", "tb",
                            "-Mdir", str(tmp_path / "obj"), "m.sv", "tb.sv"], cwd=tmp_path, capture_output=True,
                           text=True, timeout=300)
    assert build.returncode == 0, build.stderr[-2000:]
    run = subprocess.run([str(tmp_path / "obj" / "Vtb")], capture_output=True, text=True, timeout=60)
    assert "BAD_TOTAL 0" in run.stdout, run.stdout[-1500:]
