"""The generated ASAP7 black-box SRAM macro (docs/decisions.md D254): geometry math, the
on-track pin discipline the real macro placer demands (MPL-0005 measured), and the
provenance-required rule — no live tools here (the physical suite runs the real flow)."""

from __future__ import annotations

import pytest
from flux_evaluator_openroad.sram_macro import generate_sram_macro


def _macro(**kw):
    args = dict(name="fakeram_test", size_kb=4, word_width_bits=64,
                area_mm2=0.000853, access_ns=0.5, provenance="test provenance sentence")
    args.update(kw)
    return generate_sram_macro(**args)


def test_geometry_and_interface_math():
    m = _macro()
    assert m.depth == 512  # 4KB x 8 / 64
    assert "input  wire [8:0] addr" in m.verilog_stub  # log2(512) = 9
    assert m.width_um == m.height_um == pytest.approx(29.206, abs=0.001)
    assert m.area_um2 == pytest.approx(853.0, rel=0.001)
    assert f"SIZE {m.width_um:.3f} BY {m.height_um:.3f}" in m.lef_text
    assert f"area : {m.area_um2:.3f}" in m.lib_text


def test_non_power_of_two_depth_is_refused():
    with pytest.raises(ValueError, match="power of two"):
        _macro(size_kb=3)


def test_pins_land_on_the_m4_track_grid():
    """Hier-RTLMP refuses pins it cannot align (measured, MPL-0005): every pin rect's center
    must sit on OFFSET + n*PITCH of the vendored tech LEF's M4 layer."""
    import re

    m = _macro()
    pitch, width, offset = 0.048, 0.024, 0.003
    rects = re.findall(r"RECT 0\.000 ([\d.]+) 0\.048 ([\d.]+) ;", m.lef_text)
    assert len(rects) == 3 + 9 + 64 + 64  # clk/we/ce + addr + din + dout
    for lo, hi in rects:
        lo, hi = float(lo), float(hi)
        assert hi - lo == pytest.approx(width, abs=1e-9)
        center = (lo + hi) / 2
        n = (center - offset) / pitch
        assert abs(n - round(n)) < 1e-6, f"pin center {center} off the M4 grid"


def test_a_macro_too_short_for_its_pins_is_refused():
    with pytest.raises(ValueError, match="too short for its pins"):
        _macro(area_mm2=1e-8)


def test_provenance_is_embedded_in_the_liberty():
    m = _macro()
    assert "test provenance sentence" in m.lib_text
    assert "Placement-grade, not" in m.lib_text  # the honesty banner


# --- optional byte-write-enable (wmask) ------------------------------------------------------
# Added because reacc's imem and scratchpad both take AXI byte strobes. Without a wmask pin a
# sub-word store becomes a read-modify-write, which on a 1RW macro costs a second cycle and
# changes the surrounding microarchitecture — a real design consequence, not a cosmetic one.


def test_no_wmask_pin_unless_asked_for():
    m = _macro()
    assert m.write_mask_bits == 0
    assert "wmask" not in m.verilog_stub
    assert "wmask" not in m.lef_text


def test_wmask_pin_has_one_bit_per_granule():
    m = _macro(write_granularity_bits=8)  # 64-bit word, byte writes
    assert m.write_mask_bits == 8
    assert "input  wire [7:0] wmask" in m.verilog_stub
    assert "PIN wmask" in m.lef_text


def test_wmask_granularity_may_be_any_divisor_of_the_word():
    assert _macro(write_granularity_bits=16).write_mask_bits == 4
    assert _macro(write_granularity_bits=64).write_mask_bits == 1


def test_a_granularity_that_does_not_divide_the_word_is_rejected():
    with pytest.raises(ValueError, match="positive divisor"):
        _macro(write_granularity_bits=24)


def test_a_nonpositive_granularity_is_rejected():
    with pytest.raises(ValueError, match="positive divisor"):
        _macro(write_granularity_bits=0)


def test_adding_a_wmask_does_not_silently_change_the_caller_supplied_area():
    plain, masked = _macro(), _macro(write_granularity_bits=8)
    assert masked.area_um2 == plain.area_um2
