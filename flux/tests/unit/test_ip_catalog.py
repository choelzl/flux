"""The catalog's own rule, enforced (docs/decisions.md D267): an entry is listed only if Flux
can build it, and every claim it makes is separable into "published" and "we measured this"."""

from __future__ import annotations

import pytest

from flux_interconnect.catalog import CATALOG, INTERFACES, instantiate, list_ips
from flux_interconnect.fabric import routing_tables

_CLIENTS, _BANKS, _WIDTH = 28, 32, 128

_PARAMS = {  # the entry parameters a build needs beyond (clients, banks, width)
    "xbar_hier": {"groups": 4},
    "xbar_staged": {"stages": [{"switches": 7, "in": 4, "out": 4},
                               {"switches": 4, "in": 7, "out": 8}]},
    "clos": {"n": 4, "m": 4},
    "hybrid": {"layers": [{"family": "clos", "n": 4, "m": 4},
                          {"family": "xbar", "switches": 4}]},
    "butterfly": {"radix": 4},
}


@pytest.mark.parametrize("ip_id", sorted(
    ip for ip, entry in CATALOG.items() if entry.status == "constructible"))
def test_every_constructible_entry_builds_and_routes(ip_id):
    """`constructible` is a promise the catalog makes to the orchestrator. Keeping it means
    the entry produces a real topology whose wiring actually connects every client to every
    bank — checked here by building the routing tables, which refuse otherwise."""
    topo = instantiate(ip_id, _CLIENTS, _BANKS, _WIDTH, **_PARAMS.get(ip_id, {}))
    assert topo.blocks, f"{ip_id} built no hardware"
    assert topo.expected_served_per_cycle() > 0
    routing_tables(topo)  # raises UnroutableFabricError if some bank is unreachable


@pytest.mark.parametrize("ip_id", sorted(
    ip for ip, entry in CATALOG.items() if entry.status != "constructible"))
def test_entries_without_a_generator_refuse_rather_than_improvise(ip_id):
    """A catalog that answers every request with something plausible is worse than one that
    admits a gap: the mesh entry exists so its evaluation path (BookSim/Noxim, not silicon
    from this repo) is discoverable, not so it can be silently substituted for a fabric."""
    with pytest.raises(NotImplementedError, match=CATALOG[ip_id].status):
        instantiate(ip_id, _CLIENTS, _BANKS, _WIDTH)


def test_every_entry_names_a_real_interface_and_states_its_limits():
    for ip in CATALOG.values():
        assert ip.interfaces, f"{ip.id} names no interface"
        for iface in ip.interfaces:
            assert iface in INTERFACES, f"{ip.id} references unknown interface {iface}"
        assert len(ip.known_limits) > 40, f"{ip.id} has no substantive limits"
        assert ip.status in ("constructible", "evaluable_only", "not_implemented")


def test_the_axi_stream_entry_does_not_claim_compatibility_it_lacks():
    """The specific mistake this catalog exists to prevent. AXI4-Stream forbids withdrawing an
    unaccepted `tvalid`; these switches drop a request that loses arbitration. Anything that
    reads as "AXI-Stream ready" would send a user to build against semantics the RTL does not
    honour."""
    axis = INTERFACES["axis"]
    assert "ADAPTER REQUIRED" in axis["fit"]
    assert "skid buffer" in axis["fit"]
    assert "NATIVE" in INTERFACES["obi"]["fit"]


def test_filters_narrow_the_catalog_without_dropping_it():
    assert len(list_ips()) == len(CATALOG)
    obi = list_ips(interface="obi")
    assert obi and all("obi" in ip["interfaces"] for ip in obi)
    assert {ip["id"] for ip in list_ips(status="constructible")} < set(CATALOG)

    # `contains` is a plain substring over the entry text, with the consequences of that:
    # "clos" matches the Clos entry, the hybrid that can use a Clos ingress (useful), and the
    # ring's "closed loop" (not). Pinned rather than special-cased, so the filter behaves as
    # its docstring says and callers can predict it.
    assert {ip["id"] for ip in list_ips(contains="clos")} == {"clos", "hybrid", "noc_ring"}
    assert [ip["id"] for ip in list_ips(contains="three-stage")] == ["clos"]


def test_the_prompt_rendering_keeps_claims_and_their_sources_together():
    """The catalog renders into a model's context the same way mined facts do, and the render
    is where a claim most easily loses its boundary. Every published condition must arrive with
    its attribution, and every measured number must stay labelled as this repo's measurement
    rather than blending into the surrounding prose."""
    from flux_interconnect.catalog import render_for_prompt

    text = render_for_prompt()
    assert "Clos, A Study of Non-Blocking Switching Networks" in text
    assert "Measured in this repo:" in text
    assert "ADAPTER REQUIRED" in text          # the interface caveat survives rendering
    assert "not_implemented" in text           # so do the gaps

    obi_only = render_for_prompt(interface="obi")
    assert "Full crossbar" in obi_only
    assert "Ring / bidirectional ring" not in obi_only
