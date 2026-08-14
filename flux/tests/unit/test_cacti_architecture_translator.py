"""Unit tests for flux_evaluator_cacti.architecture_translator: pure translation logic over
synthetic architecture dicts, no real CACTI involved. See
tests/integration/test_cacti_adapter_live.py for the real-characterization version.
"""

from __future__ import annotations

import pytest
from flux_evaluator_cacti import NotExpressibleError, architecture_ir_to_sram_spec, architecture_ir_to_technology_um


def _arch(hierarchy: list[dict] | None, node: str = "n28") -> dict:
    doc = {
        "schema_version": "0.1.0",
        "id": "test/sram-arch",
        "tech": {"node": node, "pdk_class": "open"},
    }
    if hierarchy is not None:
        doc["hierarchy"] = hierarchy
    return doc


def _mem_node(size_kb=512, word_width_bits=128, level="gbuf", ports=None):
    attrs = {"size_kb": size_kb, "word_width_bits": word_width_bits}
    if ports is not None:
        attrs["ports"] = ports
    return {"level": level, "class": "memory", "attrs": attrs}


def test_translates_a_single_memory_node():
    spec = architecture_ir_to_sram_spec(_arch([_mem_node()]))
    assert spec.name == "gbuf"
    assert spec.width == 128
    assert spec.depth == 512 * 1024 * 8 // 128  # 32768 words


def test_default_ports_are_a_single_unified_rw_port():
    spec = architecture_ir_to_sram_spec(_arch([_mem_node()]))
    assert spec.num_rw_ports == 1
    assert spec.num_read_ports == 0
    assert spec.num_write_ports == 0


def test_explicit_ports_are_read():
    spec = architecture_ir_to_sram_spec(_arch([_mem_node(ports={"r": 2, "w": 1})]))
    assert spec.num_read_ports == 2
    assert spec.num_write_ports == 1
    assert spec.num_rw_ports == 0


def test_zero_memory_nodes_raises():
    with pytest.raises(NotExpressibleError, match="exactly one"):
        architecture_ir_to_sram_spec(_arch([{"level": "pe", "class": "compute", "attrs": {}}]))


def test_two_memory_nodes_raises():
    with pytest.raises(NotExpressibleError, match="exactly one"):
        architecture_ir_to_sram_spec(_arch([_mem_node(level="dram"), _mem_node(level="gbuf")]))


def test_missing_word_width_bits_raises():
    node = {"level": "gbuf", "class": "memory", "attrs": {"size_kb": 512}}
    with pytest.raises(NotExpressibleError, match="word_width_bits"):
        architecture_ir_to_sram_spec(_arch([node]))


def test_size_not_dividing_evenly_by_word_width_raises():
    node = {"level": "gbuf", "class": "memory", "attrs": {"size_kb": 1, "word_width_bits": 7}}
    with pytest.raises(NotExpressibleError, match="does not divide evenly"):
        architecture_ir_to_sram_spec(_arch([node]))


def test_technology_parses_n28_to_28nm_um():
    assert architecture_ir_to_technology_um(_arch([_mem_node()], node="n28")) == pytest.approx(0.028)


def test_sub_22nm_nodes_are_refused_with_the_scaled_route_named():
    """docs/decisions.md D253: CACTI's planar ITRS models end at 22nm — probed for real
    (0.022um runs, 0.016um fails outright), so the translator refuses below the floor with
    the sanctioned characterize-and-scale route in the message, instead of letting CACTI
    crash at runtime with an unparseable error."""
    arch = {"id": "t", "tech": {"node": "n16"}, "hierarchy": []}
    with pytest.raises(NotExpressibleError, match="22nm floor") as exc:
        architecture_ir_to_technology_um(arch)
    assert "cacti_scale_from_nm" in str(exc.value)
    # the floor itself parses
    assert architecture_ir_to_technology_um(
        {"id": "t", "tech": {"node": "n22"}, "hierarchy": []}) == pytest.approx(0.022)

def test_technology_above_90nm_raises():
    """CACTI 7's own real, verified constraint (docs/decisions.md D35: 'Feature size must be
    <= 90 nm', confirmed by actually running it)."""
    with pytest.raises(NotExpressibleError, match="90nm ceiling"):
        architecture_ir_to_technology_um(_arch([_mem_node()], node="n130"))


def test_technology_at_exactly_90nm_is_accepted():
    assert architecture_ir_to_technology_um(_arch([_mem_node()], node="n90")) == pytest.approx(0.090)


def test_unparseable_node_raises():
    with pytest.raises(NotExpressibleError, match="expected 'n<nm>' form"):
        architecture_ir_to_technology_um(_arch([_mem_node()], node="open"))
