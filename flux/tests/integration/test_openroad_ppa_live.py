"""Real Yosys + OpenROAD place-and-report on ASAP7 (D225). Skips without an `openroad` binary
(`nix develop` provides one).

The numbers are placement measurements at true workload precision (D228), as bands that hold for
both tool generations Flux has pinned (D860): the 8-lane int8 datapath at 401 um^2 / 11.1 mW with
OpenROAD and Yosys of 2026-07-02, 331 um^2 / 5.4 mW (+749 ps at a 2 ns clock) with those of
2026-09-15 (nixchip f4fddde, D656). ~2x area per lane doubling either way; all three widths meet 2 ns.
A tool update that moves a number out of its band is a change to read, not noise.
"""

from __future__ import annotations

import copy
import shutil
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.skipif(
    shutil.which("openroad") is None,
    reason="needs openroad on PATH (nix develop)",
)

FLUX_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def family_results():
    from flux_evaluator_abi import make_evaluator
    from flux_evaluator_abi import Budget, Candidate

    wl = yaml.safe_load((FLUX_ROOT / "core/ir/workload/examples/mlp-gemm0.yaml").read_text())
    base = yaml.safe_load(
        (FLUX_ROOT / "core/ir/architecture/examples/simple-npu-1d-v1.yaml").read_text())
    ev = make_evaluator("openroad")
    out = {}
    for lanes in (8, 16, 32):
        arch = copy.deepcopy(base)
        next(n for n in arch["hierarchy"] if n["class"] == "compute")["attrs"]["dims"]["X"] = lanes
        arch["id"] = f"openroad-live-lanes{lanes}"
        out[lanes] = ev.evaluate(
            Candidate(workload=wl, arch=arch, mapping=None), Budget(),
            frozenset({"area_mm2", "power_w"}),
        )
    return out


def _within(v, lo, hi, what):
    assert lo <= v <= hi, f"{what} {v:g} outside [{lo:g}, {hi:g}] -- the tools moved it: read why before widening"


def test_the_reference_point_pins_real_placement_numbers(family_results):
    r = family_results[8]
    _within(r.value_of("area_mm2"), 300e-6, 440e-6, "8-lane placed area (mm2)")     # 401e-6 July, 331e-6 September
    _within(r.value_of("power_w"), 0.004, 0.013, "8-lane placed power (W)")         # 11.1 mW July, 5.4 mW September
    assert r.value_of("worst_slack_ps") > 0
    assert r.validity.ok
    assert r.provenance.evaluator == "openroad@asap7-placement"
    assert r.provenance.inputs["flow_depth"] == "placement"


def test_area_scales_linearly_with_lanes(family_results):
    """One multiplier per lane: each doubling roughly doubles placed area (15% tolerance for placer noise)."""
    a8 = family_results[8].value_of("area_mm2")
    a16 = family_results[16].value_of("area_mm2")
    a32 = family_results[32].value_of("area_mm2")
    assert a16 / a8 == pytest.approx(2.0, rel=0.15)
    assert a32 / a16 == pytest.approx(2.0, rel=0.15)


def test_timing_degrades_with_lanes_and_the_int8_tree_meets_the_clock(family_results):
    slacks = {lanes: family_results[lanes].value_of("worst_slack_ps") for lanes in (8, 16, 32)}
    # with `repair_timing` (D278) 16 and 32 lanes land near each other (within 5% with the July
    # tools, 17% with September's: 503 and 418 ps); only 8 lanes is clearly best
    assert slacks[8] > slacks[16] and slacks[8] > slacks[32], slacks
    assert abs(slacks[16] - slacks[32]) / slacks[16] < 0.25, slacks
    # at int8 widths every family member meets 2 ns (D228); validity follows the slack sign
    assert all(s > 0 for s in slacks.values()) and family_results[32].validity.ok
    # latency/energy are deliberately not this stage's numbers (D225)
    assert family_results[8].refusal_for("latency_cycles") is not None
    assert family_results[8].refusal_for("energy_pj") is not None


def test_the_routed_depth_extracts_real_parasitics_for_the_combinational_datapath(tmp_path):
    """flow_depth="routed" (D229): routing leaves placed area unchanged and moves slack to the
    extracted value (~+953 ps with the July tools, +893 with September's: a band, D860). Baselined
    on the D278 flow (mapping, timing repair, platform wire RC)."""
    import yaml
    from flux_evaluator_openroad.adapter import _canonical_datapath_source
    from flux_evaluator_openroad.flow import run_ppa_flow
    from flux_evaluator_openroad.derive import derive_design_spec

    wl = yaml.safe_load((FLUX_ROOT / "core/ir/workload/examples/mlp-gemm0.yaml").read_text())
    arch = yaml.safe_load(
        (FLUX_ROOT / "core/ir/architecture/examples/simple-npu-1d-v1.yaml").read_text())
    d = derive_design_spec(wl, arch)
    r = run_ppa_flow(_canonical_datapath_source(d.spec), d.spec["module_name"],
                     flow_depth="routed")
    assert r.flow_depth == "routed"
    _within(r.area_um2, 300.0, 440.0, "routed area (um2)")                 # 401 July, 331 September
    _within(r.worst_slack_ps, 850.0, 1000.0, "routed slack (ps)")          # 953 July, 893 September
    _within(r.power_total_w, 0.004, 0.013, "routed power (W)")             # 10.8 mW July, 5.2 mW September


def test_a_clocked_design_gets_a_real_clock_tree_and_finite_reg_to_reg_slack():
    """CTS runs where a clock net exists, and clocked designs report finite slack thanks to the
    liberty repair (D229); without it every clocked design reported worst-slack INF."""
    from flux_evaluator_openroad.flow import run_ppa_flow

    clocked = """module PipeMac (
  input logic clk, input logic signed [7:0] a, input logic signed [7:0] w,
  output logic signed [18:0] acc);
  logic signed [7:0] ar, wr; logic signed [15:0] p;
  always_ff @(posedge clk) begin ar <= a; wr <= w; p <= ar * wr; acc <= acc + p; end
endmodule
"""
    placed = run_ppa_flow(clocked, "PipeMac", clock_port="clk", flow_depth="placement")
    routed = run_ppa_flow(clocked, "PipeMac", clock_port="clk", flow_depth="routed")

    import math

    # finite reg-to-reg slack is the repaired-liberty claim; INF was the broken state
    assert math.isfinite(placed.worst_slack_ps) and math.isfinite(routed.worst_slack_ps)
    assert placed.worst_slack_ps == pytest.approx(1292.8, rel=0.05)
    assert routed.worst_slack_ps == pytest.approx(1321.9, rel=0.05)
    # the routed run carries a real clock tree: CTS adds cells/area and its buffers draw power
    assert routed.area_um2 >= placed.area_um2
    assert routed.power_total_w > placed.power_total_w * 0.9
