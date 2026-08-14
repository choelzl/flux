"""Unit tests for flux_evaluator_booksim.architecture_translator: pure translation logic over
synthetic architecture dicts, no real Booksim2 involved. See
tests/integration/test_booksim_adapter_live.py for the real-simulation version, and
tests/integration/test_booksim_chiplet_live.py for the real chiplet D2D interconnect version
(docs/decisions.md D66).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from flux_evaluator_booksim import (
    NotExpressibleError,
    architecture_ir_to_booksim_config,
    architecture_ir_to_chiplet_anynet,
    dump_booksim_config,
)


def _arch(noc: dict | None) -> dict:
    doc = {
        "schema_version": "0.1.0",
        "id": "test/noc-arch",
        "hierarchy": [{"level": "router_fabric", "class": "interconnect", "attrs": {}}],
    }
    if noc is not None:
        doc["interconnect"] = {"noc": noc}
    return doc


def _chiplet_arch(chiplet_noc: dict | None) -> dict:
    doc = {
        "schema_version": "0.1.0",
        "id": "test/chiplet-arch",
        "hierarchy": [{"level": "chiplet_fabric", "class": "interconnect", "attrs": {}}],
    }
    if chiplet_noc is not None:
        doc["interconnect"] = {"chiplet_noc": chiplet_noc}
    return doc


_TWO_DIES = [{"id": "die0", "nodes": 4}, {"id": "die1", "nodes": 4}]
_ONE_D2D_LINK = [{"from": "die0", "to": "die1", "latency_cycles": 20}]


def test_translates_a_2d_mesh():
    config = architecture_ir_to_booksim_config(_arch({"topology": "mesh", "dimensions": [8, 8]}))
    assert config["topology"] == "mesh"
    assert config["k"] == 8
    assert config["n"] == 2


def test_translates_a_3d_mesh():
    config = architecture_ir_to_booksim_config(_arch({"topology": "mesh", "dimensions": [4, 4, 4]}))
    assert config["k"] == 4
    assert config["n"] == 3


def test_translates_a_torus():
    config = architecture_ir_to_booksim_config(_arch({"topology": "torus", "dimensions": [4, 4, 4]}))
    assert config["topology"] == "torus"


def test_defaults_are_applied_when_not_specified():
    config = architecture_ir_to_booksim_config(_arch({"topology": "mesh", "dimensions": [4, 4]}))
    assert config["routing_function"] == "dim_order"
    assert config["num_vcs"] == 8
    assert config["vc_buf_size"] == 8
    assert config["traffic"] == "uniform"
    assert config["injection_rate"] == 0.05
    assert config["packet_size"] == 1


def test_default_routing_function_is_dim_order_not_dor_for_torus_too():
    """docs/decisions.md D15: 'dor' silently worked for mesh ('dor_mesh' is a real Booksim2
    alias) but crashes Booksim2 itself for torus (no 'dor_torus' alias exists) — 'dim_order' is
    valid for both, so it's the universal default, not a topology-conditional one.
    """
    config = architecture_ir_to_booksim_config(_arch({"topology": "torus", "dimensions": [4, 4]}))
    assert config["routing_function"] == "dim_order"


def test_dor_routing_function_with_torus_raises_before_reaching_booksim2():
    """The exact real failure this repo hit: 'dor' + torus reaches Booksim2 as 'dor_torus', which
    doesn't exist, and crashes the simulator itself rather than failing in Python. Now caught
    here instead.
    """
    with pytest.raises(NotExpressibleError, match="not valid for topology='torus'"):
        architecture_ir_to_booksim_config(
            _arch({"topology": "torus", "dimensions": [4, 4], "routing_function": "dor"})
        )


def test_dor_routing_function_with_mesh_still_works():
    """'dor' remains valid for mesh ('dor_mesh' is a real Booksim2 alias) — the fix only rejects
    the one known-bad combination, not 'dor' universally.
    """
    config = architecture_ir_to_booksim_config(
        _arch({"topology": "mesh", "dimensions": [4, 4], "routing_function": "dor"})
    )
    assert config["routing_function"] == "dor"


def test_explicit_values_override_defaults():
    config = architecture_ir_to_booksim_config(_arch({
        "topology": "mesh", "dimensions": [4, 4],
        "routing_function": "min_adapt", "num_vcs": 4, "traffic": "transpose",
        "injection_rate": 0.01, "packet_size": 20,
    }))
    assert config["routing_function"] == "min_adapt"
    assert config["num_vcs"] == 4
    assert config["traffic"] == "transpose"
    assert config["injection_rate"] == 0.01
    assert config["packet_size"] == 20


def test_missing_noc_block_raises():
    with pytest.raises(NotExpressibleError, match="no interconnect.noc block"):
        architecture_ir_to_booksim_config(_arch(None))


def test_descriptive_only_topology_raises_not_a_schema_error():
    """my-npu-v3.yaml/generic-riscv-soc-v1.yaml's real style ('mesh_4x4', 'crossbar') — valid
    Architecture IR, just not translatable by this adapter."""
    with pytest.raises(NotExpressibleError, match="not one of"):
        architecture_ir_to_booksim_config(_arch({"topology": "mesh_4x4", "flit_bits": 256}))


def test_crossbar_topology_raises():
    with pytest.raises(NotExpressibleError):
        architecture_ir_to_booksim_config(_arch({"topology": "crossbar"}))


def test_missing_dimensions_raises():
    with pytest.raises(NotExpressibleError, match="dimensions is required"):
        architecture_ir_to_booksim_config(_arch({"topology": "mesh"}))


def test_unequal_dimensions_raises():
    with pytest.raises(NotExpressibleError, match="not all equal"):
        architecture_ir_to_booksim_config(_arch({"topology": "mesh", "dimensions": [4, 8]}))


def test_dump_booksim_config_renders_key_value_lines():
    text = dump_booksim_config({"topology": "mesh", "k": 4, "n": 3})
    assert "topology = mesh;" in text
    assert "k = 4;" in text
    assert "n = 3;" in text


# --- chiplet inter-die (D2D) interconnect, docs/decisions.md D66/D67 ---


def test_missing_chiplet_noc_block_raises():
    with pytest.raises(NotExpressibleError, match="no interconnect.chiplet_noc block"):
        architecture_ir_to_chiplet_anynet(_chiplet_arch(None))


def test_fewer_than_two_dies_raises():
    with pytest.raises(NotExpressibleError, match="at least 2"):
        architecture_ir_to_chiplet_anynet(
            _chiplet_arch({"dies": [{"id": "die0", "nodes": 4}], "d2d_links": _ONE_D2D_LINK})
        )


def test_duplicate_die_ids_raise():
    with pytest.raises(NotExpressibleError, match="duplicate ids"):
        architecture_ir_to_chiplet_anynet(
            _chiplet_arch(
                {
                    "dies": [{"id": "die0", "nodes": 4}, {"id": "die0", "nodes": 4}],
                    "d2d_links": _ONE_D2D_LINK,
                }
            )
        )


def test_missing_d2d_link_raises():
    with pytest.raises(NotExpressibleError, match="at least 1"):
        architecture_ir_to_chiplet_anynet(_chiplet_arch({"dies": _TWO_DIES, "d2d_links": []}))


def test_duplicate_link_between_the_same_die_pair_raises():
    with pytest.raises(NotExpressibleError, match="more than one d2d_link"):
        architecture_ir_to_chiplet_anynet(
            _chiplet_arch(
                {
                    "dies": _TWO_DIES,
                    "d2d_links": [
                        {"from": "die0", "to": "die1", "latency_cycles": 20},
                        {"from": "die1", "to": "die0", "latency_cycles": 30},
                    ],
                }
            )
        )


def test_d2d_link_referencing_an_unknown_die_id_raises():
    with pytest.raises(NotExpressibleError, match="from/to"):
        architecture_ir_to_chiplet_anynet(
            _chiplet_arch(
                {"dies": _TWO_DIES, "d2d_links": [{"from": "die0", "to": "die99", "latency_cycles": 20}]}
            )
        )


def test_d2d_link_from_a_die_to_itself_raises():
    with pytest.raises(NotExpressibleError, match="from/to"):
        architecture_ir_to_chiplet_anynet(
            _chiplet_arch(
                {"dies": _TWO_DIES, "d2d_links": [{"from": "die0", "to": "die0", "latency_cycles": 20}]}
            )
        )


def test_config_is_a_real_anynet_topology():
    chiplet = architecture_ir_to_chiplet_anynet(
        _chiplet_arch({"dies": _TWO_DIES, "d2d_links": _ONE_D2D_LINK})
    )
    assert chiplet.config["topology"] == "anynet"
    assert chiplet.config["network_file"] == "chiplet.anynet"


def test_anynet_file_declares_every_node_and_the_d2d_link_latency_both_directions():
    chiplet = architecture_ir_to_chiplet_anynet(
        _chiplet_arch({"dies": _TWO_DIES, "d2d_links": _ONE_D2D_LINK})
    )
    lines = chiplet.anynet_file_content.strip().splitlines()
    assert len(lines) == 2  # one line per die's own router
    # die0's router (0) connects to nodes 0-3 and router 1 at the declared D2D latency.
    assert "router 0 node 0 node 1 node 2 node 3 router 1 20" == lines[0]
    # die1's router (1) connects to nodes 4-7 and router 0 at the *same* declared D2D latency —
    # a real, symmetric physical link, not a one-way discount (Booksim2's own channel latency is
    # otherwise unidirectional per declaration).
    assert "router 1 node 4 node 5 node 6 node 7 router 0 20" == lines[1]


def test_unequal_die_node_counts_produce_disjoint_sequential_node_ids():
    chiplet = architecture_ir_to_chiplet_anynet(
        _chiplet_arch(
            {
                "dies": [{"id": "die0", "nodes": 2}, {"id": "die1", "nodes": 6}],
                "d2d_links": _ONE_D2D_LINK,
            }
        )
    )
    assert "router 0 node 0 node 1 router 1 20" in chiplet.anynet_file_content
    assert "router 1 node 2 node 3 node 4 node 5 node 6 node 7 router 0 20" in chiplet.anynet_file_content


def test_defaults_and_overrides():
    chiplet = architecture_ir_to_chiplet_anynet(
        _chiplet_arch({"dies": _TWO_DIES, "d2d_links": _ONE_D2D_LINK})
    )
    assert chiplet.config["routing_function"] == "min"
    assert chiplet.config["traffic"] == "uniform"
    assert chiplet.config["num_vcs"] == 8

    chiplet2 = architecture_ir_to_chiplet_anynet(
        _chiplet_arch(
            {"dies": _TWO_DIES, "d2d_links": _ONE_D2D_LINK, "routing_function": "dor", "num_vcs": 2}
        )
    )
    assert chiplet2.config["routing_function"] == "dor"
    assert chiplet2.config["num_vcs"] == 2


# --- N-die / M-link generalization, docs/decisions.md D67 ---

_THREE_DIES = [{"id": "die0", "nodes": 4}, {"id": "die1", "nodes": 4}, {"id": "die2", "nodes": 4}]
_CHAIN_LINKS = [
    {"from": "die0", "to": "die1", "latency_cycles": 20},
    {"from": "die1", "to": "die2", "latency_cycles": 20},
]


def test_three_die_chain_declares_every_router_once():
    chiplet = architecture_ir_to_chiplet_anynet(
        _chiplet_arch({"dies": _THREE_DIES, "d2d_links": _CHAIN_LINKS})
    )
    lines = chiplet.anynet_file_content.strip().splitlines()
    assert len(lines) == 3
    assert lines[0] == "router 0 node 0 node 1 node 2 node 3 router 1 20"
    # die1 (the middle of the chain) connects to *both* neighbours — a real router can have more
    # than one D2D link, not just the single one v0.1 was scoped to.
    assert lines[1] == "router 1 node 4 node 5 node 6 node 7 router 0 20 router 2 20"
    assert lines[2] == "router 2 node 8 node 9 node 10 node 11 router 1 20"


def test_a_die_not_touched_by_any_d2d_link_gets_no_router_entries():
    """A real, valid topology: not every die needs a direct link to every other one."""
    chiplet = architecture_ir_to_chiplet_anynet(
        _chiplet_arch(
            {
                "dies": _THREE_DIES,
                "d2d_links": [{"from": "die0", "to": "die1", "latency_cycles": 20}],
            }
        )
    )
    lines = chiplet.anynet_file_content.strip().splitlines()
    # die2 has no D2D link at all — its line is just its own nodes, no "router" targets.
    assert lines[2] == "router 2 node 8 node 9 node 10 node 11"


def test_node_ids_stay_sequential_and_disjoint_across_three_dies():
    chiplet = architecture_ir_to_chiplet_anynet(
        _chiplet_arch({"dies": _THREE_DIES, "d2d_links": _CHAIN_LINKS})
    )
    all_node_ids = [
        int(tok) for line in chiplet.anynet_file_content.strip().splitlines()
        for i, tok in enumerate(line.split()) if line.split()[i - 1] == "node"
    ]
    assert all_node_ids == list(range(12))  # 4 + 4 + 4, no gaps, no repeats


def test_stat_regexes_read_scientific_notation():
    """C++ ostream prints a double in scientific notation outside a narrow range, and the capture
    used to be `[\\d.]+` — so a real Booksim2 latency of 1234567.0, printed as "1.23457e+06", was
    parsed as **1.23457**: wrong by a factor of a million, silently, straight into
    `Result.metrics` (docs/decisions.md D181).

    A saturated network is exactly when latencies get large enough to cross into that formatting,
    which is also exactly when the number matters.
    """
    from flux_evaluator_booksim.adapter import _HOPS_RE, _LATENCY_RE

    assert float(_LATENCY_RE.search("Packet latency average = 1.23457e+06").group(1)) == 1234570.0
    assert float(_LATENCY_RE.search("Packet latency average = 42.5").group(1)) == 42.5
    assert float(_HOPS_RE.search("Hops average = 3.75").group(1)) == 3.75


def test_a_stat_line_without_a_number_still_does_not_match():
    """The looser character class must not start matching label text."""
    from flux_evaluator_booksim.adapter import _LATENCY_RE

    assert _LATENCY_RE.search("Packet latency average = nan") is None


def test_the_parser_reads_real_captured_booksim_output():
    """Against real Booksim2 output rather than a hand-written string (docs/decisions.md D182).

    The capture is a genuinely congested run — 8x8 mesh, transpose traffic, injection_rate 0.95,
    single-entry buffers — which is as close to the pathological end as Booksim2 will go before it
    aborts convergence. It reports 4928.11 cycles in fixed notation, which is the point: D181 fixed
    the parser for scientific notation and asserted those magnitudes were what these simulators
    produce when it matters. For Booksim2 that was overstated, and this fixture is the evidence.
    """
    from flux_evaluator_booksim.adapter import _LATENCY_RE

    captured = (
        Path(__file__).resolve().parents[1] / "golden" / "booksim_congested_output.txt"
    ).read_text()

    matches = _LATENCY_RE.findall(captured)

    assert matches, "no latency line found in real captured output"
    assert float(matches[-1]) == 4928.11
    assert "e+" not in captured.split("Packet latency average")[1][:40], (
        "this capture is the fixed-notation case on purpose"
    )
