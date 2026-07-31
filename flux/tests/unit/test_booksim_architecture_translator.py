"""Unit tests for flux_evaluator_booksim.architecture_translator: pure translation logic over
synthetic architecture dicts, no real Booksim2 involved. See
tests/integration/test_booksim_adapter_live.py for the real-simulation version.
"""

from __future__ import annotations

import pytest
from flux_evaluator_booksim import NotExpressibleError, architecture_ir_to_booksim_config, dump_booksim_config


def _arch(noc: dict | None) -> dict:
    doc = {
        "schema_version": "0.1.0",
        "id": "test/noc-arch",
        "hierarchy": [{"level": "router_fabric", "class": "interconnect", "attrs": {}}],
    }
    if noc is not None:
        doc["interconnect"] = {"noc": noc}
    return doc


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
    assert config["routing_function"] == "dor"
    assert config["num_vcs"] == 8
    assert config["vc_buf_size"] == 8
    assert config["traffic"] == "uniform"
    assert config["injection_rate"] == 0.05
    assert config["packet_size"] == 1


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
