"""Flux's helpers keep one interface (D968, D970): each module has `main(argv)` and `catalog()`, its
entries run as `fluxhelper NAME ...`, `--help` works, a wrong argument is exit 2, and a document's
`fluxhelper ...` runs with the interpreter running Flux, read by the loader like any catalog tool."""

from __future__ import annotations

import importlib
import json
import subprocess
import sys

import pytest

KEYS = {"id", "role", "title", "what", "run", "params", "needs", "languages", "kinds", "short"}


def _helpers() -> dict[str, list[dict]]:
    from flux_helpers import catalog

    out: dict[str, list[dict]] = {}
    for e in catalog():
        out.setdefault(e["helper_module"], []).append(e)
    return out


def _fluxhelper(*args: str, stdin=subprocess.DEVNULL) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "flux_helpers", *args], capture_output=True, text=True, timeout=60, stdin=stdin)


def test_the_helpers_are_the_modules_that_say_their_entries():
    helpers = _helpers()
    assert {"golden", "rtl", "prog", "bench", "champsim", "cases", "pytest", "ccases", "sanitize", "accuracy",
            "timebound", "regress", "extract", "systemc"} <= set(helpers)
    for name, entries in helpers.items():
        mod = importlib.import_module(f"flux_helpers.{name}")
        assert callable(getattr(mod, "main", None)), f"{name}: main(argv)"
        for e in entries:
            assert KEYS <= set(e), (name, e["id"], KEYS - set(e))
            assert e["run"].split()[:2] == ["fluxhelper", name], (name, e["run"])
            assert e["role"] in ("check", "stage") and (("pass" in e) == (e["role"] == "check")) and (("metrics" in e) == (e["role"] == "stage"))
            assert all({"label", "default", "unit"} <= set(p) for p in e["params"].values()), e["id"]
            assert all("{" + k + "}" in e["run"] for k in e["params"]), (e["id"], "every param is in the command")


@pytest.mark.parametrize("name", sorted(_helpers()))
def test_every_helper_answers_help_and_refuses_wrong_arguments_with_2(name):
    ok = _fluxhelper(name, "--help")
    assert ok.returncode == 0 and ok.stdout.strip(), ok.stderr
    bad = _fluxhelper(name, "--no-such-option")
    assert bad.returncode == 2, (bad.returncode, bad.stderr)


def test_fluxhelper_lists_describes_and_refuses_an_unknown_helper():
    listed = _fluxhelper()
    assert listed.returncode == 0 and "rtl" in listed.stdout and "golden" in listed.stdout
    described = json.loads(_fluxhelper("describe", "rtl").stdout)
    assert {e["id"] for e in described} >= {"rtl-lint", "rtl-stat", "rtl-synth", "rtl-place", "rtl-route", "rtl-cycles", "rtl-equiv",
                                            "rtl-breakdown", "rtl-fmax", "rtl-power", "rtl-energy", "rtl-path"}
    assert _fluxhelper("nope").returncode == 2 and _fluxhelper("describe", "nope").returncode == 2


def test_the_bench_helper_keeps_the_exit_codes(tmp_path):
    good = tmp_path / "d.py"
    good.write_text("def f(n):\n    return sum(range(n))\n")
    r = _fluxhelper("bench", str(good), "--call", "f", "--args", "(1000,)", "--runs", "3")
    assert r.returncode == 0 and r.stdout.startswith("time_ms=") and "runs=3" in r.stdout, r.stderr
    (tmp_path / "broken.py").write_text("def f(:\n")
    assert _fluxhelper("bench", str(tmp_path / "broken.py"), "--call", "f").returncode == 3, "did not load"
    (tmp_path / "raises.py").write_text("def f():\n    raise ValueError('no')\n")
    r = _fluxhelper("bench", str(tmp_path / "raises.py"), "--call", "f")
    assert r.returncode == 1 and "ValueError" in r.stderr and "ValueError" not in r.stdout


def test_a_documents_fluxhelper_runs_with_flux_and_reads_like_a_catalog_tool():
    from flux_loop import TaskSpec
    from flux_loop.document import _flux_program_tools

    task = TaskSpec.from_dict({"id": "t", "statement": "s", "flow": {
        "test": "fluxhelper golden {artifact} --golden {home}/golden.py",
        "measure": {"synth": {"command": "fluxhelper rtl measure {artifact} --stage synth --clock-ps 1000",
                              "metrics": ["fmax_mhz"]}}}})
    assert task.gate.named("test").run[:6] == ("{python}", "-W", "ignore", "-m", "flux_helpers", "golden")
    assert task.language == "systemverilog", "a helper's entry says its language"
    assert _flux_program_tools(("{python}", "-W", "ignore", "-m", "flux_helpers", "prog", "count", "--build", "x")) == ["valgrind"]
    assert _flux_program_tools(("{python}", "-m", "flux_helpers.prog", "size", "--build", "x")) == ["size"]


# ---- D971: cases, pytest, prog memory, rtl cycles
def _python_design(tmp_path):
    (tmp_path / "d.py").write_text("def sq(n):\n    return n * n if n != 7 else 50\n")
    (tmp_path / "ref.py").write_text("CASES = [(0,), (3,)]\n\ndef sq(n):\n    return n * n\n\n"
                                     "def inputs(rng):\n    return (rng.randint(0, 10),)\n")
    return tmp_path / "d.py"


def test_cases_compares_with_the_reference_and_its_inputs(tmp_path):
    d = _python_design(tmp_path)
    r = _fluxhelper("cases", str(d), "--call", "sq", "--reference", str(tmp_path / "ref.py"), "--random", "40", "--seed", "1")
    assert r.returncode == 1 and "FAIL sq(7): got 50, expected 49" in r.stdout and r.stdout.strip().endswith("of 42")
    (tmp_path / "ok.py").write_text("def sq(n):\n    return n * n\n")
    r = _fluxhelper("cases", str(tmp_path / "ok.py"), "--call", "sq", "--reference", str(tmp_path / "ref.py"), "--random", "40")
    assert r.returncode == 0 and r.stdout.strip() == "0 failing of 42"
    (tmp_path / "c.json").write_text('[{"args": [4], "expected": 16}, {"args": [1.5], "expected": 2.25}]')
    assert _fluxhelper("cases", str(tmp_path / "ok.py"), "--call", "sq", "--cases", str(tmp_path / "c.json")).returncode == 0
    (tmp_path / "broken.py").write_text("def sq(:\n")
    assert _fluxhelper("cases", str(tmp_path / "broken.py"), "--call", "sq", "--cases", str(tmp_path / "c.json")).returncode == 3


def test_pytest_runs_your_tests_on_the_design(tmp_path):
    d = _python_design(tmp_path)
    (tmp_path / "test_d.py").write_text("from design import sq\n\ndef test_a():\n    assert sq(3) == 9\n\n"
                                        "def test_b():\n    assert sq(7) == 49\n")
    r = _fluxhelper("pytest", str(d), "--tests", str(tmp_path / "test_d.py"))
    assert r.returncode == 1 and "test_b" in r.stdout and r.stdout.strip().endswith("1 failing of 2"), r.stdout + r.stderr
    (tmp_path / "broken.py").write_text("def sq(:\n")
    assert _fluxhelper("pytest", str(tmp_path / "broken.py"), "--tests", str(tmp_path / "test_d.py")).returncode == 3


def test_prog_memory_counts_the_programs_own_peak(tmp_path):
    (tmp_path / "big.py").write_text("x = b\"1\" * 100_000_000\n")     # touched, so resident
    small = _fluxhelper("prog", "memory", "--run", f"{sys.executable} -c pass", "--runs", "1")
    big = _fluxhelper("prog", "memory", "--run", f"{sys.executable} {tmp_path / 'big.py'}", "--runs", "1")
    peak = lambda r: int(dict(x.split("=") for x in r.stdout.split())["peak_rss_kb"])  # noqa: E731
    assert small.returncode == big.returncode == 0 and peak(big) - peak(small) > 60_000, (small.stdout, big.stdout)


@pytest.mark.heavy
@pytest.mark.skipif(__import__("shutil").which("verilator") is None, reason="needs verilator")
def test_rtl_cycles_measures_a_clocked_designs_latency(tmp_path):
    (tmp_path / "pipe.sv").write_text(
        "module pipe(input logic clk, input logic rst_n, input logic start, output logic done,\n"
        "            input logic signed [7:0] a, input logic signed [7:0] b, output logic signed [8:0] y);\n"
        "  logic signed [8:0] r1; logic v1;\n  always_ff @(posedge clk) begin\n"
        "    if (!rst_n) begin v1 <= 0; done <= 0; end\n    else begin r1 <= a + b; v1 <= start; y <= r1; done <= v1; end\n"
        "  end\nendmodule\n")
    (tmp_path / "g.py").write_text("PORTS = [{'name': 'a', 'dir': 'in', 'bits': 8}, {'name': 'b', 'dir': 'in', 'bits': 8},"
                                   " {'name': 'y', 'dir': 'out', 'bits': 9}]\nCLOCK = True\nCOUNT = 10\n\n"
                                   "def golden(a, b):\n    return {'y': a + b}\n")
    r = _fluxhelper("rtl", "cycles", str(tmp_path / "pipe.sv"), "--golden", str(tmp_path / "g.py"))
    assert r.returncode == 0 and "latency_cycles=2 latency_min_cycles=2 cycles_per_result=2" in r.stdout, r.stdout + r.stderr


# ---- D972: ccases, sanitize, accuracy, rtl equiv
def test_ccases_calls_a_c_function_against_a_python_reference(tmp_path):
    if __import__("shutil").which("cc") is None:
        pytest.skip("needs a C compiler")
    (tmp_path / "add.c").write_text("long add(long a, long b) { return a == 7 ? 0 : a + b; }\n")
    (tmp_path / "ref.py").write_text("def add(a, b):\n    return a + b\n\ndef inputs(rng):\n    return (rng.randint(0, 9), rng.randint(0, 9))\n")
    r = _fluxhelper("ccases", str(tmp_path / "add.c"), "--call", "add", "--sig", "long(long,long)",
                    "--reference", f"{tmp_path / 'ref.py'}:add", "--random", "60", "--seed", "2")
    assert r.returncode == 1 and "FAIL add(7," in r.stdout and r.stdout.strip().endswith("of 60"), r.stdout + r.stderr
    (tmp_path / "broken.c").write_text("long add(long a, long b) { return a + b }\n")
    r = _fluxhelper("ccases", str(tmp_path / "broken.c"), "--call", "add", "--sig", "long(long,long)",
                    "--reference", f"{tmp_path / 'ref.py'}:add", "--random", "5")
    assert r.returncode == 3 and "did not build" in r.stdout
    assert _fluxhelper("ccases", str(tmp_path / "add.c"), "--call", "add", "--sig", "complex(long)", "--random", "1").returncode == 2


def test_sanitize_counts_memory_errors(tmp_path):
    if __import__("shutil").which("cc") is None:
        pytest.skip("needs a C compiler")
    (tmp_path / "bad.c").write_text("#include <stdio.h>\n#include <stdlib.h>\n"     # a read past the end, used: not optimised out
                                    "int main(){int*p=calloc(4, sizeof(int)); printf(\"%d\\n\", p[4]); free(p); return 0;}\n")
    (tmp_path / "good.c").write_text("int main(){return 0;}\n")
    bad = _fluxhelper("sanitize", "--build", f"cc -O1 -o {{out}} {tmp_path / 'bad.c'}")
    good = _fluxhelper("sanitize", "--build", f"cc -O1 -o {{out}} {tmp_path / 'good.c'}")
    assert bad.returncode == 1 and "heap-buffer-overflow" in bad.stdout, bad.stdout + bad.stderr
    assert good.returncode == 0 and good.stdout.strip() == "0 failing"


def test_accuracy_measures_how_far_an_approximation_is(tmp_path):
    (tmp_path / "e.py").write_text("def e(x):\n    return 1 + x + x * x / 2\n")
    (tmp_path / "ref.py").write_text("import math\n\ndef e(x):\n    return math.exp(x)\n\n"
                                     "def inputs(rng):\n    return (rng.uniform(-0.5, 0.5),)\n")
    r = _fluxhelper("accuracy", str(tmp_path / "e.py"), "--call", "e", "--reference", str(tmp_path / "ref.py"), "--random", "200")
    got = dict(x.split("=") for x in r.stdout.split())
    assert r.returncode == 0 and 0 < float(got["max_abs_error"]) < 0.03 and got["cases"] == "200", r.stdout + r.stderr


@pytest.mark.heavy
@pytest.mark.skipif(__import__("shutil").which("yosys") is None, reason="needs yosys")
def test_rtl_equiv_proves_or_shows_a_counterexample(tmp_path):
    ref = "module m(input logic signed [7:0] a, input logic signed [7:0] w, output logic signed [15:0] p);\n  assign p = a * w;\nendmodule\n"
    (tmp_path / "ref.sv").write_text(ref)
    (tmp_path / "same.sv").write_text(ref.replace("a * w", "w * a"))
    (tmp_path / "off.sv").write_text(ref.replace("a * w", "(a == 8'sd3) ? 16'sd0 : a * w"))
    same = _fluxhelper("rtl", "equiv", str(tmp_path / "same.sv"), "--reference", str(tmp_path / "ref.sv"))
    off = _fluxhelper("rtl", "equiv", str(tmp_path / "off.sv"), "--reference", str(tmp_path / "ref.sv"))
    assert same.returncode == 0 and same.stdout.strip() == "0 failing", same.stdout + same.stderr
    assert off.returncode == 1 and "input a = 3" in off.stdout, off.stdout


# ---- D973: timebound, regress, extract, systemc, rtl path, rtl energy
def test_timebound_fails_a_design_over_its_budget(tmp_path):
    (tmp_path / "t.py").write_text("def slow(n):\n    return sum(i * j for i in range(n) for j in range(n))\n\n"
                                   "def fast(n):\n    return (n * (n - 1) // 2) ** 2\n")
    slow = _fluxhelper("timebound", str(tmp_path / "t.py"), "--call", "slow", "--args", "(1500,)", "--max-ms", "20")
    fast = _fluxhelper("timebound", str(tmp_path / "t.py"), "--call", "fast", "--args", "(1500,)", "--max-ms", "20")
    assert slow.returncode == 1 and "over 20 ms" in slow.stdout and slow.stdout.strip().endswith("1 failing")
    assert fast.returncode == 0 and fast.stdout.strip().endswith("0 failing")
    (tmp_path / "forever.py").write_text("def f():\n    while True:\n        pass\n")
    stuck = _fluxhelper("timebound", str(tmp_path / "forever.py"), "--call", "f", "--max-ms", "100", "--runs", "1")
    assert stuck.returncode == 1 and "did not finish" in stuck.stdout


def test_regress_records_a_baseline_and_fails_a_worse_run(tmp_path):
    base = tmp_path / "b.json"
    say = lambda v: ["--", sys.executable, "-c", f"print('time_ms={v} runs=5')"]  # noqa: E731
    assert _fluxhelper("regress", "--baseline", str(base), "--metrics", "time_ms", "--record", *say(10)).returncode == 0
    assert json.loads(base.read_text()) == {"time_ms": 10.0}
    assert _fluxhelper("regress", "--baseline", str(base), "--tol", "5", *say(10.4)).returncode == 0
    worse = _fluxhelper("regress", "--baseline", str(base), "--tol", "5", *say(12))
    assert worse.returncode == 1 and "FAIL time_ms: 12" in worse.stdout
    assert _fluxhelper("regress", "--baseline", str(base), "--higher", "time_ms", *say(12)).returncode == 0


def test_extract_turns_json_csv_and_text_into_name_value(tmp_path):
    (tmp_path / "o.json").write_text('{"results": {"latency": [3.5, 7.25]}, "ok": 1}')
    (tmp_path / "o.csv").write_text("step,runtime\n1,10\n2,14\n")
    cat = lambda f: ["--", "cat", str(tmp_path / f)]  # noqa: E731
    r = _fluxhelper("extract", "--pick", "results.latency.1=latency_ms", "--pick", "ok=ok", *cat("o.json"))
    assert r.returncode == 0 and r.stdout.split() == ["latency_ms=7.25", "ok=1"]
    assert _fluxhelper("extract", "--format", "csv", "--pick", "runtime=t", "--row", "max", *cat("o.csv")).stdout.strip() == "t=14"
    assert _fluxhelper("extract", "--format", "regex", "--pick", r"(\d+),14=step", *cat("o.csv")).stdout.strip() == "step=2"
    assert _fluxhelper("extract", "--pick", "nope=x", *cat("o.json")).returncode == 1


@pytest.mark.heavy
def test_systemc_translates_and_hands_the_rtl_on(tmp_path):
    from flux_loop.systemc_proto import icsc

    if icsc() is None or __import__("shutil").which("verilator") is None:
        pytest.skip("needs ICSC and Verilator")
    (tmp_path / "add4.h").write_text("#include <systemc.h>\nSC_MODULE(add4) {\n  sc_in<sc_uint<4>> a, b;\n  sc_out<sc_uint<5>> s;\n"
                                     "  void run() { s.write(a.read() + b.read()); }\n  SC_CTOR(add4) { SC_METHOD(run); sensitive << a << b; }\n};\n")
    (tmp_path / "g.py").write_text("PORTS = [{'name': 'a', 'dir': 'in', 'bits': 4, 'unsigned': True}, {'name': 'b', 'dir': 'in', "
                                   "'bits': 4, 'unsigned': True}, {'name': 's', 'dir': 'out', 'bits': 5, 'unsigned': True}]\n"
                                   "def golden(a, b):\n    return {'s': a + b}\n")
    args = [str(tmp_path / "add4.h"), "--top", "add4", "--golden", str(tmp_path / "g.py")]
    assert _fluxhelper("systemc", *args).stdout.strip() == "0 failing"
    tested = _fluxhelper("systemc", *args, "--then", f"golden --golden {tmp_path / 'g.py'}")
    assert tested.returncode == 0 and tested.stdout.strip().startswith("0 failing of"), tested.stdout + tested.stderr
    (tmp_path / "bad.h").write_text("SC_MODULE(add4) { this is not C++ };\n")
    assert _fluxhelper("systemc", str(tmp_path / "bad.h"), *args[1:]).returncode == 3
