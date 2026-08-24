"""Real Verilator runs of the generated fabrics (docs/decisions.md D266).

This is the test that turns the interconnect DSE's throughput column from a model into a
measurement, and it pins the three things the measuring found — each of which had made some
fabric look better than it is:

1. the analytic model is exact for a direct crossbar, so the harness agrees where agreement
   is provable;
2. it is PESSIMISTIC exactly where a fabric has several paths per destination, because it
   assumes random port selection where the RTL rotates among equivalent paths;
3. a Clos gains throughput up to m = n and then stops, so the strict-sense condition
   (m >= 2n-1) buys wiring, not packets — a published theorem about circuit switching read as
   a packet-mode promise would have paid for that twice over.

Skips without verilator (nix develop .#physical). Runs ~1 min.
"""

from __future__ import annotations

import shutil

import pytest

from flux_interconnect.fabric import measure_throughput, path_diversity
from flux_interconnect.topology import butterfly, clos_network, full_crossbar, staged_crossbar

pytestmark = pytest.mark.skipif(
    shutil.which("verilator") is None,
    reason="needs verilator on PATH (nix develop .#physical)",
)

_CLIENTS, _BANKS, _WIDTH = 28, 32, 128
_CYCLES = 20000


def test_the_analytic_model_is_exact_for_a_direct_crossbar():
    """Where the model is provably right it must BE right: a one-stage crossbar has no
    internal blocking, so simulated and modelled throughput are the same number. This is the
    harness's own credibility check — a testbench that cannot reproduce the occupancy result
    has nothing to say about the fabrics where the model is unverifiable."""
    run = measure_throughput(full_crossbar(_CLIENTS, _BANKS, _WIDTH), cycles=_CYCLES)
    assert run["measured_words_per_cycle"] == pytest.approx(18.85, rel=0.01)
    assert run["ratio"] == pytest.approx(1.0, rel=0.02)


def test_where_the_model_and_the_rtl_disagree_is_where_paths_are_plural():
    """The measured shape of the analytic screen's error, which is the thing worth knowing
    about it: it assumes each switch picks among its outputs uniformly at random, so it is
    accurate wherever exactly one output reaches the destination, and PESSIMISTIC where several
    do and the router rotates among them. A radix-4 butterfly has two-way choice at its first
    stage and lands 3% above the model; a Clos at m = n has four-way choice and lands 21%
    above it."""
    butterfly4 = measure_throughput(butterfly(_CLIENTS, _BANKS, _WIDTH, 4), cycles=_CYCLES)
    assert butterfly4["measured_words_per_cycle"] == pytest.approx(13.54, rel=0.03)
    assert butterfly4["ratio"] == pytest.approx(1.03, rel=0.05)
    assert path_diversity(butterfly(_CLIENTS, _BANKS, _WIDTH, 4)) == [2.0, 1.0, 1.0]

    clos = measure_throughput(clos_network(_CLIENTS, _BANKS, _WIDTH, 4, 4), cycles=_CYCLES)
    assert clos["ratio"] == pytest.approx(1.21, rel=0.05)

    # and where every destination has exactly one path, the model is simply right
    single = measure_throughput(butterfly(_CLIENTS, _BANKS, _WIDTH, 2), cycles=_CYCLES)
    assert path_diversity(butterfly(_CLIENTS, _BANKS, _WIDTH, 2)) == [1.0] * 5
    assert single["ratio"] == pytest.approx(1.0, rel=0.03)


def test_a_parallel_switch_fabric_matches_its_model():
    """The user-proposed 7x(4x4) -> 4x(7x8) fabric, where every destination has exactly one
    path and the model's assumptions hold."""
    topo = staged_crossbar(_CLIENTS, _BANKS, _WIDTH, [
        {"switches": 7, "in": 4, "out": 4}, {"switches": 4, "in": 7, "out": 8}])
    run = measure_throughput(topo, cycles=_CYCLES)
    assert run["measured_words_per_cycle"] == pytest.approx(14.89, rel=0.02)
    assert run["ratio"] == pytest.approx(1.0, rel=0.03)


def test_a_clos_gains_throughput_up_to_m_equals_n_and_no_further():
    """The measured shape of the Clos trade, and the reason the catalog refuses to translate
    "strictly non-blocking" into a throughput promise: m = 2 (blocking) is far worse, m = 4
    captures essentially all of the available gain, and m = 7 — which satisfies m >= 2n-1 —
    adds ~75% more inter-stage wiring for nothing measurable."""
    got = {m: measure_throughput(clos_network(_CLIENTS, _BANKS, _WIDTH, 4, m),
                                 cycles=_CYCLES)["measured_words_per_cycle"]
           for m in (2, 4, 7)}
    assert got[2] < 0.7 * got[4], f"m=2 should be badly blocking, got {got}"
    assert got[7] == pytest.approx(got[4], rel=0.02), f"strict-sense bought throughput? {got}"
    assert got[4] > 15.0

    wires = {m: clos_network(_CLIENTS, _BANKS, _WIDTH, 4, m).interstage_link_bits()
             for m in (4, 7)}
    assert wires[7] > 1.7 * wires[4]


def test_path_diversity_is_what_makes_a_clos_a_clos():
    """A Clos whose router always picks the same middle switch measured identically for m = 2,
    4, 7 and 8 — the middle stage was built and unreachable. Diversity at the ingress stage is
    the structural fact that has to hold before any Clos claim means anything."""
    for m in (4, 8):
        ingress, middle, egress = path_diversity(clos_network(_CLIENTS, _BANKS, _WIDTH, 4, m))
        assert ingress == float(m), f"ingress diversity {ingress} should be m={m}"
        assert (middle, egress) == (1.0, 1.0)  # past the middle, the destination is fixed


def test_every_family_delivers_to_the_right_bank_with_the_right_data():
    """Throughput measures how much a structure moves, not whether it works: a fabric that
    delivered every word to the WRONG bank would report the same words/cycle as a correct one.
    The stimulus makes the destination a function of the payload, so each bank verifies its own
    mail, and the payload carries its own complement so dropped or crossed bits are caught."""
    fabrics = [
        full_crossbar(_CLIENTS, _BANKS, _WIDTH),
        butterfly(_CLIENTS, _BANKS, _WIDTH, 4),
        clos_network(_CLIENTS, _BANKS, _WIDTH, 4, 4),
        staged_crossbar(_CLIENTS, _BANKS, _WIDTH, [
            {"switches": 7, "in": 4, "out": 4}, {"switches": 4, "in": 7, "out": 8}]),
    ]
    for topo in fabrics:
        checks = measure_throughput(topo, cycles=5000)["correctness"]
        assert checks["route_errors"] == 0, f"{topo.kind} misrouted"
        assert checks["data_errors"] == 0, f"{topo.kind} corrupted payloads"
        # and no client is starved — an aggregate throughput number cannot show this
        assert checks["starvation_ratio"] > 0.8, f"{topo.kind} serves clients unevenly: {checks}"


def test_the_correctness_check_actually_catches_a_broken_fabric():
    """The negative control, without which the test above proves nothing. One swapped entry in
    the last stage's routing table — bank 0's words sent to port 1 — must be caught, and caught
    as a failure rather than reported as a throughput."""
    import flux_interconnect.fabric as fabric_mod

    real = fabric_mod.routing_tables

    def misrouting(topo):
        table = real(topo)
        table[-1][0][0], table[-1][0][1] = table[-1][0][1], table[-1][0][0]
        return table

    fabric_mod.routing_tables = misrouting
    try:
        with pytest.raises(fabric_mod.FabricIncorrectError, match="wrong bank"):
            measure_throughput(full_crossbar(_CLIENTS, _BANKS, _WIDTH), cycles=3000)
    finally:
        fabric_mod.routing_tables = real


def test_the_generated_switch_is_pipelined_and_still_delivers_correctly():
    """The switch is split into a decode stage and an arbitrate-and-mux stage
    (docs/decisions.md D273), which is a change to the hardware, not to the model: throughput
    must be unchanged, correctness must be unchanged, and the latency the evaluator reports
    must have doubled to match. A pipeline that quietly cost throughput would show here."""
    topo = staged_crossbar(_CLIENTS, _BANKS, _WIDTH, [
        {"switches": 7, "in": 4, "out": 4}, {"switches": 4, "in": 7, "out": 8}])
    run = measure_throughput(topo, cycles=_CYCLES)
    assert run["measured_words_per_cycle"] == pytest.approx(14.89, rel=0.02)
    assert run["correctness"]["route_errors"] == 0
    assert run["correctness"]["data_errors"] == 0

    rtl = __import__("flux_interconnect.fabric", fromlist=["fabric_rtl"]).fabric_rtl(topo)
    assert "decode_proc" in rtl and "switch_proc" in rtl, "the pipeline register is gone"
    assert "want_q" in rtl and "chosen_q" in rtl


def test_the_vendored_switch_and_the_generated_one_agree_on_every_family():
    """Two independent switch implementations of the SAME network (docs/decisions.md D279): the
    generated one, and the vendored PULP `xbar_varlat` driven from the same routing tables.
    They should move the same words, and where they do not it is a real difference rather than
    noise — taking only the first valid port instead of rotating measured 4.86 words/cycle on a
    Clos where rotating gives 15.46, and the failure looked perfect on a routing check because
    a fabric that delivers nothing misroutes nothing."""
    from flux_interconnect.topology import full_crossbar as _xbar

    families = [
        ("staged", staged_crossbar(_CLIENTS, _BANKS, _WIDTH, [
            {"switches": 7, "in": 4, "out": 4}, {"switches": 4, "in": 7, "out": 8}])),
        ("clos", clos_network(_CLIENTS, _BANKS, _WIDTH, 4, 4)),
        ("butterfly", butterfly(_CLIENTS, _BANKS, _WIDTH, 4)),
        ("crossbar", _xbar(_CLIENTS, _BANKS, _WIDTH)),
    ]
    for name, topo in families:
        generated = measure_throughput(topo, cycles=_CYCLES)
        vendored = measure_throughput(topo, cycles=_CYCLES, switch="vendored")
        assert vendored["correctness"]["route_errors"] == 0, name
        assert vendored["correctness"]["data_errors"] == 0, name
        assert vendored["measured_words_per_cycle"] == pytest.approx(
            generated["measured_words_per_cycle"], abs=0.1), name
