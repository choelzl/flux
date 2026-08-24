"""Generated ASAP7 SRAM macro through the REAL flow (docs/decisions.md D253/D254): the
area chain is CACTI 7 at its native 32nm, scaled to 7nm by the published Stillmaker & Baas
factor, emitted as a black-box macro, and PLACED by real Yosys + OpenROAD next to a real
8-lane int8 datapath — one technology, one flow, one area that includes the memory.

Probed before written: scaled 4KB gbuf = 853 um2; the combined placement reported 1260 um2 =
macro (853) + datapath (~407, matching D225's 401 um2 8-lane pin), positive slack, 3831
cells. Skips without openroad (nix develop .#physical)."""

from __future__ import annotations

import shutil

import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("openroad") is None,
    reason="needs openroad on PATH (nix develop .#physical)",
)

_RTL = """
module EngineWithBuffer (
  input  wire clk, input wire we, input wire ce, input wire [8:0] addr,
  input  wire signed [7:0] a0, a1, a2, a3, a4, a5, a6, a7,
  input  wire signed [7:0] w0, w1, w2, w3, w4, w5, w6, w7,
  output reg  signed [18:0] acc
);
  wire [63:0] buf_dout;
  fakeram_gbuf_4k gbuf (
    .clk(clk), .we(we), .ce(ce), .addr(addr),
    .din({w7, w6, w5, w4, w3, w2, w1, w0}), .dout(buf_dout)
  );
  wire signed [7:0] b0 = buf_dout[7:0],   b1 = buf_dout[15:8],
                    b2 = buf_dout[23:16], b3 = buf_dout[31:24],
                    b4 = buf_dout[39:32], b5 = buf_dout[47:40],
                    b6 = buf_dout[55:48], b7 = buf_dout[63:56];
  always @(posedge clk) begin
    acc <= a0*b0 + a1*b1 + a2*b2 + a3*b3 + a4*b4 + a5*b5 + a6*b6 + a7*b7;
  end
endmodule
"""


def test_engine_plus_buffer_place_together_and_the_area_includes_both():
    from flux_evaluator_openroad.flow import run_ppa_flow
    from flux_evaluator_openroad.sram_macro import generate_sram_macro

    # the D256 refined estimate: 4KB x 8 bits x 0.027 um2/bit (TSMC N7 HD bitcell) /
    # 0.7360 (CACTI's own 32nm array efficiency for this config) = 1202 um2 — probed live
    # access time: the published ASAP7 reference absolute, carrying CACTI's own size/shape
    # dependence via the same-node ratio (D259) — 388 ps for this 4KBx64 buffer, not the
    # reference's flat 218 ps and not the 67 ps the inverter proxy claimed (D258)
    from flux_evaluator_cacti.scaling import anchored_access_ns

    access_ns, access_note = anchored_access_ns(0.2651, 0.1488)
    assert access_ns == pytest.approx(0.3884, abs=0.0005)
    macro = generate_sram_macro(
        "fakeram_gbuf_4k", size_kb=4, word_width_bits=64,
        area_mm2=0.001202,
        access_ns=access_ns,
        provenance="area: bits x TSMC N7 HD bitcell 0.027 um2 / 0.7360 CACTI@32nm array "
                   f"efficiency (D255/D256); {access_note}")
    assert macro.area_um2 == pytest.approx(1202.0, rel=0.001)

    report = run_ppa_flow(_RTL, "EngineWithBuffer", clock_port="clk",
                          macros=[macro], timeout_s=900)
    # the reported design area includes the macro AND the mapped datapath: strictly more
    # than the macro alone, and pinned against the probed combined placement
    assert report.area_um2 > macro.area_um2
    assert report.area_um2 == pytest.approx(1609.0, rel=0.05)
    # the datapath share matches the known 8-lane pin (D225: ~401 um2) within placer noise
    assert report.area_um2 - macro.area_um2 == pytest.approx(407.0, rel=0.10)
    # with the reference (3.2x slower, honest) access time the design still closes at 2ns
    assert report.worst_slack_ps > 0

    # our generated macro's area density is within 15% of the shipped ASAP7 reference macro
    # (fakeram7_256x32: 351.1 um2 / 8192 bits = 0.0429 um2/bit) — the D258 cross-check, kept
    # live so a drifting area chain fails here rather than in a report nobody re-runs
    ours_per_bit = macro.area_um2 / (4 * 1024 * 8)
    assert ours_per_bit == pytest.approx(0.0429, rel=0.15)
