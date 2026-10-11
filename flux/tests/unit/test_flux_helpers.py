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
    assert {"golden", "rtl", "prog", "bench", "champsim", "cases", "pytest"} <= set(helpers)
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
    assert {e["id"] for e in described} == {"rtl-lint", "rtl-stat", "rtl-synth", "rtl-place", "rtl-route", "rtl-cycles"}
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
