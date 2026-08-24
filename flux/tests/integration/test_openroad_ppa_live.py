"""Real Yosys + OpenROAD place-and-report on ASAP7 (docs/decisions.md D225). Skips without an
`openroad` binary — `nix develop .#physical` provides one (built from source; or-tools makes the
first build slow).

The pinned numbers are real placement measurements at TRUE workload precision (docs/decisions.md
D228): the 8-lane int8 datapath at 401 um^2 / 11.1 mW / +768 ps at a 2 ns clock — 4.8x smaller
than the D225-era 32-bit-carrier figure, which is exactly the overstatement D228 removed — and
the near-exact 2x area scaling per lane doubling that a one-multiplier-per-lane datapath must
show. All three widths now meet 2 ns (the int8 adder tree is shallower than the 32-bit one that
made 32 lanes miss); slack still degrades monotonically with lanes, which is the signal the
timing test pins.
"""

from __future__ import annotations

import copy
import shutil
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.skipif(
    shutil.which("openroad") is None,
    reason="needs openroad on PATH (nix develop .#physical)",
)

FLUX_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def family_results():
    from flux_cli.registry import make_evaluator
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


def test_the_reference_point_pins_real_placement_numbers(family_results):
    r = family_results[8]
    assert r.value_of("area_mm2") == pytest.approx(401e-6, rel=0.05)
    assert r.value_of("power_w") == pytest.approx(0.0111, rel=0.10)
    assert r.value_of("worst_slack_ps") > 0
    assert r.validity.ok
    assert r.provenance.evaluator == "openroad@asap7-placement"
    assert r.provenance.inputs["flow_depth"] == "placement"


def test_area_scales_linearly_with_lanes(family_results):
    """One multiplier per lane: each doubling must roughly double placed area. Measured 2.004x
    and 2.002x when pinned; 15% tolerance absorbs placer noise without admitting a wrong slope."""
    a8 = family_results[8].value_of("area_mm2")
    a16 = family_results[16].value_of("area_mm2")
    a32 = family_results[32].value_of("area_mm2")
    assert a16 / a8 == pytest.approx(2.0, rel=0.15)
    assert a32 / a16 == pytest.approx(2.0, rel=0.15)


def test_timing_degrades_with_lanes_and_the_int8_tree_meets_the_clock(family_results):
    slacks = {lanes: family_results[lanes].value_of("worst_slack_ps") for lanes in (8, 16, 32)}
    # Monotonic degradation with width held while the flow did no timing optimisation. Once
    # `repair_timing` runs (docs/decisions.md D278) the optimiser works every design toward the
    # SAME target, so the deeper trees converge rather than separating: 8 lanes stays clearly
    # best, and 16 and 32 land within a couple of percent of each other. Pinning the old
    # ordering would be pinning the absence of optimisation.
    assert slacks[8] > slacks[16] and slacks[8] > slacks[32], slacks
    assert abs(slacks[16] - slacks[32]) / slacks[16] < 0.05, slacks
    # At true int8 widths every family member meets 2 ns (D228) — the D225-era 32-lane miss was
    # the 32-bit carriers' deeper tree. Validity stays wired to the slack sign either way.
    assert all(s > 0 for s in slacks.values()) and family_results[32].validity.ok
    # latency/energy are deliberately not this rung's numbers (docs/decisions.md D225)
    assert family_results[8].refusal_for("latency_cycles") is not None
    assert family_results[8].refusal_for("energy_pj") is not None


def test_the_routed_depth_extracts_real_parasitics_for_the_combinational_datapath(tmp_path):
    """flow_depth="routed" (docs/decisions.md D229): TritonRoute + OpenRCX on the 8-lane int8
    datapath. Placed area is unchanged by routing (same cells); slack shifts from the placement
    estimate to the extracted value (~+953 ps — the estimator is pessimistic here, which is
    itself a real finding about the estimator, not noise: pinned at 5%). Re-baselined for
    docs/decisions.md D278, which gave the flow full technology mapping, a timing-repair pass
    and the platform's own wire RC — every number measured before that came from a flow that
    never optimised timing."""
    import yaml
    from flux_evaluator_openroad.adapter import _canonical_datapath_source
    from flux_evaluator_openroad.flow import run_ppa_flow
    from flux_generation import derive_design_spec

    wl = yaml.safe_load((FLUX_ROOT / "core/ir/workload/examples/mlp-gemm0.yaml").read_text())
    arch = yaml.safe_load(
        (FLUX_ROOT / "core/ir/architecture/examples/simple-npu-1d-v1.yaml").read_text())
    d = derive_design_spec(wl, arch)
    r = run_ppa_flow(_canonical_datapath_source(d.spec), d.spec["module_name"],
                     flow_depth="routed")
    assert r.flow_depth == "routed"
    assert r.area_um2 == pytest.approx(401.0, rel=0.05)
    assert r.worst_slack_ps == pytest.approx(953.3, rel=0.05)
    assert r.power_total_w == pytest.approx(0.0108, rel=0.10)


def test_a_clocked_design_gets_a_real_clock_tree_and_finite_reg_to_reg_slack():
    """CTS runs only where a clock net exists, and the D229 liberty repair is what makes this
    assertable at all: before it, three SEQ-header table templates were missing from the D92
    merge, OpenSTA priced every DFF arc as unconstrained, and ANY clocked design reported
    worst-slack INF — silently, since ABC's area mapping never reads timing tables."""
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
