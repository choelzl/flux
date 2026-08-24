"""ASIC synthesis against ASAP7 (D92): a PDK-derived physical area, unlike
`synth.synthesize_and_measure`'s generic-cell logic-complexity signal.

`asap7_pdk/` vendors the BSD-3-Clause ASAP7 liberty library (see its `PROVENANCE.md`) for
`abc -liberty` technology mapping and a physically meaningful `area_um2` -- an academic,
predictive 7nm PDK rather than a foundry one. Reuses `synth.py`'s `_normalized()` (e.g. a
trailing `endmodule;` Yosys rejects).
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
# A single-module design prints "Chip area for module '\Name'"; a hierarchical design's
# whole-design aggregate (under "=== design hierarchy ===", always the final block) prints
# "Chip area for top module '\Name'" instead.
_CHIP_AREA_RE = re.compile(r"Chip area for (?:top )?module '\\?(\S+?)':\s+([\d.]+)")
_SEQUENTIAL_AREA_RE = re.compile(r"of which used for sequential elements:\s+([\d.]+)\s+\(([\d.]+)%\)")
_CELLS_SUMMARY_RE = re.compile(r"^\s*\d+\s+[\d.]+\s+cells\s*$", re.MULTILINE)
_SUBMODULES_SUMMARY_RE = re.compile(r"^\s*\d+\s+[\d.]+\s+submodules\s*$", re.MULTILINE)
_CELL_AREA_RE = re.compile(r"^\s*(\d+)\s+([\d.]+)\s+(\S+)\s*$", re.MULTILINE)


class Asap7NotAvailableError(RuntimeError):
    """The vendored ASAP7 liberty library is missing from this install: a packaging bug (it
    ships as package data, see `codegen/rtl_harness/pyproject.toml`), not a runtime condition.
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
    """The public, policy-checked entry point: refuses (`ConfidentialPdkError`) if the PDK is
    registered confidential, before any synthesis runs. The check lives here in the engine, so
    importing this function is no way around it.

    See `_synthesize_with_asap7_unchecked` for the synthesis contract.
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
    """ASIC synthesis against the vendored ASAP7 liberty library (`read_verilog -sv; synth -top
    <module_name>; dfflibmap -liberty ...; abc -liberty ...; stat -liberty ...`), giving a
    physical `area_um2`, not a gate count. `extra_sources` (as in
    `synth.synthesize_and_measure`) are leaf modules read alongside the top-level source, so
    `area_um2` covers the whole design.

    `cache` (D89/D92), if given, is checked first under a content-hash key over
    `(module_source, module_name, extra_sources)` -- the same `ToolResultCache` as
    `synth.synthesize_and_measure`. Only a success is cached.

    Raises `SynthesisError` if Yosys/ABC fails, `Asap7NotAvailableError` if the vendored
    liberty file is missing.
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

    # try/finally so the work dir (a ~4.1 MB decompressed liberty copy per call) is removed on
    # every path: success, SynthesisError and timeout.
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
            # A timeout is a Yosys/ABC failure and must surface as SynthesisError (callers such
            # as flux_rtl_generate_dse catch only that).
            raise SynthesisError(
                (exc.stdout if isinstance(exc.stdout, str) else "") or "",
                f"yosys timed out after {timeout_s}s",
                returncode=-1,
            ) from exc
        if proc.returncode != 0:
            raise SynthesisError(proc.stdout, proc.stderr, returncode=proc.returncode)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

    # A design with submodule hierarchy makes ABC/`stat` run more than once (D52): only the last
    # "Printing statistics" step, and in it the last "Chip area for module" match, is the
    # whole-design aggregate.
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

    # The per-cell-type breakdown lies between the "N.NNN cells" summary line and either the
    # "N.NNN submodules" line (a hierarchical design lists per-submodule areas after its leaf
    # cells; submodule names are not cell types) or the "Chip area" line (a flat design).
    # Checked against real Yosys output for a flat and a two-instance composite design.
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
