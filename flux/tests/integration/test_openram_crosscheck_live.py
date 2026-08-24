"""OpenRAM cross-check of the analytical CACTI path (docs/decisions.md D260).

OpenRAM is a real memory COMPILER: it generates an actual layout (GDS/LEF) and characterizes
it, where CACTI solves analytical equations. Both support 45nm (OpenRAM's freepdk45 vs
CACTI's native node), so the same geometry can be built twice and compared with no scaling
in between — the strongest available check on the base of this repo's memory-area chain.

What the comparison found and this test pins (measured, both tools, 256 words x 32 bits =
8192 bits at 45nm): OpenRAM's compiled macro is ~4.7x LARGER than CACTI's analytical estimate
(1.95 vs 0.42 um2/bit) while its access time is only ~1.4x slower (293 vs 209 ps). The area
gap is real and is about ARRAY EFFICIENCY, not the bitcell: CACTI's own reported efficiency
at this geometry is 70.7% (implying a ~0.30 um2 bitcell, a plausible 45nm figure), while a
generated layout with full periphery and conservative routing lands far below that at small
sizes. An academic compiler is not a commercial one, so the truth for a real 45nm macro sits
between the two — which is exactly why this is a BAND check, not an equality check.

Skips without openram-ram (nix develop .#physical). Runs ~1 min.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("openram-ram") is None,
    reason="needs openram-ram on PATH (nix develop .#physical)",
)

_WORDS, _WORD_BITS = 256, 32
_BITS = _WORDS * _WORD_BITS


def _run_openram(tmp_path: Path) -> tuple[float, float]:
    """(area_um2, access_ns) from a real OpenRAM freepdk45 compile with layout."""
    cfg = tmp_path / "cfg.py"
    cfg.write_text(
        f"word_size = {_WORD_BITS}\n"
        f"num_words = {_WORDS}\n"
        'num_banks = 1\n'
        'tech_name = "freepdk45"\n'
        'process_corners = ["TT"]\n'
        'supply_voltages = [1.1]\n'
        'temperatures = [25]\n'
        'route_supplies = False\n'
        'check_lvsdrc = False\n'
        'analytical_delay = True\n'
        f'output_path = "{tmp_path / "out"}"\n'
        'output_name = "sram"\n'
    )
    proc = subprocess.run(["openram-ram", str(cfg)], capture_output=True, text=True,
                          cwd=tmp_path, timeout=1800)
    assert proc.returncode == 0, proc.stdout[-2000:] + proc.stderr[-2000:]
    lib_path = next((tmp_path / "out").glob("*TT_1p1V*.lib"))
    lib = lib_path.read_text()
    area = float(re.search(r"^\s*area\s*:\s*([\d.]+)", lib, re.M).group(1))
    access = float(re.search(r'cell_rise\(CELL_TABLE\)\s*\{\s*values\("([\d.]+)', lib).group(1))
    return area, access


def _run_cacti() -> tuple[float, float, float]:
    """(area_um2, access_ns, efficiency) from real CACTI at the same node and geometry."""
    from flux_evaluator_cacti.adapter import CactiEvaluator, run_cacti
    from flux_evaluator_cacti.architecture_translator import architecture_ir_to_sram_spec
    from flux_evaluator_cacti.scaling import measure_area_efficiency

    arch = {
        "id": "crosscheck", "tech": {"node": "n45"},
        "hierarchy": [{"level": "gbuf", "class": "memory",
                       "attrs": {"size_kb": _BITS / 8 / 1024,
                                 "word_width_bits": _WORD_BITS}}],
    }
    binary = CactiEvaluator()._ensure_cacti_binary()
    result = run_cacti(architecture_ir_to_sram_spec(arch), technology_um=0.045,
                       cacti_path=str(binary))
    efficiency = measure_area_efficiency(arch, 0.045, cacti_path=str(binary))
    return result.height_mm * result.width_mm * 1e6, result.access_time_ns, efficiency


def test_a_real_memory_compiler_bounds_the_analytical_path_at_the_same_node(tmp_path):
    or_area, or_access = _run_openram(tmp_path)
    cacti_area, cacti_access, efficiency = _run_cacti()

    # both tools built the same 8192-bit macro; neither number is scaled
    assert or_area == pytest.approx(16004.0, rel=0.10)
    assert cacti_area == pytest.approx(3426.0, rel=0.10)
    assert or_access == pytest.approx(0.293, rel=0.10)
    assert cacti_access == pytest.approx(0.2087, rel=0.10)

    # the finding, pinned as a band: the compiled layout is several times larger than the
    # analytical estimate, while access agrees to within ~1.5x
    area_ratio = or_area / cacti_area
    access_ratio = or_access / cacti_access
    assert 3.5 < area_ratio < 6.0, f"area ratio moved to {area_ratio:.2f}"
    assert 1.2 < access_ratio < 1.8, f"access ratio moved to {access_ratio:.2f}"

    # and the gap is efficiency, not the bitcell: CACTI's own reported efficiency implies a
    # plausible 45nm 6T cell, so its optimism is in the periphery it assumes, not the array
    implied_bitcell_um2 = cacti_area / _BITS * efficiency
    assert 0.20 < implied_bitcell_um2 < 0.40, implied_bitcell_um2
    assert 0.60 < efficiency < 0.85
