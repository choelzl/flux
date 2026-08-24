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


def test_the_golden_model_gives_the_vectors_and_the_spec():
    from flux_cli.rtl import load_golden, spec_from_golden, vectors_from_golden
    from flux_codegen_rtl_harness import design_spec_from_dict

    mod = load_golden(EXAMPLE / "golden.py")
    rows = vectors_from_golden(mod)
    inputs = {(r["inputs"]["a"], r["inputs"]["w"]) for r in rows}
    assert {(-128, -128), (127, 127), (-128, 127), (0, 0), (-1, 1)} <= inputs, "the corners, pairwise"
    assert all(r["expected"] == {"p": r["inputs"]["a"] * r["inputs"]["w"]} for r in rows)
    assert 24 + 25 <= len(rows) <= 24 + 40 and len(inputs) == len(rows), "the corners pairwise, then 24 random"
    spec = spec_from_golden(mod, "mul8", rows)
    assert design_spec_from_dict(spec).module_name == "mul8"
    assert [p["bits"] for p in spec["ports"]] == [8, 8, 16] and "is_clocked" not in spec
    with pytest.raises(SystemExit, match="golden model"):
        load_golden(EXAMPLE / "nope.py")


def test_the_example_document_loads_and_names_the_two_commands():
    from flux_loop import PromptProblem, load_task
    from flux_loop.task import describe_flow

    task = load_task(EXAMPLE / "mul8.problem.yaml")
    assert task.gate.test[:6] == ("{python}", "-m", "flux_cli.main", "rtl", "test", "{artifact}")
    assert "{home}/golden.py" in task.gate.test and task.home.endswith("mul8")
    assert [s.name for s in task.stages] == ["screen", "confirm"]
    prob = PromptProblem(task)
    assert prob.world is None and prob.subgoals() == []
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
    assert r.returncode == 1 and "did not compile" in r.stdout and "line 2 of your module" in r.stdout


@pytest.mark.heavy
@pytest.mark.skipif(shutil.which("yosys") is None, reason="needs yosys")
def test_rtl_measure_prints_the_screens_numbers(tmp_path):
    good = tmp_path / "good.sv"
    good.write_text(GOOD)
    r = _rtl("measure", str(good), "--stage", "synth", "--clock-ps", "1000")
    assert r.returncode == 0, r.stdout + r.stderr
    line = r.stdout.strip().splitlines()[0]
    assert line.startswith("fmax_mhz=") and "area_um2=" in line and "cell_count=" in line and "stage=synth" in line
