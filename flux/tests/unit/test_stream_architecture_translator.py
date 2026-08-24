"""Unit tests for Flux Architecture IR (interconnect.multi_core) -> Stream hardware YAML
translation (docs/decisions.md D82). Reads Stream's own real, installed package for its bundled
`offchip.yaml` (fast, local file I/O, not a real solve) — no real Stream solver is invoked here.
See tests/integration/test_stream_multicore_live.py for the real end-to-end version.
"""

from __future__ import annotations

from pathlib import Path

import flux_ir
import pytest
import yaml
from flux_evaluator_stream import NotExpressibleError, architecture_ir_to_stream_hardware_yaml

FLUX_ROOT = Path(__file__).resolve().parents[2]
DUAL_CORE = FLUX_ROOT / "core/ir/architecture/examples/simple-npu-1d-dual-core-v1.yaml"


def _core_arch(level_extra=None) -> dict:
    hierarchy = [
        {"level": "gbuf", "class": "memory", "attrs": {"size_kb": 512}},
        {"level": "pe_array", "class": "compute", "attrs": {"dims": {"X": 8}}},
    ]
    if level_extra:
        hierarchy.insert(0, level_extra)
    return {"schema_version": "0.1.0", "id": "core-test", "hierarchy": hierarchy}


def test_the_real_dual_core_example_translates_correctly(tmp_path):
    arch = flux_ir.load_document(DUAL_CORE)
    hw_path = architecture_ir_to_stream_hardware_yaml(arch, tmp_path)

    assert hw_path.name == "hardware.yaml"
    hw = yaml.safe_load(hw_path.read_text())
    assert hw["cores"] == {0: "./core_0.yaml", 1: "./core_1.yaml", 2: "./core_2.yaml"}
    assert hw["offchip_core_id"] == 2
    assert hw["core_coordinates"] == {0: [0, 0], 1: [1, 0]}
    assert len(hw["core_connectivity"]) == 3

    core0 = yaml.safe_load((tmp_path / "core_0.yaml").read_text())
    assert core0["name"] == "core_0"
    assert core0["type"] == "zigzag.compute"
    assert core0["operational_array"]["sizes"] == [8]

    offchip = yaml.safe_load((tmp_path / "core_2.yaml").read_text())
    assert offchip["type"] == "zigzag.offchip"


def test_no_multi_core_block_is_rejected(tmp_path):
    arch = {"schema_version": "0.1.0", "id": "x", "hierarchy": []}
    with pytest.raises(NotExpressibleError, match="no interconnect.multi_core"):
        architecture_ir_to_stream_hardware_yaml(arch, tmp_path)


def test_empty_cores_is_rejected(tmp_path):
    arch = {"schema_version": "0.1.0", "id": "x", "hierarchy": [], "interconnect": {"multi_core": {"cores": []}}}
    with pytest.raises(NotExpressibleError, match="cores is empty"):
        architecture_ir_to_stream_hardware_yaml(arch, tmp_path)


def test_core_missing_architecture_is_rejected(tmp_path):
    arch = {
        "schema_version": "0.1.0", "id": "x", "hierarchy": [],
        "interconnect": {"multi_core": {"cores": [{"id": 0}]}},
    }
    with pytest.raises(NotExpressibleError, match="has no inline 'architecture'"):
        architecture_ir_to_stream_hardware_yaml(arch, tmp_path)


def test_core_declaring_a_dram_level_is_rejected(tmp_path):
    dram_core = _core_arch(level_extra={"level": "dram", "class": "memory", "attrs": {"size_kb": 1048576}})
    arch = {
        "schema_version": "0.1.0", "id": "x", "hierarchy": [],
        "interconnect": {"multi_core": {"cores": [{"id": 0, "architecture": dram_core}]}},
    }
    with pytest.raises(NotExpressibleError, match="dram-class memory level"):
        architecture_ir_to_stream_hardware_yaml(arch, tmp_path)


def test_duplicate_core_ids_are_rejected(tmp_path):
    arch = {
        "schema_version": "0.1.0", "id": "x", "hierarchy": [],
        "interconnect": {"multi_core": {"cores": [
            {"id": 0, "architecture": _core_arch()},
            {"id": 0, "architecture": _core_arch()},
        ]}},
    }
    with pytest.raises(NotExpressibleError, match="duplicate core id"):
        architecture_ir_to_stream_hardware_yaml(arch, tmp_path)


def test_core_links_referencing_an_undeclared_core_is_rejected(tmp_path):
    arch = {
        "schema_version": "0.1.0", "id": "x", "hierarchy": [],
        "interconnect": {"multi_core": {
            "cores": [{"id": 0, "architecture": _core_arch()}],
            "core_links": [{"cores": [0, 99], "bandwidth": 32}],
        }},
    }
    with pytest.raises(NotExpressibleError, match="undeclared core id"):
        architecture_ir_to_stream_hardware_yaml(arch, tmp_path)


def test_core_links_with_non_positive_bandwidth_is_rejected(tmp_path):
    arch = {
        "schema_version": "0.1.0", "id": "x", "hierarchy": [],
        "interconnect": {"multi_core": {
            "cores": [
                {"id": 0, "architecture": _core_arch()},
                {"id": 1, "architecture": _core_arch()},
            ],
            "core_links": [{"cores": [0, 1], "bandwidth": 0}],
        }},
    }
    with pytest.raises(NotExpressibleError, match="no positive numeric bandwidth"):
        architecture_ir_to_stream_hardware_yaml(arch, tmp_path)


def test_offchip_core_id_colliding_with_a_compute_core_is_rejected(tmp_path):
    arch = {
        "schema_version": "0.1.0", "id": "x", "hierarchy": [],
        "interconnect": {"multi_core": {
            "cores": [{"id": 0, "architecture": _core_arch()}],
            "offchip_core_id": 0,
        }},
    }
    with pytest.raises(NotExpressibleError, match="collides with a real compute core"):
        architecture_ir_to_stream_hardware_yaml(arch, tmp_path)


def test_a_shared_bus_link_among_more_than_two_cores_is_typed_bus(tmp_path):
    arch = {
        "schema_version": "0.1.0", "id": "x", "hierarchy": [],
        "interconnect": {"multi_core": {
            "cores": [
                {"id": 0, "architecture": _core_arch()},
                {"id": 1, "architecture": _core_arch()},
                {"id": 2, "architecture": _core_arch()},
            ],
            "core_links": [{"cores": [0, 1, 2], "bandwidth": 128}],
        }},
    }
    hw_path = architecture_ir_to_stream_hardware_yaml(arch, tmp_path)
    hw = yaml.safe_load(hw_path.read_text())
    assert hw["core_connectivity"][0]["type"] == "bus"
    assert hw["core_connectivity"][0]["cores"] == [0, 1, 2]


def test_no_coordinates_declared_omits_core_coordinates_entirely(tmp_path):
    arch = {
        "schema_version": "0.1.0", "id": "x", "hierarchy": [],
        "interconnect": {"multi_core": {"cores": [{"id": 0, "architecture": _core_arch()}]}},
    }
    hw_path = architecture_ir_to_stream_hardware_yaml(arch, tmp_path)
    hw = yaml.safe_load(hw_path.read_text())
    assert "core_coordinates" not in hw
