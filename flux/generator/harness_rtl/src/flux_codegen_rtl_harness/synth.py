"""Generic gate-level synthesis via Yosys (D47).

No liberty file is used, so `synth -top <module>` maps to Yosys's generic primitives (`$_AND_`,
`$_XOR_`, ...). `total_cells` is a comparable logic-complexity signal, not a silicon area.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from .cache import ToolResultCache, content_key
from .sv_parse import module_headers

_STATS_HEADER_RE = re.compile(r"\d+\. Printing statistics\.")
_TOTAL_CELLS_RE = re.compile(r"^\s*(\d+)\s+cells$", re.MULTILINE)
_CELL_TYPE_RE = re.compile(r"^\s*(\d+)\s+(\$\S+)\s*$", re.MULTILINE)
_TRAILING_ENDMODULE_SEMICOLON_RE = re.compile(r"\bendmodule\s*;")


def _normalized(source: str) -> str:
    """Strip `endmodule;` (Verilator tolerates it, Yosys rejects it, D61) and ensure a trailing
    newline."""
    source = _TRAILING_ENDMODULE_SEMICOLON_RE.sub("endmodule", source)
    return source if source.endswith("\n") else source + "\n"


# Applied to the port header only (`module <name> #(...) ( ... );`): internal arrays in the body
# synthesise fine and must not be flagged.
_RANGE_RE = re.compile(r"\[[^\]]*\]")
# After collapsing every `[...]` to `@`, a scalar port reads `input logic signed @ name,` and an
# unpacked-array port reads `input logic signed @ name @,` — a range *after* the identifier.
_UNPACKED_RE = re.compile(r"@\s*(\w+)\s*@")


def unpacked_array_ports(source: str) -> list[str]:
    """Port names declared as unpacked arrays (`input logic [31:0] a [0:7]`).

    Yosys's frontend rejects these even with `-sv`, with an unhelpful syntax error (D127), so
    they are detected up front.
    """
    found: list[str] = []
    # Shared header scanner (D179): handles parameter defaults containing parentheses.
    for _module_name, header in module_headers(source):
        # `finditer`: several ports can share one line; report them all.
        found.extend(m.group(1) for m in _UNPACKED_RE.finditer(_RANGE_RE.sub("@", header)))
    return found


class SynthesisError(RuntimeError):
    """Yosys failed (bad syntax, unknown top module, ...). Carries stdout and stderr, since yosys
    reports most errors on stdout."""

    def __init__(self, stdout: str, stderr: str, *, returncode: int) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode
        super().__init__(f"yosys exited {returncode}:\n{(stderr or stdout)[-4000:]}")


class UnsupportedForSynthesisError(SynthesisError):
    """The design cannot go through Yosys at all; Yosys was never invoked (D127). A subclass so
    callers treating synthesis failure as an outcome still catch it."""

    def __init__(self, message: str) -> None:
        RuntimeError.__init__(self, message)
        self.stdout = ""
        self.stderr = ""
        self.returncode = 0


@dataclass(frozen=True)
class SynthesisResult:
    total_cells: int
    cells_by_type: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"total_cells": self.total_cells, "cells_by_type": dict(self.cells_by_type)}


def synthesize_and_measure(
    module_source: str,
    module_name: str,
    *,
    timeout_s: float = 60.0,
    extra_sources: dict[str, str] | None = None,
    cache: ToolResultCache | None = None,
) -> SynthesisResult:
    """Run Yosys generic synthesis (`read_verilog -sv; synth -top <module_name>; stat`) and parse
    the cell counts. `extra_sources` ({filename stem: source}, D52) supplies instantiated modules
    so the whole design is measured. Raises `SynthesisError` if Yosys fails (including timeout).

    `cache` (D89) is keyed on `(module_source, module_name, extra_sources)`; only successes are
    stored.
    """
    module_source = _normalized(module_source)

    if cache is not None:
        key = content_key(module_source, module_name, extra_sources or {})
        cached = cache.get(key)
        if cached is not None:
            return SynthesisResult(total_cells=cached["total_cells"], cells_by_type=cached["cells_by_type"])

    # try/finally so the work dir is removed on every path: success, SynthesisError, timeout.
    work_dir = Path(tempfile.mkdtemp(prefix="flux-rtl-synth-"))
    try:
        dut_path = work_dir / "dut.sv"
        dut_path.write_text(module_source)

        extra_paths: list[Path] = []
        for stem, source in (extra_sources or {}).items():
            p = work_dir / f"{stem}.sv"
            p.write_text(_normalized(source))
            extra_paths.append(p)

        _reject_unpacked_array_ports(module_source, extra_sources)
        read_cmd = " ".join(["read_verilog -sv", str(dut_path), *[str(p) for p in extra_paths]])
        try:
            proc = subprocess.run(
                ["yosys", "-p", f"{read_cmd}; synth -top {module_name}; stat"],
                capture_output=True, text=True, cwd=work_dir, timeout=timeout_s,
            )
        except subprocess.TimeoutExpired as exc:
            # a timeout is a Yosys failure and must surface as SynthesisError
            raise SynthesisError(
                (exc.stdout if isinstance(exc.stdout, str) else "") or "",
                f"yosys timed out after {timeout_s}s",
                returncode=-1,
            ) from exc
        if proc.returncode != 0:
            raise SynthesisError(proc.stdout, proc.stderr, returncode=proc.returncode)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

    # `synth` prints an intermediate stat block; parse after the trailing `stat` command's
    # "N. Printing statistics." header (N varies with hierarchy, D52).
    header_match = _STATS_HEADER_RE.search(proc.stdout)
    if not header_match:
        raise SynthesisError(proc.stdout, proc.stderr, returncode=0)
    final_section = proc.stdout[header_match.end():]

    # With submodules `stat` prints one "N cells" line per module, then the whole-design
    # aggregate last; the last match is the total in every case (D52).
    total_matches = list(_TOTAL_CELLS_RE.finditer(final_section))
    if not total_matches:
        raise SynthesisError(proc.stdout, proc.stderr, returncode=0)
    total_match = total_matches[-1]

    cells_by_type = {m.group(2): int(m.group(1)) for m in _CELL_TYPE_RE.finditer(final_section)}

    result = SynthesisResult(total_cells=int(total_match.group(1)), cells_by_type=cells_by_type)
    if cache is not None:
        cache.put(key, result.to_dict())
    return result


def _reject_unpacked_array_ports(
    module_source: str, extra_sources: dict[str, str] | None = None
) -> None:
    """Fail before invoking Yosys when any source has unpacked array ports (D127)."""
    for label, source in [("module_source", module_source), *sorted((extra_sources or {}).items())]:
        ports = unpacked_array_ports(source)
        if ports:
            raise UnsupportedForSynthesisError(
                f"{label} declares unpacked array port(s) {ports} — Yosys's Verilog frontend "
                "does not accept them (verified: it fails with `syntax error, unexpected '['`, "
                "with `-sv` already enabled). This design verifies in Verilator but cannot be "
                "synthesised through this path; use the flat-port form where one exists "
                "(`generate_tiled_wrapper(..., array_operands=False)`), or treat this shape as "
                "simulation-only."
            )
