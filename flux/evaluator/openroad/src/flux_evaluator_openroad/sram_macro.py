"""Black-box SRAM macros for the ASAP7 OpenROAD flow (docs/decisions.md D254).

ASAP7 ships no public SRAM macros; the standard workaround in open flows (e.g.
OpenROAD-flow-scripts' use of bsg_fakeram, BSD-3) is a GENERATED black-box macro: a LEF with
a real footprint, a liberty with area/pins, and a Verilog stub — the physical flow then
places the macro next to the standard cells and every downstream area number includes it.

This module is that generator, with this repo's provenance discipline instead of a bundled
model: the AREA comes from the caller — in practice real CACTI at a native node scaled by the
published Stillmaker & Baas factor (D253) — and the numbers' origin is embedded verbatim in
the generated liberty as comments. Timing is a single conservative constant the caller
supplies (in practice CACTI@32nm access time, UNSCALED — pessimistic at 7nm, stated); this is
a placement-grade macro for area-accurate floorplans, not a sign-off model, and the liberty
says so.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class SramMacro:
    name: str
    word_width_bits: int
    depth: int
    width_um: float
    height_um: float
    lef_text: str
    lib_text: str
    verilog_stub: str
    write_mask_bits: int = 0  # 0 = no wmask pin; else one bit per write-granule

    @property
    def area_um2(self) -> float:
        return self.width_um * self.height_um


def generate_sram_macro(
    name: str,
    *,
    size_kb: float,
    word_width_bits: int,
    area_mm2: float,
    access_ns: float,
    provenance: str,
    ports: dict[str, int] | None = None,
    write_granularity_bits: int | None = None,
) -> SramMacro:
    """A 1RW synchronous SRAM black box sized to `area_mm2` (square footprint, 1nm grid).
    `provenance` is REQUIRED and embedded in the generated liberty: a macro whose numbers
    cannot say where they came from is exactly the unlabeled-model artifact this repo
    refuses to produce.

    `write_granularity_bits` (e.g. 8 for byte writes) adds a `wmask` pin, one bit per granule,
    qualifying the write. Real compiled SRAM offers this and designs that need sub-word writes
    depend on it: without it a byte store becomes a read-modify-write, which on a 1RW macro costs
    a second cycle and changes the surrounding microarchitecture. Area/timing are caller-supplied
    and unchanged by this flag — a wmask costs a little periphery in reality, so a masked macro's
    area here is very slightly optimistic, and the liberty records that the pin was added."""
    if ports is not None and ports != {"rw": 1}:
        raise ValueError(
            f"macro {name!r}: this generator emits a 1RW interface (clk/we/ce/addr/din/dout) "
            f"and cannot represent ports={ports!r} — CACTI models multi-port SRAM and the "
            "architecture translator accepts attrs.ports, but a LEF/liberty whose pins do not "
            "match the requested ports would misrepresent the macro (docs/decisions.md D259)"
        )
    depth = int(size_kb * 1024 * 8 / word_width_bits)
    if depth < 2 or (depth & (depth - 1)) != 0:
        raise ValueError(
            f"size_kb={size_kb} x 8 / word_width_bits={word_width_bits} gives depth={depth} "
            "— need a power of two >= 2 (address bits must be integral)"
        )
    addr_bits = depth.bit_length() - 1
    side_um = round(math.sqrt(area_mm2 * 1e6), 3)
    w_um = h_um = side_um

    mask_bits = 0
    if write_granularity_bits is not None:
        if write_granularity_bits <= 0 or word_width_bits % write_granularity_bits != 0:
            raise ValueError(
                f"macro {name!r}: write_granularity_bits={write_granularity_bits!r} must be a "
                f"positive divisor of word_width_bits={word_width_bits}"
            )
        mask_bits = word_width_bits // write_granularity_bits

    pins = (
        [("clk", "input", 1), ("we", "input", 1), ("ce", "input", 1)]
        + ([("wmask", "input", mask_bits)] if mask_bits else [])
        + [("addr", "input", addr_bits), ("din", "input", word_width_bits),
           ("dout", "output", word_width_bits)]
    )

    # -- LEF: CLASS BLOCK, pins as M4 rects on the REAL track grid ---------------------------
    # ASAP7 1x M4 (horizontal): PITCH 0.048, WIDTH 0.024, OFFSET 0.003 — read from the
    # vendored tech LEF, because Hier-RTLMP refuses pins it cannot align with the track grid
    # (measured: MPL-0005 with free-floating pin rects, D254). Each pin is one wire-width
    # strip centered on a track: track y = OFFSET + n*PITCH.
    _M4_PITCH, _M4_WIDTH, _M4_OFFSET = 0.048, 0.024, 0.003
    lef_pins = []
    total_bits = sum(bits for _, _, bits in pins)
    n_tracks = int((h_um - 2 * _M4_OFFSET) / _M4_PITCH)
    if n_tracks < total_bits:
        raise ValueError(
            f"macro {name!r} is too short for its pins: {total_bits} pins need "
            f"{total_bits} M4 tracks, the {h_um:.3f}um edge has {n_tracks}"
        )
    stride = max(1, n_tracks // (total_bits + 1))
    track = stride
    for pin_name, direction, bits in pins:
        for i in range(bits):
            full = pin_name if bits == 1 else f"{pin_name}[{i}]"
            y_center = _M4_OFFSET + track * _M4_PITCH
            y_lo = y_center - _M4_WIDTH / 2
            lef_pins.append(
                f"  PIN {full}\n"
                f"    DIRECTION {'INPUT' if direction == 'input' else 'OUTPUT'} ;\n"
                f"    USE SIGNAL ;\n"
                f"    PORT\n"
                f"      LAYER M4 ;\n"
                f"        RECT 0.000 {y_lo:.3f} 0.048 {y_lo + _M4_WIDTH:.3f} ;\n"
                f"    END\n"
                f"  END {full}\n"
            )
            track += stride
            if track >= n_tracks:
                track = (track % n_tracks) + 1
    lef_text = (
        "VERSION 5.8 ;\nBUSBITCHARS \"[]\" ;\nDIVIDERCHAR \"/\" ;\n"
        f"MACRO {name}\n"
        "  CLASS BLOCK ;\n"
        f"  FOREIGN {name} 0 0 ;\n"
        "  ORIGIN 0 0 ;\n"
        f"  SIZE {w_um:.3f} BY {h_um:.3f} ;\n"
        "  SYMMETRY X Y ;\n"
        + "".join(lef_pins)
        + "  OBS\n"
        "    LAYER M1 ;\n"
        f"      RECT 0.100 0.000 {w_um:.3f} {h_um:.3f} ;\n"
        "    LAYER M2 ;\n"
        f"      RECT 0.100 0.000 {w_um:.3f} {h_um:.3f} ;\n"
        "    LAYER M3 ;\n"
        f"      RECT 0.100 0.000 {w_um:.3f} {h_um:.3f} ;\n"
        "  END\n"
        f"END {name}\n"
        "END LIBRARY\n"
    )

    # -- Liberty: area + pins + one conservative constant timing arc -------------------------
    def bus_type(bits: int, tag: str) -> str:
        return (
            f"  type ({tag}) {{\n"
            "    base_type : array ;\n    data_type : bit ;\n"
            f"    bit_width : {bits} ;\n    bit_from : {bits - 1} ;\n    bit_to : 0 ;\n"
            "  }\n"
        )

    lib_text = (
        f"/* Generated black-box SRAM macro (docs/decisions.md D254). Placement-grade, not\n"
        f"   sign-off: {provenance} */\n"
        f"library ({name}_lib) {{\n"
        "  delay_model : table_lookup ;\n"
        "  time_unit : \"1ns\" ;\n  capacitive_load_unit (1, pf) ;\n"
        "  voltage_unit : \"1V\" ;\n  current_unit : \"1mA\" ;\n"
        "  leakage_power_unit : \"1mW\" ;\n  pulling_resistance_unit : \"1kohm\" ;\n"
        "  nom_process : 1 ;\n  nom_temperature : 25 ;\n  nom_voltage : 0.7 ;\n"
        + bus_type(addr_bits, "addr_bus")
        + bus_type(word_width_bits, "data_bus")
        + f"  cell ({name}) {{\n"
        f"    area : {w_um * h_um:.3f} ;\n"
        "    interface_timing : true ;\n"
        "    pin (clk) { direction : input ; clock : true ; capacitance : 0.001 ; }\n"
        "    pin (we)  { direction : input ; capacitance : 0.001 ; }\n"
        "    pin (ce)  { direction : input ; capacitance : 0.001 ; }\n"
        "    bus (addr) { bus_type : addr_bus ; direction : input ; capacitance : 0.001 ; }\n"
        "    bus (din)  { bus_type : data_bus ; direction : input ; capacitance : 0.001 ; }\n"
        "    bus (dout) {\n"
        "      bus_type : data_bus ; direction : output ; max_capacitance : 0.5 ;\n"
        "      timing () {\n"
        "        related_pin : \"clk\" ; timing_type : rising_edge ;\n"
        f"        cell_rise (scalar) {{ values (\"{access_ns:.4f}\") ; }}\n"
        f"        rise_transition (scalar) {{ values (\"0.05\") ; }}\n"
        f"        cell_fall (scalar) {{ values (\"{access_ns:.4f}\") ; }}\n"
        f"        fall_transition (scalar) {{ values (\"0.05\") ; }}\n"
        "      }\n"
        "    }\n"
        "  }\n"
        "}\n"
    )

    wmask_decl = f"  input  wire [{mask_bits - 1}:0] wmask,\n" if mask_bits else ""
    verilog_stub = (
        f"(* blackbox *)\n"
        f"module {name} (\n"
        "  input  wire clk,\n  input  wire we,\n  input  wire ce,\n"
        f"{wmask_decl}"
        f"  input  wire [{addr_bits - 1}:0] addr,\n"
        f"  input  wire [{word_width_bits - 1}:0] din,\n"
        f"  output wire [{word_width_bits - 1}:0] dout\n"
        ");\nendmodule\n"
    )

    return SramMacro(
        name=name, word_width_bits=word_width_bits, depth=depth,
        width_um=w_um, height_um=h_um,
        lef_text=lef_text, lib_text=lib_text, verilog_stub=verilog_stub,
        write_mask_bits=mask_bits,
    )
