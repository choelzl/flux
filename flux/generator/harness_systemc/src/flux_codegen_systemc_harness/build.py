"""Compiles a DUT module source against a deterministically-generated driver and runs it — the
real g++/SystemC invocation (docs/decisions.md D39), same `g++ -std=c++17 -O2 ... -lsystemc -lm`
recipe `evaluators/systemc` already uses successfully in this environment.

The DUT source is written to `dut.h` (a header, `#pragma once`-guarded, containing only the
`SC_MODULE` — no `sc_main`, no other definition of the driver's own `int main`/`sc_main`
entrypoint) so the harness's own generated `driver.cpp` can `#include` it without a duplicate-
symbol clash. `generate_systemc_module` (flows/chia_nodes) is responsible for prompting the LLM
to emit exactly that shape and for stripping markdown fences — a real, confirmed necessity (see
docs/decisions.md D40): the local coder model wraps its own output in \\`\\`\\`cpp fences by
default even when told not to.

`extra_sources` (docs/decisions.md D55, the SystemC sibling of D48's RTL-side addition) lets
`dut.h` `#include` other, already-verified leaf modules as their own pragma-once headers —
`compose.py` uses this to compile a deterministically-generated composite `dut.h` alongside the
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

from .driver_gen import generate_driver_cpp
from .errors import CompileError
from .spec import DesignSpec

_RESULT_RE = re.compile(r"^RESULT (PASS|FAIL) vectors=(\d+) passed=(\d+)", re.MULTILINE)
_VECTOR_FAIL_RE = re.compile(r"^VECTOR (\d+) FAIL .*$", re.MULTILINE)


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

    @property
    def all_passed(self) -> bool:
        return self.compiled and self.ran and self.total_vectors > 0 and self.passed_vectors == self.total_vectors

    def to_dict(self) -> dict:
        """JSON-safe (MCP tool return values must be JSON-safe — the same real gotcha
        `ArchitectureDSEReport`/`ConformanceReport` needed `to_dict()` for, docs/decisions.md
        D7): `vcd_path` (a `Path | None`) becomes a plain string or `None`."""
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
        }


def compile_and_run(
    module_source: str,
    spec: DesignSpec,
    *,
    timeout_s: float = 60.0,
    keep_workdir: bool = False,
    extra_sources: dict[str, str] | None = None,
) -> HarnessRunResult:
    """Write `module_source` as `dut.h`, generate `driver.cpp` from `spec`, compile both (plus any
    `extra_sources` — {module name: source}, e.g. previously-verified leaf modules a composite
    `dut.h` instantiates, docs/decisions.md D55) with a real g++, run the binary, and parse its
    `RESULT ...` line plus per-vector `VECTOR N FAIL ...` diagnostics. Never raises on a *failing*
    DUT (bad test results are real, informative data, not this harness's failure) — only raises
    `CompileError` when g++ itself rejects the source, since a caller (e.g. a generate-repair
    loop) needs the real compiler stderr to act on.
    """
    work_dir = Path(tempfile.mkdtemp(prefix="flux-systemc-harness-"))
    dut_path = work_dir / "dut.h"
    driver_path = work_dir / "driver.cpp"
    binary_path = work_dir / "sim"
    vcd_stem = str(work_dir / "trace")
    vcd_path = Path(vcd_stem + ".vcd")

    for name, source in (extra_sources or {}).items():
        (work_dir / f"{name}.h").write_text(f"#pragma once\n#include <systemc.h>\n\n{source}\n")

    dut_path.write_text(f"#pragma once\n#include <systemc.h>\n\n{module_source}\n")
    driver_path.write_text(generate_driver_cpp(spec, vcd_stem=vcd_stem))

    build_proc = subprocess.run(
        ["g++", "-std=c++17", "-O2", "-I", str(work_dir), "-o", str(binary_path), str(driver_path), "-lsystemc", "-lm"],
        capture_output=True, text=True, timeout=timeout_s,
    )
    if build_proc.returncode != 0:
        raise CompileError(build_proc.stderr, returncode=build_proc.returncode)

    run_proc = subprocess.run(
        [str(binary_path)], capture_output=True, text=True, timeout=timeout_s, cwd=work_dir,
    )
    stdout, stderr = run_proc.stdout, run_proc.stderr
    match = _RESULT_RE.search(stdout)
    total = int(match.group(2)) if match else 0
    passed = int(match.group(3)) if match else 0
    failing = tuple(m.group(0) for m in _VECTOR_FAIL_RE.finditer(stdout))

    vcd_nonempty = vcd_path.exists() and vcd_path.stat().st_size > 0

    result = HarnessRunResult(
        compiled=True,
        compile_stderr=None,
        ran=match is not None,
        total_vectors=total,
        passed_vectors=passed,
        # `vcd_path` is only a live, valid path when the caller asked to keep the workdir — a
        # deleted-but-non-None Path would silently lie about being openable.
        vcd_path=vcd_path if (keep_workdir and vcd_path.exists()) else None,
        vcd_nonempty=vcd_nonempty,
        stdout=stdout,
        stderr=stderr,
        failing_vector_lines=failing,
    )

    if not keep_workdir:
        shutil.rmtree(work_dir, ignore_errors=True)

    return result
