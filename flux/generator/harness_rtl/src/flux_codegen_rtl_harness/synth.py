"""Real gate-level synthesis via Yosys (docs/decisions.md D47) — the first use of Yosys anywhere
in this repo (it's been a cherry-picked `.#default` nix package since `evaluators/rtl` needed
Verilator, per `flake.nix`'s own note anticipating "when EDA-tool needs grow past what's cherry-
picked here" — this is that growth).

**A real cell count, not a physical area estimate — named honestly, not oversold.** No standard-
cell technology library (PDK/liberty file) is wired in, so `synth -top <module>` maps to Yosys's
own generic internal gate primitives (`$_AND_`, `$_XOR_`, ...), not real transistors in any real
process node. `total_cells` is a genuine, comparable *logic-complexity* signal — two
functionally-equivalent implementations of the same DUT really do synthesize to different gate
counts, checkable and meaningful — but it is not `evaluators/cacti`'s `area_mm2` or anything
calibratable against real silicon without a real PDK, a distinct, larger, not-started piece of
work.

Verilog/RTL only — Yosys reads Verilog, not SystemC, so this capability doesn't have a
`codegen/systemc_harness` sibling (a real, structural asymmetry between the two generation paths,
not an oversight).
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
    """Real, found defensiveness (docs/decisions.md D61): a real LLM-generated module used
    `endmodule;` — a trailing semicolon Verilator silently tolerates (this composite's own
    `PcUnit` leaf compiled and ran correctly through `build.py`'s real Verilator path) but Yosys's
    own, stricter Verilog frontend rejects outright (`ERROR: syntax error, unexpected ';'`) — a
    real tool-compatibility gap only surfaced once real Yosys synthesis was run against a design
    containing it, the same "combining previously-independent pieces surfaces real bugs" pattern
    this whole framework's development has repeatedly found. Same real EOFNEWLINE-class
    defensiveness as `build.py`'s own `_normalized()` (a genuinely separate quirk, not shared code
    — this package's own precedent already duplicates this class of check per file, not shared).
    """
    source = _TRAILING_ENDMODULE_SEMICOLON_RE.sub("endmodule", source)
    return source if source.endswith("\n") else source + "\n"


# The port header only: `module <name> #(...) ( ... );`. Scanning whole lines instead missed
# ports declared on the same line as `module M (`, and scanning the whole body would flag
# internal arrays (`logic [31:0] mem [0:7];`), which synthesise perfectly well.
_RANGE_RE = re.compile(r"\[[^\]]*\]")
# After collapsing every `[...]` to `@`, a scalar port reads `input logic signed @ name,` and an
# unpacked-array port reads `input logic signed @ name @,` — a range *after* the identifier.
_UNPACKED_RE = re.compile(r"@\s*(\w+)\s*@")


def unpacked_array_ports(source: str) -> list[str]:
    """Port names declared as unpacked arrays (`input logic [31:0] a [0:7]`).

    Yosys's Verilog frontend rejects these outright, `-sv` included — checked, not assumed: it
    reports `syntax error, unexpected '['` pointing into generated code the caller never wrote
    (docs/decisions.md D127). Detected here so the failure names the real limitation instead.
    """
    found: list[str] = []
    # Shared scanner (docs/decisions.md D179): the regex this used to carry could not survive a
    # parameter default containing parentheses, and returned no ports at all when it hit one —
    # so this guard silently passed and Yosys failed later with the very error it exists to
    # pre-empt.
    for _module_name, header in module_headers(source):
        # `finditer`, not `search`: several ports can share one line, and reporting only the
        # first would still reject the design correctly but name it incompletely — which sends
        # the reader to fix one port and hit the same wall on the next.
        found.extend(m.group(1) for m in _UNPACKED_RE.finditer(_RANGE_RE.sub("@", header)))
    return found


class SynthesisError(RuntimeError):
    """Real Yosys failure (bad syntax, unknown top module, ...). Carries the real stderr/stdout
    so a caller can act on it — same shape as `CompileError` elsewhere in this repo's harnesses,
    but yosys reports most real errors on stdout, not stderr, so both are captured."""

    def __init__(self, stdout: str, stderr: str, *, returncode: int) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode
        super().__init__(f"yosys exited {returncode}:\n{(stderr or stdout)[-4000:]}")


class UnsupportedForSynthesisError(SynthesisError):
    """The design is real and verified, but cannot go through Yosys at all — as opposed to Yosys
    running and rejecting it (docs/decisions.md D127). A subclass so existing callers that already
    treat synthesis failure as a recorded outcome keep working, with its own message because
    "yosys exited 0" would be a lie: yosys is never invoked."""

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
    """Run real Yosys generic synthesis (`read_verilog -sv; synth -top <module_name>; stat`) on
    `module_source` and parse the real cell counts from its own `stat` output. `extra_sources`
    (docs/decisions.md D52) — {filename stem: source} — lets Yosys read other modules
    `module_source` instantiates (e.g. a composite's own leaf modules, mirroring `build.py`'s
    identical parameter for Verilator) so `synth -top` measures the *whole* design, not just the
    top-level wrapper. Raises `SynthesisError` if Yosys itself fails — never fabricates a result.

    `cache` (docs/decisions.md D89), if given, is checked first: a real, deterministic
    content-hash key over `(module_source, module_name, extra_sources)` — exactly what Yosys
    itself reads, nothing broader — and a real cache hit skips the real Yosys subprocess call
    entirely. `compose.synthesize_composite` delegates to this same function, so composite
    synthesis gets real caching for free with no separate key derivation. Only a real *success* is
    ever cached — a `SynthesisError` is never stored, so a transient failure doesn't poison future
    calls with the same inputs. Omit `cache` (the default) for the original always-real-synthesis
    behavior — additive, not a behavior change for existing callers.
    """
    module_source = _normalized(module_source)

    if cache is not None:
        key = content_key(module_source, module_name, extra_sources or {})
        cached = cache.get(key)
        if cached is not None:
            return SynthesisResult(total_cells=cached["total_cells"], cells_by_type=cached["cells_by_type"])

    # try/finally so the work dir is removed on every path — success, SynthesisError, timeout.
    # `build.compile_and_run` always cleaned up; this path originally didn't (review finding).
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
            # "Raises SynthesisError if Yosys itself fails" is the documented contract — a
            # timeout is such a failure and must not escape as a different exception type.
            raise SynthesisError(
                (exc.stdout if isinstance(exc.stdout, str) else "") or "",
                f"yosys timed out after {timeout_s}s",
                returncode=-1,
            ) from exc
        if proc.returncode != 0:
            raise SynthesisError(proc.stdout, proc.stderr, returncode=proc.returncode)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

    # `synth` prints its own intermediate stat block mid-flow; only the explicit trailing `stat`
    # command's output (after "N. Printing statistics.", the real step number, not hardcoded — a
    # real bug found via D52: composites trigger extra synthesis passes, so this is "3." for a
    # single module but "4."+ once real hierarchy/submodules are involved) is the final,
    # fully-synthesized count — parsing the first occurrence would risk a pre-optimization number.
    header_match = _STATS_HEADER_RE.search(proc.stdout)
    if not header_match:
        raise SynthesisError(proc.stdout, proc.stderr, returncode=0)
    final_section = proc.stdout[header_match.end():]

    # A second real bug found via D52: with real submodules, Yosys's `stat` prints one "N cells"
    # line per module *plus* a final aggregate line under "=== design hierarchy ===" — always
    # last in the output. Taking the *first* match (the old behavior) silently returned one
    # submodule's local count instead of the whole design's — the *last* match is always the
    # real, whole-design aggregate, for both hierarchical and single-module designs alike (a
    # single module has exactly one "N cells" line, so "last" and "only" coincide there).
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
    """Fail before invoking Yosys, with the real cause named (docs/decisions.md D127).

    The array-port designs (D120's wide operand vectors, D121's GEMM memories) are real, verified
    designs — they simply cannot reach this tool. Saying so beats a `syntax error, unexpected '['`
    at a line number in a temp file.
    """
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
