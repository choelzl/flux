"""`flux rtl test` and `flux rtl measure` (D579): an RTL problem with no code of its own."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

FLUX = Path(__file__).resolve().parents[2]
EXAMPLE = FLUX / "applications" / "mul8"
GOOD = """module mul8(input logic signed [7:0] a, input logic signed [7:0] w, output logic signed [15:0] p);
  wire signed [15:0] t;
  assign t = a * w;
  assign p = t;
endmodule
"""


def _rtl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "flux_cli.main", "rtl", *args], capture_output=True, text=True, timeout=900, cwd=str(FLUX))


def test_the_golden_model_gives_the_vectors():
    from flux_cli.rtl import load_golden
    from flux_codegen_rtl_harness import golden_vectors

    g = load_golden(EXAMPLE / "golden.py")
    rows = golden_vectors(g)
    inputs = {(r["inputs"]["a"], r["inputs"]["w"]) for r in rows}
    assert {(-128, -128), (127, 127), (-128, 127), (0, 0), (-1, 1)} <= inputs, "the corners, pairwise"
    assert all(r["expected"] == {"p": r["inputs"]["a"] * r["inputs"]["w"]} for r in rows)
    assert 24 + 25 <= len(rows) <= 24 + 40 and len(inputs) == len(rows), "the corners pairwise, then 24 random"
    assert not g.clocked and g.latency is None
    with pytest.raises(SystemExit, match="golden model"):
        load_golden(EXAMPLE / "nope.py")


def test_the_example_document_loads_and_names_the_two_commands():
    from flux_loop import PromptProblem, load_task
    from flux_loop.document import describe_flow

    task = load_task(EXAMPLE / "problem.yaml")
    assert task.gate.named("test").run[:8] == ("{python}", "-W", "ignore", "-m", "flux_cli.main", "rtl", "test", "{artifact}")
    assert "{home}/golden.py" in task.gate.named("test").run and task.home.endswith("mul8")
    assert [s.name for s in task.stages] == ["screen", "confirm"]
    prob = PromptProblem(task)
    assert prob.subgoals() == []
    assert any(line.startswith("test: gate (never delegated) -- the document's commands") for line in describe_flow(task, prob))


@pytest.mark.heavy
@pytest.mark.skipif(shutil.which("verilator") is None, reason="needs verilator")
def test_rtl_test_passes_the_right_module_and_names_the_wrong_ones_vectors(tmp_path):
    good = tmp_path / "good.sv"
    good.write_text(GOOD)
    r = _rtl("test", str(good), "--golden", str(EXAMPLE / "golden.py"))
    assert r.returncode == 0 and r.stdout.strip().endswith("0 failing of " + r.stdout.strip().rsplit(" ", 1)[-1]), r.stdout + r.stderr
    bad = tmp_path / "bad.sv"
    bad.write_text(GOOD.replace("assign t = a * w;", "assign t = a * w + 16'sd1;"))
    r = _rtl("test", str(bad), "--golden", str(EXAMPLE / "golden.py"))
    assert r.returncode == 1 and "VECTOR 0 FAIL" in r.stdout and "expected p=" in r.stdout
    n, _, m = r.stdout.strip().splitlines()[-1].partition(" failing of ")
    assert int(n) == int(m), "every product is off by one"
    broken = tmp_path / "broken.sv"
    broken.write_text("module mul8(input logic signed [7:0] a, output logic signed [15:0] p);\nassign p = {10{a[7]}, a};\nendmodule\n")
    r = _rtl("test", str(broken), "--golden", str(EXAMPLE / "golden.py"))
    assert r.returncode == 3 and "did not compile" in r.stdout and "line 2 of your module" in r.stdout   # D594: 3 = not built


@pytest.mark.heavy
@pytest.mark.skipif(shutil.which("verilator") is None, reason="needs verilator")
def test_a_one_bit_port_is_checked_as_one_bit(tmp_path):
    """A 1-bit port (a carry-out) is a plain `logic`; it was widened to 2 bits and every design failed to compile (D621)."""
    golden = tmp_path / "golden.py"
    golden.write_text("PORTS = [{'name': 'a', 'dir': 'in', 'bits': 4, 'unsigned': True},\n"
                      "         {'name': 'b', 'dir': 'in', 'bits': 4, 'unsigned': True},\n"
                      "         {'name': 's', 'dir': 'out', 'bits': 4, 'unsigned': True},\n"
                      "         {'name': 'cout', 'dir': 'out', 'bits': 1, 'unsigned': True}]\n"
                      "COUNT = 16\n\ndef golden(a, b):\n    return {'s': (a + b) & 15, 'cout': (a + b) >> 4}\n")
    add = "module add4(input [3:0] a, input [3:0] b, output [3:0] s, output cout);\n  assign {cout, s} = a + b;\nendmodule\n"
    good = tmp_path / "add4.sv"
    good.write_text(add)
    r = _rtl("test", str(good), "--golden", str(golden))
    assert r.returncode == 0 and "0 failing" in r.stdout, r.stdout + r.stderr
    bad = tmp_path / "bad.sv"
    bad.write_text(add.replace("assign {cout, s} = a + b;", "assign s = a + b; assign cout = 1'b0;"))
    r = _rtl("test", str(bad), "--golden", str(golden))
    assert r.returncode == 1 and "expected s=" in r.stdout and "cout=1" in r.stdout, r.stdout


@pytest.mark.heavy
@pytest.mark.skipif(shutil.which("yosys") is None, reason="needs yosys")
def test_rtl_measure_prints_the_screens_numbers(tmp_path):
    good = tmp_path / "good.sv"
    good.write_text(GOOD)
    r = _rtl("measure", str(good), "--stage", "synth", "--clock-ps", "1000")
    assert r.returncode == 0, r.stdout + r.stderr
    line = r.stdout.strip().splitlines()[0]
    assert line.startswith("fmax_mhz=") and "area_um2=" in line and "cell_count=" in line and "stage=synth" in line


def test_an_unsigned_port_reaches_the_harness_as_the_same_bits_and_comes_back_unsigned():
    """D581: the harness's testbench reads every int port signed; an unsigned value with its
    top bit set goes over as those bits read signed, and a FAIL line is read back unsigned."""
    from flux_cli.rtl import load_golden
    from flux_codegen_rtl_harness.golden import _as_harness, _from_harness

    g = load_golden(FLUX / "applications" / "adder16" / "golden.py")
    rows = [{"inputs": {"a": 65535, "b": 1}, "expected": {"s": 65536}},
            {"inputs": {"a": 3, "b": 4}, "expected": {"s": 7}}]
    got = _as_harness(g, rows)
    assert got[0] == {"inputs": {"a": -1, "b": 1}, "expected": {"s": -65536}} and got[1] == rows[1]
    assert _from_harness(g, "VECTOR 0 FAIL s=-65536") == "VECTOR 0 FAIL s=65536"
    assert _from_harness(g, "VECTOR 0 FAIL q=-3") == "VECTOR 0 FAIL q=-3"      # not a port of it


def test_the_clock_port_is_found_in_the_modules_header():
    """D582: `measure_rtl`'s "auto" takes clk / rst_n when the module declares them."""
    from flux_evaluator_openroad.flow import _port_of

    src = "module other(input clk);\nendmodule\nmodule pe(input logic clk, input logic rst_n,\n  input [7:0] a, output [7:0] y);\nendmodule\n"
    assert _port_of(src, "pe", "clk") == "clk" and _port_of(src, "pe", "rst_n") == "rst_n"
    assert _port_of("module m(input [7:0] a, output [7:0] y);\nendmodule\n", "m", "clk") is None
    assert _port_of(src, "other", "rst_n") is None


def test_the_ulp_distance_of_ieee_patterns():
    """D589: representable values apart; +0/-0 equal; two NaNs equal; a NaN against a number never."""
    from flux_codegen_rtl_harness.golden import ulp_distance

    assert ulp_distance(0x3C00, 0x3C01, 16) == 1 and ulp_distance(0x3C00, 0x3BFF, 16) == 1
    assert ulp_distance(0x0000, 0x8000, 16) == 0 and ulp_distance(0x0001, 0x8001, 16) == 2
    assert ulp_distance(0x7E00, 0x7C01, 16) == 0 and ulp_distance(0x7E00, 0x3C00, 16) == float("inf")
    assert ulp_distance(0x3F800000, 0x3F800002, 32) == 2


def _half_golden(tmp_path, tolerance: int | None):
    g = tmp_path / "golden.py"
    g.write_text(
        "import numpy as np\n"
        "PORTS = [{'name': 'x', 'dir': 'in', 'bits': 16, 'unsigned': True}, {'name': 'y', 'dir': 'out', 'bits': 16, 'unsigned': True}]\n"
        "VECTORS = [{'x': b} for b in (0x3C00, 0x4000, 0x3800, 0xBC00, 0x0000)]\n"
        + (f"TOLERANCE_ULP = {{'y': {tolerance}}}\n" if tolerance is not None else "")
        + "def golden(x):\n"
        "    v = float(np.uint16(x).view(np.float16))\n"
        "    return {'y': int(np.float16(v * 0.5).view(np.uint16))}\n")
    return g


@pytest.mark.heavy
@pytest.mark.skipif(shutil.which("verilator") is None, reason="needs verilator")
def test_a_float_output_may_be_off_by_the_goldens_ulp_tolerance(tmp_path):
    """Halving that is one ULP high on the positive inputs: refused exactly, admitted within 1 ULP."""
    art = tmp_path / "half.sv"
    art.write_text("module half(input logic [15:0] x, output logic [15:0] y);\n"
                   "  wire [15:0] h = (x[14:0] == 0) ? x : {x[15], x[14:10] - 5'd1, x[9:0]};\n"
                   "  assign y = (x[15] == 0 && x[14:0] != 0) ? h + 16'd1 : h;\n"
                   "endmodule\n")
    r = _rtl("test", str(art), "--golden", str(_half_golden(tmp_path, None)))
    assert r.returncode == 1 and "3 failing of 5" in r.stdout, r.stdout
    r = _rtl("test", str(art), "--golden", str(_half_golden(tmp_path, 1)))
    assert r.returncode == 0 and "0 failing of 5" in r.stdout, r.stdout


@pytest.mark.heavy
@pytest.mark.skipif(shutil.which("verilator") is None, reason="needs verilator")
def test_a_clock_the_golden_does_not_declare_is_explained(tmp_path):
    art = tmp_path / "mul8.sv"
    art.write_text("module mul8(input logic clk, input logic signed [7:0] a, input logic signed [7:0] w,\n"
                   "            output logic signed [15:0] p);\n  always_ff @(posedge clk) p <= a * w;\nendmodule\n")
    r = _rtl("test", str(art), "--golden", str(EXAMPLE / "golden.py"))
    assert r.returncode == 3 and "golden model declares no CLOCK" in r.stdout, r.stdout
