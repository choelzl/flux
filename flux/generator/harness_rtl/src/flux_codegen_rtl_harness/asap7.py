"""Real ASIC synthesis against ASAP7 (docs/decisions.md D92) — a real, PDK-derived physical area,
not `synth.synthesize_and_measure`'s own generic-cell logic-complexity signal (that module's own
docstring: "No standard-cell technology library (PDK/liberty file) is wired in ... not real
transistors in any real process node"). This closes that real gap: `asap7_pdk/`'s own vendored,
real, BSD-3-Clause-licensed ASAP7 liberty library (see that directory's own `PROVENANCE.md` for
exact source/license/merge process) gives real `abc -liberty` technology mapping and a real,
physically meaningful `area_um2` — an academic/predictive 7nm PDK, not a real foundry's production
one (which would need a paid NDA this repo doesn't have), but real, checked-sufficient standard-
cell data, not a placeholder.

Shares `synth.py`'s own `_normalized()` real defensiveness (an LLM-generated `endmodule;` trailing
semicolon Yosys's stricter frontend rejects) rather than duplicating it — the same real fix,
reused, not re-derived.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from flux_redaction import require_not_confidential

from .cache import ToolResultCache, content_key
from .synth import SynthesisError, _normalized, _reject_unpacked_array_ports

_PDK_NAME = "asap7"

_ASAP7_LIBERTY_GZ = Path(__file__).resolve().parent / "asap7_pdk" / "asap7sc7p5t_simple_invbuf_seq_rvt_tt.lib.gz"

_STATS_HEADER_RE = re.compile(r"\d+\. Printing statistics\.")
# A real, checked-against-actual-output difference (not assumed): a single-module design prints
# "Chip area for module '\Name'"; a real hierarchical design's own whole-design aggregate (under
# "=== design hierarchy ===", always the real, final block) prints "Chip area for *top* module
# '\Name'" instead — the same real "top"-prefixed line D52's own generic-cell parser never needed
# to distinguish, since `_TOTAL_CELLS_RE` there matches a bare "N cells" count with no such label.
_CHIP_AREA_RE = re.compile(r"Chip area for (?:top )?module '\\?(\S+?)':\s+([\d.]+)")
_SEQUENTIAL_AREA_RE = re.compile(r"of which used for sequential elements:\s+([\d.]+)\s+\(([\d.]+)%\)")
_CELLS_SUMMARY_RE = re.compile(r"^\s*\d+\s+[\d.]+\s+cells\s*$", re.MULTILINE)
_SUBMODULES_SUMMARY_RE = re.compile(r"^\s*\d+\s+[\d.]+\s+submodules\s*$", re.MULTILINE)
_CELL_AREA_RE = re.compile(r"^\s*(\d+)\s+([\d.]+)\s+(\S+)\s*$", re.MULTILINE)


class Asap7NotAvailableError(RuntimeError):
    """The vendored ASAP7 liberty library is missing from this install — a real packaging bug
    (it ships as package data, see `codegen/rtl_harness/pyproject.toml`), not a normal runtime
    condition to design around.
    """


@dataclass(frozen=True)
class Asap7SynthesisResult:
    area_um2: float
    sequential_area_um2: float
    cells_by_type: dict[str, tuple[int, float]] = field(default_factory=dict)  # name -> (count, area_um2)

    @property
    def sequential_fraction(self) -> float:
        return (self.sequential_area_um2 / self.area_um2) if self.area_um2 > 0 else 0.0

    def to_dict(self) -> dict:
        return {
            "area_um2": self.area_um2,
            "sequential_area_um2": self.sequential_area_um2,
            "sequential_fraction": self.sequential_fraction,
            "cells_by_type": {name: {"count": c, "area_um2": a} for name, (c, a) in self.cells_by_type.items()},
        }


def _extract_liberty(work_dir: Path) -> Path:
    if not _ASAP7_LIBERTY_GZ.is_file():
        raise Asap7NotAvailableError(f"vendored ASAP7 liberty file missing at {_ASAP7_LIBERTY_GZ}")
    import gzip

    liberty_path = work_dir / "asap7.lib"
    with gzip.open(_ASAP7_LIBERTY_GZ, "rt") as src, liberty_path.open("w") as dst:
        dst.write(src.read())
    return liberty_path


def synthesize_with_asap7(
    module_source: str,
    module_name: str,
    *,
    timeout_s: float = 60.0,
    extra_sources: dict[str, str] | None = None,
    cache: ToolResultCache | None = None,
) -> Asap7SynthesisResult:
    """The public, policy-checked entry point: refuses outright (`ConfidentialPdkError`) if the
    PDK is registered confidential, *before* any real synthesis runs — enforcement lives here in
    the engine itself, not only in the CHIA-node wrapper, so importing this function directly
    is no longer a way around the check (a real review finding: the earlier docstring claimed
    "structural refusal" while the engine was unguarded). The redaction layer's own internal
    path uses `_synthesize_with_asap7_unchecked` below — underscore-private, for computing
    deltas that never leave unredacted — and that is the only sanctioned bypass.

    See `_synthesize_with_asap7_unchecked` for the actual synthesis contract.
    """
    require_not_confidential(_PDK_NAME)
    return _synthesize_with_asap7_unchecked(
        module_source, module_name, timeout_s=timeout_s, extra_sources=extra_sources, cache=cache,
    )


def _synthesize_with_asap7_unchecked(
    module_source: str,
    module_name: str,
    *,
    timeout_s: float = 60.0,
    extra_sources: dict[str, str] | None = None,
    cache: ToolResultCache | None = None,
) -> Asap7SynthesisResult:
    """Real ASIC synthesis against the real, vendored ASAP7 liberty library
    (`read_verilog -sv; synth -top <module_name>; dfflibmap -liberty ...; abc -liberty ...; stat
    -liberty ...`) — real `area_um2` (a genuine physical quantity, ASAP7's own real 7nm predictive
    process), not a generic gate count. `extra_sources` (matching `synth.synthesize_and_measure`'s
    own parameter) lets real leaf modules a composite instantiates be read alongside the top-level
    source, so `area_um2` reflects the *whole* real design.

    `cache` (docs/decisions.md D89/D92), if given, is checked first against a real content-hash
    key over `(module_source, module_name, extra_sources)` — the same real mechanism
    `synth.synthesize_and_measure` already uses, a second real consumer of the same
    `ToolResultCache`, not a parallel one. Only a real success is ever cached.

    Raises `SynthesisError` if Yosys/ABC itself fails, `Asap7NotAvailableError` if the vendored
    liberty file is missing from this install (a packaging bug, not a normal failure mode).
    """
    module_source = _normalized(module_source)

    if cache is not None:
        key = content_key("asap7", module_source, module_name, extra_sources or {})
        cached = cache.get(key)
        if cached is not None:
            return Asap7SynthesisResult(
                area_um2=cached["area_um2"], sequential_area_um2=cached["sequential_area_um2"],
                cells_by_type={n: (v["count"], v["area_um2"]) for n, v in cached["cells_by_type"].items()},
            )

    # try/finally so the work dir (holding a real ~4.1 MB decompressed liberty copy per call) is
    # removed on every path — success, SynthesisError, and timeout alike. `build.compile_and_run`
    # already cleaned up; the two synthesis paths originally didn't, a real leak found in review
    # (a 50-variant DSE sweep left ~205 MB of orphan /tmp/flux-asap7-synth-* dirs).
    work_dir = Path(tempfile.mkdtemp(prefix="flux-asap7-synth-"))
    try:
        liberty_path = _extract_liberty(work_dir)

        dut_path = work_dir / "dut.sv"
        dut_path.write_text(module_source)

        extra_paths: list[Path] = []
        for stem, source in (extra_sources or {}).items():
            p = work_dir / f"{stem}.sv"
            p.write_text(_normalized(source))
            extra_paths.append(p)

        _reject_unpacked_array_ports(module_source, extra_sources)
        read_cmd = " ".join(["read_verilog -sv", str(dut_path), *[str(p) for p in extra_paths]])
        script = (
            f"{read_cmd}; synth -top {module_name}; "
            f"dfflibmap -liberty {liberty_path}; abc -liberty {liberty_path}; stat -liberty {liberty_path}"
        )
        try:
            proc = subprocess.run(
                ["yosys", "-p", script], capture_output=True, text=True, cwd=work_dir, timeout=timeout_s,
            )
        except subprocess.TimeoutExpired as exc:
            # The documented contract is "SynthesisError if Yosys/ABC itself fails" — a timeout
            # is exactly that, and must not escape as a different type (flux_rtl_generate_dse
            # catches only SynthesisError; a bare TimeoutExpired aborted its whole report).
            raise SynthesisError(
                (exc.stdout if isinstance(exc.stdout, str) else "") or "",
                f"yosys timed out after {timeout_s}s",
                returncode=-1,
            ) from exc
        if proc.returncode != 0:
            raise SynthesisError(proc.stdout, proc.stderr, returncode=proc.returncode)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

    # Same real defensiveness D52 established for the generic path: a design with real submodule
    # hierarchy makes ABC/`stat` run more than once, shifting the real step number and printing
    # more than one "Chip area for module" section — only the *last* "Printing statistics" step,
    # and within it the *last* "Chip area for module" match, is the real, whole-design aggregate.
    header_matches = list(_STATS_HEADER_RE.finditer(proc.stdout))
    if not header_matches:
        raise SynthesisError(proc.stdout, proc.stderr, returncode=0)
    final_section = proc.stdout[header_matches[-1].end():]

    chip_area_matches = list(_CHIP_AREA_RE.finditer(final_section))
    if not chip_area_matches:
        raise SynthesisError(proc.stdout, proc.stderr, returncode=0)
    area_um2 = float(chip_area_matches[-1].group(2))

    seq_matches = list(_SEQUENTIAL_AREA_RE.finditer(final_section))
    sequential_area_um2 = float(seq_matches[-1].group(1)) if seq_matches else 0.0

    # The real per-cell-type breakdown lives between its own "N.NNN cells" summary line and either
    # a real "N.NNN submodules" summary line (a hierarchical design's own real aggregate block
    # lists its own real leaf cell types, then separately its own real per-submodule area
    # contributions — a submodule's own name is not a real leaf cell type, and must not be
    # counted as one) or the "Chip area" line itself (a flat, non-hierarchical design has no
    # "submodules" line at all) — real, checked directly against actual Yosys output for both a
    # flat design (`Adder2`) and a real two-instance composite (`Adder3`) before trusting this,
    # not guessed from the single-module case alone.
    before_area = final_section[:chip_area_matches[-1].start()]
    cells_summary_matches = list(_CELLS_SUMMARY_RE.finditer(before_area))
    cell_section = before_area[cells_summary_matches[-1].end():] if cells_summary_matches else ""
    submodules_summary_match = _SUBMODULES_SUMMARY_RE.search(cell_section)
    if submodules_summary_match is not None:
        cell_section = cell_section[:submodules_summary_match.start()]

    cells_by_type: dict[str, tuple[int, float]] = {}
    for m in _CELL_AREA_RE.finditer(cell_section):
        count, area, name = int(m.group(1)), float(m.group(2)), m.group(3)
        cells_by_type[name] = (count, area)

    result = Asap7SynthesisResult(
        area_um2=area_um2, sequential_area_um2=sequential_area_um2, cells_by_type=cells_by_type,
    )
    if cache is not None:
        cache.put(key, result.to_dict())
    return result
