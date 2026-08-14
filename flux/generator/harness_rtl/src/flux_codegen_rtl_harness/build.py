"""Compiles a DUT module source against a deterministically-generated testbench and runs it
through real Verilator (docs/decisions.md D43) — the same real invocation
`evaluators/rtl` already uses successfully (`--binary --build --timing ... -j 1`, including the
real Verilator 5.020 threading-bug workaround `evaluators/rtl/adapter.py` already found and
documented: `-j 1`, not `-j 0`, combined with `--timing`).

The DUT source is written to `dut.sv` (containing only the `module ... endmodule` — no testbench,
no `initial` block driving it) so the harness's own generated `testbench.sv` can instantiate it
without a name clash. `generate_rtl_module` (flows/chia_nodes) is responsible for prompting the
LLM to emit exactly that shape.

`extra_sources` (docs/decisions.md D48) lets `dut.sv` reference other, already-verified modules —
`compose.py` uses this to compile a deterministically-generated top-level composite alongside the
real, previously-verified leaf modules it instantiates, without re-deriving or duplicating any of
their source.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .driver_gen import generate_testbench_sv
from .errors import CompileError
from flux_codegen_systemc_harness import DesignSpec

_RESULT_RE = re.compile(r"^RESULT (PASS|FAIL) vectors=(\d+) passed=(\d+)", re.MULTILINE)
_VECTOR_FAIL_RE = re.compile(r"^VECTOR (\d+) FAIL .*$", re.MULTILINE)
_CYCLES_RE = re.compile(r"^CYCLES vector=(\d+) n=(\d+)", re.MULTILINE)


@dataclass(frozen=True)
class HarnessRunResult:
    compiled: bool
    compile_stderr: str | None
    ran: bool
    total_vectors: int
    passed_vectors: int
    vcd_path: Path | None
    vcd_nonempty: bool
    stdout: str
    stderr: str
    failing_vector_lines: tuple[str, ...]
    # Measured cycles per test vector, in vector order — populated only for a
    # `measures_latency` spec (docs/decisions.md D115), empty otherwise. This is the harness's
    # first *quantitative* output: every other field is pass/fail. It exists so a generated
    # design can eventually serve as a latency reference, not just a correctness one.
    cycles_per_vector: tuple[int, ...] = ()

    @property
    def total_cycles(self) -> int | None:
        """Summed measured latency across vectors, or `None` when latency wasn't measured —
        `None` rather than 0, since 0 is a legitimate measurement and "not measured" is not."""
        return sum(self.cycles_per_vector) if self.cycles_per_vector else None

    @property
    def all_passed(self) -> bool:
        return self.compiled and self.ran and self.total_vectors > 0 and self.passed_vectors == self.total_vectors

    def to_dict(self) -> dict:
        return {
            "compiled": self.compiled,
            "compile_stderr": self.compile_stderr,
            "ran": self.ran,
            "total_vectors": self.total_vectors,
            "passed_vectors": self.passed_vectors,
            "vcd_path": str(self.vcd_path) if self.vcd_path else None,
            "vcd_nonempty": self.vcd_nonempty,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "failing_vector_lines": list(self.failing_vector_lines),
            "all_passed": self.all_passed,
            "cycles_per_vector": list(self.cycles_per_vector),
            "total_cycles": self.total_cycles,
        }


_TRAILING_ENDMODULE_SEMICOLON_RE = re.compile(r"\bendmodule\s*;")


def _normalized(source: str) -> str:
    # Two cosmetic quirks that reject otherwise-correct LLM-generated source: Verilator treats a
    # missing trailing newline as a hard error (EOFNEWLINE), and Yosys's stricter frontend rejects
    # `endmodule;` (Verilator tolerates it). Both normalized here so a module behaves identically
    # through either harness (D61).
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
    any `extra_sources` — {filename stem: source}, e.g. previously-verified leaf modules a
    composite `dut.sv` instantiates, docs/decisions.md D48) with real Verilator, run the binary,
    and parse its `RESULT ...` line plus per-vector `VECTOR N FAIL ...` diagnostics. Never raises
    on a *failing* DUT — only raises `CompileError` when Verilator itself rejects the source,
    since a caller needs the real stderr to act on.
    """
    work_dir = Path(tempfile.mkdtemp(prefix="flux-rtl-harness-"))
    dut_path = work_dir / "dut.sv"
    testbench_path = work_dir / "testbench.sv"
    vcd_path = work_dir / "trace.vcd"

    dut_path.write_text(_normalized(module_source))
    testbench_path.write_text(generate_testbench_sv(spec, vcd_path=str(vcd_path)))

    extra_paths: list[Path] = []
    for stem, source in (extra_sources or {}).items():
        p = work_dir / f"{stem}.sv"
        p.write_text(_normalized(source))
        extra_paths.append(p)

    build_proc = subprocess.run(
        [
            "verilator", "--binary", "--build", "--timing", "--trace",
            "-Wall", "-Wno-DECLFILENAME", "-Wno-UNUSEDSIGNAL", "-j", "1",
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
    # Ordered by the vector index the testbench printed, not by appearance, so the tuple lines up
    # with `spec.test_vectors` even if output interleaves (D115).
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
