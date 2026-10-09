"""An RTL application's own `rtl.py test|lint|measure` (D579, D948): an RTL problem with no code of its own."""

from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import replace
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
    return subprocess.run([sys.executable, str(EXAMPLE / "rtl.py"), *args], capture_output=True, text=True, timeout=900, cwd=str(FLUX))


def test_the_golden_model_gives_the_vectors():
    from rtl import golden_vectors, load_golden

    g = load_golden(EXAMPLE / "golden.py")
    rows = golden_vectors(g)
    inputs = {(r["inputs"]["a"], r["inputs"]["w"]) for r in rows}
    assert {(-128, -128), (127, 127), (-128, 127), (0, 0), (-1, 1)} <= inputs, "the corners, pairwise"
    assert all(r["expected"] == {"p": r["inputs"]["a"] * r["inputs"]["w"]} for r in rows)
    assert g.exhaustive and len(rows) == len(inputs) == 65536, "every input pair (D865)"
    sampled = golden_vectors(replace(g, exhaustive=False, count=24))
    assert 24 + 25 <= len(sampled) <= 24 + 40, "else the corners pairwise, then 24 random"
    assert not g.clocked and g.latency is None
    with pytest.raises(SystemExit, match="golden model"):
        load_golden(EXAMPLE / "nope.py")


def test_the_example_document_loads_and_names_the_two_commands():
    from flux_loop import PromptProblem, load_task
    from flux_loop.document import describe_flow

    task = load_task(EXAMPLE / "problem.yaml")
    assert task.gate.named("test").run[:4] == ("{python}", "{home}/rtl.py", "test", "{artifact}")
    assert "{home}/golden.py" in task.gate.named("test").run and task.home.endswith("mul8")
    assert [s.name for s in task.stages] == ["screen", "confirm"]
    prob = PromptProblem(task)
    assert prob.subgoals() == []
    assert any(line.startswith("test: gate (never delegated) -- the document's commands") for line in describe_flow(task, prob))


def test_the_adder_and_the_multiplier_read_no_library():
    """D867: the shared library is the NLU's papers; an agent spent 40 minutes digesting 88 of
    them for the adder. Both documents turn it off."""
    from flux_loop import load_task
    from flux_loop.document import library_on

    for app in ("adder16", "mul8"):
        assert not library_on(load_task(FLUX / "applications" / app / "problem.yaml")), app


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


def test_the_vectors_are_a_table_the_testbench_reads():
    """D865: the vectors are a `$readmemh` table of the packed inputs, never unrolled lines; the
    test bench prints each row's outputs and Python compares them (D948)."""
    import rtl

    g = rtl.load_golden(EXAMPLE / "golden.py")
    tb = rtl._testbench(g, "mul8", 65536)
    assert '$readmemh("vectors.hex", tab);' in tb and "{a, w} = tab[v];" in tb and tb.count("\n") < 40
    assert "clk" not in tb and "mul8 dut (.a(a), .w(w), .p(p));" in tb
    clocked = rtl._testbench(replace(g, clocked=True, latency=2, exhaustive=False), "mul8", 10)
    assert ".clk(clk), .rst_n(rst_n), .start(start), .done(done)" in clocked and "while (done !== 1'b1" in clocked


@pytest.mark.heavy
@pytest.mark.skipif(shutil.which("verilator") is None, reason="needs verilator")
def test_a_multiplier_wrong_on_one_input_in_64_is_refused(tmp_path):
    """D865: off by 256 when a[3:0] == 4'b1011 and w[7:6] == 2'b01, 1,024 of 65,536 pairs: the 49
    sampled vectors passed it; the exhaustive gate names every one."""
    bad = tmp_path / "bad.sv"
    bad.write_text(GOOD.replace("assign t = a * w;", "assign t = (a[3:0] == 4'b1011 && w[7:6] == 2'b01) ? a * w + 16'sd256 : a * w;"))
    r = _rtl("test", str(bad), "--golden", str(EXAMPLE / "golden.py"))
    assert r.returncode == 1 and r.stdout.strip().endswith("1024 failing of 65536"), r.stdout[-2000:] + r.stderr


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


def test_an_output_is_read_back_at_its_width_signed_or_not():
    """D581: the bits that came out, read as the golden declares the port: signed unless
    `unsigned: true`; an x or z bit is no value."""
    import rtl

    assert rtl._value("ffff", 16, False) == 65535 and rtl._value("ffff", 16, True) == -1
    assert rtl._value("10000", 17, False) == 65536 and rtl._value("1", 1, False) == 1
    assert rtl._value("x", 1, False) is None and rtl._value("0z3f", 16, True) is None


def test_the_clock_port_is_found_in_the_modules_header():
    """D582: `measure` takes clk / rst_n when the module declares them."""
    import rtl

    src = "module other(input clk);\nendmodule\nmodule pe(input logic clk, input logic rst_n,\n  input [7:0] a, output [7:0] y);\nendmodule\n"
    assert {"clk", "rst_n"} <= rtl._ports(src, "pe") and "clk" in rtl._ports(src, "other")
    assert "clk" not in rtl._ports("module m(input [7:0] a, output [7:0] y);\nendmodule\n", "m")
    assert "rst_n" not in rtl._ports(src, "other")


def test_the_ulp_distance_of_ieee_patterns():
    """D589: representable values apart; +0/-0 equal; two NaNs equal; a NaN against a number never."""
    from rtl import ulp_distance

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


PIPE = """module pipe(input logic clk, input logic rst_n, input logic start, output logic done,
            input logic signed [7:0] a, input logic signed [7:0] b, output logic signed [8:0] y);
  logic signed [8:0] r1; logic v1;
  always_ff @(posedge clk) begin
    if (!rst_n) begin v1 <= 0; done <= 0; end
    else begin r1 <= a + b; v1 <= start; y <= r1; done <= v1; end
  end
endmodule
"""


@pytest.mark.heavy
@pytest.mark.skipif(shutil.which("verilator") is None, reason="needs verilator")
def test_a_clocked_design_is_checked_and_its_latency_measured(tmp_path):
    """CLOCK and LATENCY: clk/rst_n/start/done driven by the test bench, the cycles from the
    edge that took the inputs counted; a wrong claim is refused with what was measured."""
    art = tmp_path / "pipe.sv"
    art.write_text(PIPE)
    golden = "PORTS = [{'name': 'a', 'dir': 'in', 'bits': 8}, {'name': 'b', 'dir': 'in', 'bits': 8}, {'name': 'y', 'dir': 'out', 'bits': 9}]\n" \
             "CLOCK = True\nLATENCY = @LAT@\nCOUNT = 20\n\ndef golden(a, b):\n    return {'y': a + b}\n"
    (tmp_path / "g2.py").write_text(golden.replace("@LAT@", "2"))
    (tmp_path / "g3.py").write_text(golden.replace("@LAT@", "3"))
    r = _rtl("test", str(art), "--golden", str(tmp_path / "g2.py"))
    assert r.returncode == 0 and "latency=2" in r.stdout and r.stdout.strip().splitlines()[-1].startswith("0 failing of "), r.stdout + r.stderr
    r = _rtl("test", str(art), "--golden", str(tmp_path / "g3.py"))
    assert r.returncode == 1 and "claims 3 cycle(s) of latency, measured 2" in r.stdout, r.stdout


@pytest.mark.heavy
@pytest.mark.skipif(shutil.which("verilator") is None, reason="needs verilator")
def test_lint_says_each_defect_and_a_parse_error(tmp_path):
    good = tmp_path / "good.sv"
    good.write_text(GOOD)
    r = _rtl("lint", str(good))
    assert r.returncode == 0 and r.stdout.strip() == "0 failing", r.stdout + r.stderr
    broken = tmp_path / "broken.sv"
    broken.write_text("module mul8(input logic [7:0] a, output logic [15:0] p)\nassign p = a;\nendmodule\n")
    r = _rtl("lint", str(broken))
    assert r.returncode == 3 and "did not parse" in r.stdout and "line 2 of your module" in r.stdout, r.stdout


@pytest.mark.heavy
@pytest.mark.skipif(shutil.which("openroad") is None or shutil.which("yosys") is None, reason="needs yosys and openroad")
def test_measure_on_asap7_from_orfs_at_each_stage(tmp_path):
    """D948: Yosys and OpenROAD read ASAP7 from OpenROAD-flow-scripts, no PDK bundled: stat is
    Yosys alone; synth times the signed multiplier's netlist (OpenROAD refuses `input signed`
    until it is stripped); place times it placed, slower than unplaced; a clocked design's clock is its own."""
    import rtl

    try:
        rtl.platform()
    except SystemExit:
        pytest.skip("OpenROAD-flow-scripts' ASAP7 is not here (FLOW_HOME)")
    stat = rtl.measure(GOOD, stage="stat")
    assert set(stat) == {"area_um2", "cell_count"} and stat["area_um2"] > 0
    synth = rtl.measure(GOOD, stage="synth", clock_ps=625)
    place = rtl.measure(GOOD, stage="place", clock_ps=625)
    assert 1000 < synth["fmax_mhz"] < 4000 and synth["power_w"] > 0 and synth["cell_count"] == stat["cell_count"]
    assert place["fmax_mhz"] < synth["fmax_mhz"] and place["critical_path"].endswith(("]", "_"))
    assert abs(synth["path_ps"] - (625 - synth["worst_slack_ps"])) < 1e-6 and place["flow_depth"] == "placement"
    piped = rtl.measure(PIPE, stage="synth", clock_ps=400)
    assert piped["fmax_mhz"] > 1000 and piped["cell_count"] > 0
    r = _rtl("measure", str(tmp_path / "x.sv"), "--stage", "stat") if (tmp_path / "x.sv").write_text(GOOD) else None
    assert r.returncode == 0 and r.stdout.startswith("area_um2=") and "stage=stat" in r.stdout, r.stdout + r.stderr
