"""`flux prog time|count|size` (D661), `flux rtl measure --stage stat` (D662) and the catalog's
evaluator stages (D663): each prints `name=value` lines a document's stage reads."""

from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

import pytest

SUM_C = """#include <stdio.h>
int big[4096];
int main(void) { long s = 0; for (int r = 0; r < 50; r++) for (int i = 0; i < 4096; i++) s += big[i] + i;
                 printf("%ld\\n", s); return 0; }
"""
CC = shutil.which("cc")


def _flux(capsys, *argv: str) -> tuple[int, dict[str, str]]:
    from flux_cli.main import main

    code = main(list(argv))
    out = capsys.readouterr().out
    return code, dict(re.findall(r"(?:^|(?<=\s))(\w+)=(\S+)", out)) | {"_out": out}


def _build(tmp_path: Path) -> str:
    src = tmp_path / "sum.c"
    src.write_text(SUM_C)
    return f"cc -O2 -o {{out}} {src}"


def test_prog_time_without_hyperfine_times_a_python_loop(capsys, tmp_path, monkeypatch):
    import flux_cli.prog as prog

    real = shutil.which
    monkeypatch.setattr(prog.shutil, "which", lambda t: None if t == "hyperfine" else real(t))
    script = tmp_path / "p.py"
    script.write_text("print(sum(range(1000)))\n")
    code, got = _flux(capsys, "prog", "time", "--run", f"{sys.executable} {script}", "--runs", "3", "--warmup", "1")
    assert code == 0, got["_out"]
    assert got["runs"] == "3" and got["timer"] == "python"
    assert 0 < float(got["time_ms_min"]) <= float(got["time_ms"]) and float(got["time_ms_stddev"]) >= 0


@pytest.mark.skipif(shutil.which("hyperfine") is None or CC is None, reason="needs hyperfine and cc")
def test_prog_time_builds_then_times_with_hyperfine(capsys, tmp_path):
    code, got = _flux(capsys, "prog", "time", "--build", _build(tmp_path), "--runs", "3", "--warmup", "0")
    assert code == 0, got["_out"]
    assert got["timer"] == "hyperfine" and got["runs"] == "3" and float(got["time_ms"]) > 0


@pytest.mark.skipif(CC is None, reason="needs cc")
def test_a_build_that_fails_did_not_build(capsys, tmp_path):
    (tmp_path / "bad.c").write_text("int main(void) { return x; }\n")
    code, got = _flux(capsys, "prog", "time", "--build", f"cc -o {{out}} {tmp_path / 'bad.c'}")
    assert code == 3 and "did not build" in got["_out"] and "1 failing" in got["_out"]
    code, got = _flux(capsys, "prog", "count")
    assert code == 2 and "nothing to run" in got["_out"]


@pytest.mark.skipif(shutil.which("valgrind") is None or CC is None, reason="needs valgrind and cc")
def test_prog_count_is_cachegrinds_counts_the_same_every_run(capsys, tmp_path):
    code, first = _flux(capsys, "prog", "count", "--build", _build(tmp_path))
    assert code == 0, first["_out"]
    assert {"instructions", "d1_misses", "ll_misses", "branch_mispredicts"} <= set(first)
    assert int(first["instructions"]) > 50 * 4096
    _, again = _flux(capsys, "prog", "count", "--build", _build(tmp_path))
    assert again["instructions"] == first["instructions"]


@pytest.mark.skipif(shutil.which("size") is None or CC is None, reason="needs size (binutils) and cc")
def test_prog_size_reads_the_sections(capsys, tmp_path):
    code, got = _flux(capsys, "prog", "size", "--build", _build(tmp_path))
    assert code == 0, got["_out"]
    assert int(got["text_bytes"]) > 0 and int(got["bss_bytes"]) >= 4096 * 4       # `big` is zeroed data
    code, again = _flux(capsys, "prog", "size", sys.executable)                   # a program already built
    assert code == 0 and int(again["text_bytes"]) > 0


@pytest.mark.skipif(shutil.which("yosys") is None, reason="needs yosys")
def test_rtl_measure_stat_is_yosys_alone(capsys, tmp_path):
    sv = tmp_path / "add8.sv"
    sv.write_text("module add8(input logic [7:0] a, b, output logic [8:0] s);\n  assign s = a + b;\nendmodule\n")
    code, got = _flux(capsys, "rtl", "measure", str(sv), "--stage", "stat")
    assert code == 0, got["_out"]
    assert float(got["area_um2"]) > 0 and int(got["cell_count"]) > 0
    assert got["stage"] == "stat" and "fmax_mhz" not in got


def test_documents_name_what_the_new_stages_need():
    from flux_loop import TaskSpec
    from flux_loop.document import _flux_rtl_tools

    py = ["{python}", "-W", "ignore", "-m", "flux_cli.main"]
    assert _flux_rtl_tools(py + ["prog", "count", "--build", "cc -o {out} {artifact}"]) == ["valgrind"]
    assert _flux_rtl_tools(py + ["prog", "size"]) == ["size"] and _flux_rtl_tools(py + ["prog", "time"]) == []
    assert _flux_rtl_tools(py + ["rtl", "measure", "{artifact}", "--stage", "stat"]) == ["yosys"]
    task = TaskSpec.from_dict({"id": "t",
                               "statement": "x",
                               "language": "systemverilog",
                               "flow": {"test": "flux rtl lint {artifact}",
                                        "measure": {"stat": "flux rtl measure {artifact} --stage stat"}}})
    assert task.stages[0].metrics == ("area_um2", "cell_count") and task.stages[0].needs == ("yosys",)
    prog = TaskSpec.from_dict({"id": "p",
                               "statement": "x",
                               "language": "c",
                               "flow": {"test": "cc -fsyntax-only {artifact}",
                                        "measure": {"count": {"metrics": ["instructions"],
                                                              "command": 'flux prog count --build "cc -O2 -o {out} {artifact}"'}}}})
    assert prog.stages[0].needs == ("valgrind",)


def test_an_evaluator_stage_reads_the_workload_beside_the_document(tmp_path):
    """D663: `workload: "{home}/w.yaml"` (or `w.yaml`) is read from the document's folder."""
    from types import SimpleNamespace

    from flux_evaluator_abi import register_evaluator
    from flux_loop import Candidate, LoopRequest, LoopState, PromptProblem, load_task

    seen: list = []

    class Fake:
        def evaluate(self, cand, _budget, _metrics):
            seen.append((cand.workload, cand.arch))
            return SimpleNamespace(metrics={"latency_cycles": SimpleNamespace(value=7.0)})

    register_evaluator("fake-d663", Fake, replace=True)
    (tmp_path / "w.yaml").write_text("id: w\nops: []\n")
    for workload in ("{home}/w.yaml", "w.yaml"):
        (tmp_path / "t.problem.yaml").write_text(
            f'statement: x\nlanguage: yaml\nworkload: "{workload}"\n'
            "flow: {test: 'true', measure: {m: {evaluator: fake-d663, metrics: [latency_cycles]}}}\n"
            "objectives: [{metric: latency_cycles, direction: minimize}]\n")
        task = load_task(str(tmp_path / "t.problem.yaml"))
        st = LoopState(request=LoopRequest(db=""), say=lambda _m: None, proposer=None, feedback=None)
        st.workdir = str(tmp_path)
        assert PromptProblem(task).measure(Candidate(name="a", artifact="level: 1\n"), "m", st) == {"latency_cycles": 7.0}
    assert seen[-1] == ({"id": "w", "ops": []}, {"level": 1})


def test_the_catalog_carries_the_new_tools():
    from flux_loop.objective import UNITS
    from flux_loop.toolbox import TOOLS, fill, tool

    for tid in ("prog-time", "prog-count", "prog-size", "rtl-stat", "zigzag-eval", "timeloop-eval"):
        t = tool(tid)
        assert t["role"] == "stage" and all(m in UNITS for m in t["metrics"]), tid
    assert fill("prog-count") == 'flux prog count --build "c++ -O2 -o {out} {artifact}" --run ""'
    assert fill("rtl-stat") == "flux rtl measure {artifact} --stage stat --clock-ps 1000"
    evals = [t for t in TOOLS if "stage" in t]
    assert {t["stage"]["evaluator"] for t in evals} == {"zigzag", "timeloop"}
    assert all("run" not in t and t["document"] == {"workload": "{workload}"} for t in evals)
    assert all(("run" in t) != ("stage" in t) for t in TOOLS)
    with pytest.raises(ValueError, match="evaluator stage"):
        fill("zigzag-eval")
