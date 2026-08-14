"""The structural claims the interconnect DSE rests on (docs/decisions.md D261, D264, D265).

These are unit tests, not silicon: they pin what the topology MODEL asserts, because every
number the demo prints is that model multiplied by measured block PPA. Three of them exist
because the model was wrong in a way that flattered a fabric — a degenerate chain that could
not reach every bank, a blocking-free throughput claim for a multistage network, and a
butterfly that priced only half its hardware. Each of those made a bad topology win.
"""

from __future__ import annotations

import pytest

from flux_interconnect import build
from flux_interconnect.topology import (
    butterfly,
    enumerate_space,
    full_crossbar,
    multistage_crossbar,
    staged_crossbar,
)

_CLIENTS, _BANKS, _WIDTH = 28, 32, 128


def test_a_fabric_that_cannot_reach_every_bank_is_refused():
    """28 switches of 1x2 chain into 32 banks arithmetically, and are not a crossbar: a client
    entering one 1x2 switch reaches 2 downstream switches, not 32 banks. This shape topped the
    area ranking before the check existed, which is the whole reason it is pinned."""
    with pytest.raises(ValueError, match="reaches only"):
        staged_crossbar(_CLIENTS, _BANKS, _WIDTH, [
            {"switches": 28, "in": 1, "out": 1},
            {"switches": 28, "in": 1, "out": 2},
        ])

    # the same family accepts a real fabric: the product of fan-outs covers every bank
    ok = staged_crossbar(_CLIENTS, _BANKS, _WIDTH, [
        {"switches": 7, "in": 4, "out": 4},
        {"switches": 4, "in": 7, "out": 8},
    ])
    assert ok.peak_concurrency == 28


def test_direct_crossbar_throughput_is_the_classic_occupancy_result():
    """A one-stage crossbar has no internal blocking, so the stage-by-stage model must reduce
    exactly to `banks * (1 - (1 - 1/banks)**clients)` — the bound bank conflicts alone impose.
    If this drifts, the blocking model has broken the case it is supposed to agree with."""
    expected = _BANKS * (1 - (1 - 1 / _BANKS) ** _CLIENTS)
    assert full_crossbar(_CLIENTS, _BANKS, _WIDTH).expected_served_per_cycle() == pytest.approx(
        expected, rel=1e-9)
    assert expected == pytest.approx(18.85, abs=0.01)


def test_a_multistage_fabric_serves_strictly_less_than_a_crossbar():
    """The point of the Patel-style model: requests lose contention for inter-stage links, so
    every fabric that is not a crossbar must serve LESS. A radix-4 network reported the full
    18.85 words/cycle under the bank-conflict-only model — indistinguishable from a crossbar
    costing many times its area, which is exactly the flattery this removes."""
    crossbar = full_crossbar(_CLIENTS, _BANKS, _WIDTH).expected_served_per_cycle()
    for topo in (
        butterfly(_CLIENTS, _BANKS, _WIDTH, 4),
        butterfly(_CLIENTS, _BANKS, _WIDTH, 8),
        multistage_crossbar(_CLIENTS, _BANKS, _WIDTH, [8]),
    ):
        served = topo.expected_served_per_cycle()
        assert 0 < served < crossbar, f"{topo.kind} claims {served:.2f} of {crossbar:.2f}"
    assert butterfly(_CLIENTS, _BANKS, _WIDTH, 4).expected_served_per_cycle() == pytest.approx(
        13.13, abs=0.05)


def test_a_radix_32_butterfly_costs_about_what_a_direct_crossbar_costs():
    """Over 32 ports, a radix-32 butterfly IS a direct crossbar — one stage, one 32x32 switch.
    Any model in which it is dramatically cheaper is doing accounting, not engineering; this
    caught the butterfly family pricing its request path only."""
    bits = lambda t: sum(k[0] * k[1] * n for k, n in t.blocks.items())  # noqa: E731
    ratio = bits(butterfly(_CLIENTS, _BANKS, _WIDTH, 32)) / bits(
        full_crossbar(_CLIENTS, _BANKS, _WIDTH))
    assert 0.8 < ratio < 1.3, f"radix-32 butterfly is {ratio:.2f}x a crossbar"


def test_interstage_wiring_is_reported_where_it_exists_and_zero_where_it_does_not():
    """Composed cell area prices gates, never the wires between stages, so a fabric of many
    tiny switches looks free unless that burden is carried as its own metric."""
    assert full_crossbar(_CLIENTS, _BANKS, _WIDTH).interstage_link_bits() == 0
    staged = staged_crossbar(_CLIENTS, _BANKS, _WIDTH, [
        {"switches": 7, "in": 4, "out": 4}, {"switches": 4, "in": 7, "out": 8}])
    assert staged.interstage_link_bits() == 28 * _WIDTH  # 7 switches x 4 outputs, 128b each
    assert butterfly(_CLIENTS, _BANKS, _WIDTH, 4).interstage_link_bits() > 0


def test_the_space_is_discovered_from_the_problem_and_carries_no_duplicates():
    """The demo hands Flux (clients, banks, width) and nothing else. Everything evaluated has
    to come from here, every candidate has to build, and structurally identical fabrics must
    collapse — otherwise the search spends its budget re-measuring one design."""
    narrow = enumerate_space(_CLIENTS, _BANKS, _WIDTH, breadth="narrow")
    wide = enumerate_space(_CLIENTS, _BANKS, _WIDTH, breadth="wide", max_candidates=2000)
    assert len(wide) > len(narrow) > 20

    signatures = set()
    for spec in wide:
        topo = build(spec)  # raises if the space emitted something unbuildable
        # Routing belongs in the identity: two fabrics of the same shape under different
        # route-selection policies are different things to measure, serving 8.90 or 13.55
        # words/cycle on identical silicon (docs/decisions.md D302).
        signature = (tuple(sorted(topo.blocks.items())), topo.stages, topo.peak_concurrency,
                     topo.params.get("routing", "rotate"))
        assert signature not in signatures, f"duplicate fabric {spec}"
        signatures.add(signature)

    # The asymmetric shapes are the reason to enumerate rather than sweep powers of two: 7
    # divides the client count and no power-of-two sweep would find it.
    assert any(spec.get("ports") == [7] for spec in wide)
    assert any(spec.get("ports") == [7, 7] for spec in wide)
    # one family name for both spellings (docs/decisions.md D285): the rank form is `ports`,
    # the explicit form is `stages`, and neither gets a `kind` of its own
    assert {s["kind"] for s in wide if s.get("ports") is not None} == {"xbar_staged"}
    # `[7, 8]` is deliberately NOT here (docs/decisions.md D271): seven links cannot feed eight
    # ranks, so that fabric IS `[7, 7]` and dedups into it. The previous implementation emitted
    # it as a distinct candidate whose stages did not chain and whose RTL could not be built.
    assert not any(spec.get("ports") == [7, 8] for spec in wide)


def test_a_rounds_stage_budget_actually_scopes_what_it_offers():
    """Widening rounds only mean something if each round's scope holds. A Clos is a
    three-stage construction, so offering one inside a two-stage round breaks that round's own
    budget — it did, and round 1 went from 16 candidates to 46 without anyone asking for
    deeper fabrics.

    The deliberate exceptions, pinned so they stay deliberate: `max_stages` bounds the RANK-BASED
    enumeration, while two families derive their depth from their own structure. A radix-2
    network over 32 ports is five stages, and a router network's depth is its DIAMETER — a 14x14
    mesh is 26 hops whatever round offers it. Both are offered in every round and both carry
    their real depth in the result table, which is why those tables have a stage column.
    """
    derived_depth = ("butterfly", "mesh", "torus", "ring")
    two_stage = enumerate_space(_CLIENTS, _BANKS, _WIDTH, max_stages=2, breadth="wide",
                                max_candidates=5000)
    assert not [s for s in two_stage if s["kind"] == "clos"]
    assert all(build(s).stages <= 2 for s in two_stage
               if s["kind"] not in derived_depth)

    three_stage = enumerate_space(_CLIENTS, _BANKS, _WIDTH, max_stages=3, breadth="narrow")
    assert [s for s in three_stage if s["kind"] == "clos"], "Clos must appear once 3 stages fit"

    deep = [build(s) for s in two_stage if s["kind"] == "butterfly"]
    assert any(t.stages > 2 for t in deep), "radix-R depth is derived, not bounded"
    hops = [build(s) for s in two_stage if s["kind"] in ("mesh", "torus")]
    assert any(t.stages > 2 for t in hops), "a router network's depth is its diameter"


def test_families_can_be_mixed_into_one_fabric():
    """A Clos ingress feeding a crossbar, a radix layer finished by a fan-out: compositions no
    single named family describes (docs/decisions.md D270). They are not new hardware — every
    family already reduces to a stage list — but naming the layers makes the composition
    something a search or a model can propose without open-coding the arithmetic."""
    from flux_interconnect.fabric import canonical_stages, routing_tables
    from flux_interconnect.topology import hybrid_fabric

    mixes = [
        [{"family": "clos", "n": 4, "m": 4}, {"family": "xbar", "switches": 4}],
        [{"family": "radix", "radix": 8}, {"family": "xbar", "switches": 4}],
        [{"family": "concentrate", "factor": 2}, {"family": "radix", "radix": 4},
         {"family": "xbar", "switches": 4}],
    ]
    for layers in mixes:
        topo = hybrid_fabric(_CLIENTS, _BANKS, _WIDTH, layers)
        routing_tables(topo)                       # every client reaches every bank
        assert topo.kind == "hybrid"
        assert len(canonical_stages(topo)) == topo.stages >= 2
        assert topo.expected_served_per_cycle() > 0

    # the layers really do chain: consecutive stages agree on their link count, which is the
    # rule that no hand-written composition manages to keep
    stages = canonical_stages(hybrid_fabric(_CLIENTS, _BANKS, _WIDTH, mixes[0]))
    for before, after in zip(stages, stages[1:]):
        assert before["switches"] * before["out"] == after["switches"] * after["in"]


def test_a_hybrids_last_layer_is_resized_to_actually_reach_the_banks():
    """A crossbar layer asking for 8 switches over 28 links gets snapped to 7, and 7 x 4 covers
    only 28 of 32 banks. The fan-out is recomputed from the switch count the layer actually
    got — otherwise a perfectly reasonable composition is refused for an arithmetic detail the
    proposer could not have anticipated."""
    from flux_interconnect.fabric import canonical_stages
    from flux_interconnect.topology import hybrid_fabric

    topo = hybrid_fabric(_CLIENTS, _BANKS, _WIDTH, [
        {"family": "radix", "radix": 4, "stages": 2}, {"family": "xbar", "switches": 8}])
    last = canonical_stages(topo)[-1]
    assert last["switches"] * last["out"] >= _BANKS


def test_capacity_is_a_count_and_does_not_move_with_the_traffic():
    """A rate without its capacity says nothing about what was left on the table
    (docs/decisions.md D282), and a capacity is a COUNT of concurrent transfers fixed by the
    structure's narrowest point. It was first written as the bank-conflict bound, 18.85 for
    this problem, which is neither an integer nor a maximum: it is the expected number of
    distinct banks hit under one traffic pattern."""
    from flux_interconnect.topology import hierarchical_crossbar

    crossbar = full_crossbar(_CLIENTS, _BANKS, _WIDTH)
    assert crossbar.max_served_per_cycle() == 28        # every client, every cycle
    assert isinstance(crossbar.max_served_per_cycle(), int)

    # the narrowest point binds when the structure, not the client count, is the limit
    assert staged_crossbar(_CLIENTS, _BANKS, _WIDTH, [
        {"switches": 7, "in": 4, "out": 2}, {"switches": 2, "in": 7, "out": 16},
    ]).max_served_per_cycle() == 14                     # 7 switches x 2 links
    assert staged_crossbar(_CLIENTS, _BANKS, _WIDTH, [
        {"switches": 8, "in": 4, "out": 2}, {"switches": 2, "in": 8, "out": 16},
    ]).max_served_per_cycle() == 16
    assert hierarchical_crossbar(_CLIENTS, _BANKS, _WIDTH, 4).max_served_per_cycle() == 4

    # Traffic-agnostic by construction: the measured rate depends on the traffic model, the
    # capacity does not, and no fabric can exceed its own capacity.
    for topo in (crossbar, butterfly(_CLIENTS, _BANKS, _WIDTH, 4),
                 hierarchical_crossbar(_CLIENTS, _BANKS, _WIDTH, 4)):
        assert topo.expected_served_per_cycle() <= topo.max_served_per_cycle() + 1e-9, topo.kind


def test_a_fabric_that_cannot_carry_every_client_is_refusable_at_screen():
    """Full concurrency is a requirement of the stated problem, not a preference: the clients
    access the banks at the same time, so a fabric whose narrowest point carries fewer than all
    of them cannot serve it however cheap it is (docs/decisions.md D283).

    The capacity is structural, so this is decidable at the screen, before any tool runs. That
    is the whole reason to express it as a constraint on a screened metric rather than filtering
    the results afterwards: a disqualified fabric never costs a place-and-route."""
    full = staged_crossbar(_CLIENTS, _BANKS, _WIDTH, [
        {"switches": 7, "in": 4, "out": 4}, {"switches": 4, "in": 7, "out": 8}])
    narrow = staged_crossbar(_CLIENTS, _BANKS, _WIDTH, [
        {"switches": 7, "in": 4, "out": 2}, {"switches": 2, "in": 7, "out": 16}])

    assert full.max_served_per_cycle() >= _CLIENTS
    assert narrow.max_served_per_cycle() < _CLIENTS

    # and the screening evaluator reports it, which is what a constraint can act on
    from flux_evaluator_abi import Budget, Candidate
    from flux_evaluator_interconnect_struct.adapter import InterconnectStructuralEvaluator

    evaluator = InterconnectStructuralEvaluator()
    for topo, expected in ((full, 28), (narrow, 14)):
        spec = {"kind": "xbar_staged", "clients": _CLIENTS, "banks": _BANKS,
                "width_bits": _WIDTH, "stages": topo.params["stages"]}
        result = evaluator.evaluate(
            Candidate(arch={"interconnect": spec}, workload={}), Budget(), frozenset())
        assert result.value_of("max_throughput_words_per_cycle") == expected


def test_mixed_topologies_are_enumerated_not_only_proposed():
    """Hybrids were reachable only through the LLM proposer, so a deterministic run contained
    none and "can it mix families" was answered by whether a model happened to think of it
    (docs/decisions.md D284). The wide space now enumerates them directly."""
    from flux_interconnect.fabric import canonical_stages, routing_tables

    wide = enumerate_space(_CLIENTS, _BANKS, _WIDTH, max_stages=3, breadth="wide",
                           max_candidates=5000)
    hybrids = [s for s in wide if s["kind"] == "hybrid"]
    assert len(hybrids) >= 20, f"only {len(hybrids)} mixed fabrics enumerated"

    # both mixtures are present: a radix network into a crossbar, and a Clos into a crossbar
    families = {tuple(layer["family"] for layer in s["layers"]) for s in hybrids}
    assert ("radix", "xbar") in families
    assert ("clos", "xbar") in families

    # and every one of them is a real, routable fabric rather than a label
    for spec in hybrids:
        topo = build(spec)
        routing_tables(topo)
        assert len(canonical_stages(topo)) == topo.stages >= 2

    # the narrow space stays narrow: mixing is a widening step, not the default
    assert not [s for s in enumerate_space(_CLIENTS, _BANKS, _WIDTH, max_stages=3,
                                           breadth="narrow") if s["kind"] == "hybrid"]
