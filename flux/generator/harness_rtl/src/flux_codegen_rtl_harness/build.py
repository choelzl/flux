"""Compiles a DUT module against a generated testbench and runs it through Verilator (D43),
with the same invocation as `evaluator/rtl` (`--binary --build --timing ... -j 1`; `-j 1`
avoids a Verilator 5.020 threading bug with `--timing`).

The DUT is written to `dut.sv` (only `module ... endmodule`, no driving `initial` block) so the
generated `testbench.sv` can instantiate it. `extra_sources` (D48) adds already-verified modules,
e.g. the leaves a `compose.py` composite instantiates.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from .driver_gen import generate_testbench_sv, vector_table
from .errors import CompileError
from flux_codegen_harness_spec import DesignSpec, HarnessRunResult

_RESULT_RE = re.compile(r"^RESULT (PASS|FAIL) vectors=(\d+) passed=(\d+)", re.MULTILINE)
_VECTOR_FAIL_RE = re.compile(r"^VECTOR (\d+) FAIL .*$", re.MULTILINE)
_CYCLES_RE = re.compile(r"^CYCLES vector=(\d+) n=(\d+)", re.MULTILINE)


_TRAILING_ENDMODULE_SEMICOLON_RE = re.compile(r"\bendmodule\s*;")


def _normalized(source: str) -> str:
    # Normalize two cosmetic quirks (D61): Verilator rejects a missing trailing newline
    # (EOFNEWLINE) and Yosys rejects `endmodule;`.
    source = _TRAILING_ENDMODULE_SEMICOLON_RE.sub("endmodule", source)
    return source if source.endswith("\n") else source + "\n"


def compile_and_run(
    module_source: str,
    spec: DesignSpec,
    *,
    timeout_s: float = 120.0,
    keep_workdir: bool = False,
    extra_sources: dict[str, str] | None = None,
) -> HarnessRunResult:
    """Write `module_source` as `dut.sv`, generate `testbench.sv` from `spec`, compile both (plus
    `extra_sources`, {filename stem: source}) with Verilator, run the binary, and parse its
    `RESULT ...` line and per-vector `VECTOR N FAIL ...` diagnostics. A failing DUT is data; only
    a Verilator rejection raises `CompileError`, carrying the stderr.
    """
    work_dir = Path(tempfile.mkdtemp(prefix="flux-rtl-harness-"))
    dut_path = work_dir / "dut.sv"
    testbench_path = work_dir / "testbench.sv"
    vcd_path = work_dir / "trace.vcd"

    dut_path.write_text(_normalized(module_source))
    table = vector_table(spec)          # D865: a long vector list is read, not compiled
    if table is not None:
        (work_dir / "vectors.hex").write_text(table)
    testbench_path.write_text(generate_testbench_sv(
        spec, vcd_path=str(vcd_path), table_path=str(work_dir / "vectors.hex") if table is not None else None))

    extra_paths: list[Path] = []
    for stem, source in (extra_sources or {}).items():
        p = work_dir / f"{stem}.sv"
        p.write_text(_normalized(source))
        extra_paths.append(p)

    build_proc = subprocess.run(
        [
            "verilator", "--binary", "--build", "--timing", "--trace",
            "-Wall", "-Wno-DECLFILENAME", "-Wno-UNUSEDSIGNAL", "-Wno-UNUSEDPARAM", "-j", "1",   # D605: unused is style, not a defect
            str(testbench_path), str(dut_path), *[str(p) for p in extra_paths],
            "--top-module", "testbench",
        ],
        capture_output=True, text=True, cwd=work_dir, timeout=timeout_s,
    )
    if build_proc.returncode != 0:
        raise CompileError(build_proc.stderr, returncode=build_proc.returncode)

    sim_binary = work_dir / "obj_dir" / "Vtestbench"
    run_proc = subprocess.run(
        [str(sim_binary)], capture_output=True, text=True, cwd=work_dir, timeout=timeout_s,
    )
    stdout, stderr = run_proc.stdout, run_proc.stderr
    match = _RESULT_RE.search(stdout)
    total = int(match.group(2)) if match else 0
    passed = int(match.group(3)) if match else 0
    failing = tuple(m.group(0) for m in _VECTOR_FAIL_RE.finditer(stdout))
    # Ordered by the printed vector index so the tuple lines up with `spec.test_vectors` (D115).
    cycles = tuple(
        n for _, n in sorted(
            (int(m.group(1)), int(m.group(2))) for m in _CYCLES_RE.finditer(stdout)
        )
    )

    vcd_nonempty = vcd_path.exists() and vcd_path.stat().st_size > 0

    result = HarnessRunResult(
        compiled=True,
        compile_stderr=None,
        ran=match is not None,
        total_vectors=total,
        passed_vectors=passed,
        vcd_path=vcd_path if (keep_workdir and vcd_path.exists()) else None,
        vcd_nonempty=vcd_nonempty,
        stdout=stdout,
        stderr=stderr,
        failing_vector_lines=failing,
        cycles_per_vector=cycles,
    )

    if not keep_workdir:
        shutil.rmtree(work_dir, ignore_errors=True)

    return result


def run_bench(module_source: str, testbench_sv: str, *, files: dict[str, str] | None = None,
              timeout_s: float = 120.0) -> str:
    """A caller's own `testbench.sv` (top `testbench`) against `module_source`, same Verilator
    invocation as `compile_and_run` but no trace: the run's stdout. `files` are data files the
    bench reads ($readmemh), by name. A Verilator rejection raises `CompileError`."""
    work_dir = Path(tempfile.mkdtemp(prefix="flux-rtl-bench-"))
    try:
        (work_dir / "dut.sv").write_text(_normalized(module_source))
        (work_dir / "testbench.sv").write_text(testbench_sv)
        for name, text in (files or {}).items():
            (work_dir / name).write_text(text)
        build_proc = subprocess.run(
            ["verilator", "--binary", "--build", "--timing", "-Wall", "-Wno-DECLFILENAME", "-Wno-UNUSEDSIGNAL",
             "-Wno-UNUSEDPARAM", "-j", "1", "testbench.sv", "dut.sv", "--top-module", "testbench"],
            capture_output=True, text=True, cwd=work_dir, timeout=timeout_s,
        )
        if build_proc.returncode != 0:
            raise CompileError(build_proc.stderr, returncode=build_proc.returncode)
        run_proc = subprocess.run([str(work_dir / "obj_dir" / "Vtestbench")], capture_output=True, text=True,
                                  cwd=work_dir, timeout=timeout_s)
        return run_proc.stdout
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
