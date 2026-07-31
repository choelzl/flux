"""Pure-logic tests for Flux Architecture IR -> ZigZag accelerator-YAML translation (no ZigZag
execution — see tests/integration/test_zigzag_architecture_translation_live.py for that).
"""

from __future__ import annotations

from pathlib import Path

import flux_ir
import pytest
from flux_evaluator_zigzag import NotExpressibleError, architecture_ir_to_zigzag_accelerator

FLUX_ROOT = Path(__file__).resolve().parents[2]
SIMPLE_NPU = FLUX_ROOT / "ir/architecture/examples/simple-npu-v1.yaml"


def test_simple_npu_translates_to_a_valid_shape():
    arch = flux_ir.load_document(SIMPLE_NPU)
    accel = architecture_ir_to_zigzag_accelerator(arch)

    assert accel["name"] == "simple-npu/v1"
    assert accel["operational_array"]["dimensions"] == ["D1", "D2"]
    assert accel["operational_array"]["sizes"] == [8, 8]
    assert set(accel["memories"]) == {"dram", "gbuf"}
    for mem in accel["memories"].values():
        assert mem["operands"] == ["I1", "I2", "O"]
        assert mem["served_dimensions"] == ["D1", "D2"]
        assert len(mem["ports"]) == 1
        assert mem["ports"][0]["type"] == "read_write"


def test_memory_size_kb_converts_to_bits():
    arch = flux_ir.load_document(SIMPLE_NPU)
    accel = architecture_ir_to_zigzag_accelerator(arch)
    assert accel["memories"]["gbuf"]["size"] == 512 * 1024 * 8


def test_compute_dim_order_is_preserved_and_renamed_to_d1_d2():
    arch = {
        "id": "x",
        "hierarchy": [
            {"level": "buf", "class": "memory", "attrs": {"size_kb": 64}},
            {"level": "arr", "class": "compute", "attrs": {"dims": {"Y": 16, "X": 4}}},
        ],
    }
    accel = architecture_ir_to_zigzag_accelerator(arch)
    # dict insertion order (Y then X) maps to D1 then D2 — original names are not preserved.
    assert accel["operational_array"]["dimensions"] == ["D1", "D2"]
    assert accel["operational_array"]["sizes"] == [16, 4]


def test_zero_compute_nodes_is_rejected():
    arch = {"id": "x", "hierarchy": [{"level": "buf", "class": "memory", "attrs": {"size_kb": 1}}]}
    with pytest.raises(NotExpressibleError, match="0 compute nodes"):
        architecture_ir_to_zigzag_accelerator(arch)


def test_multiple_compute_nodes_is_rejected():
    arch = {
        "id": "x",
        "hierarchy": [
            {"level": "a", "class": "compute", "attrs": {"dims": {"X": 4}}},
            {"level": "b", "class": "compute", "attrs": {"dims": {"X": 4}}},
        ],
    }
    with pytest.raises(NotExpressibleError, match="2 compute nodes"):
        architecture_ir_to_zigzag_accelerator(arch)


def test_compute_node_without_dims_is_rejected():
    arch = {"id": "x", "hierarchy": [{"level": "arr", "class": "compute", "attrs": {}}]}
    with pytest.raises(NotExpressibleError, match="no attrs.dims"):
        architecture_ir_to_zigzag_accelerator(arch)


def test_no_memory_nodes_is_rejected():
    arch = {"id": "x", "hierarchy": [{"level": "arr", "class": "compute", "attrs": {"dims": {"X": 4}}}]}
    with pytest.raises(NotExpressibleError, match="no memory-class"):
        architecture_ir_to_zigzag_accelerator(arch)


def test_memory_without_size_kb_is_rejected():
    arch = {
        "id": "x",
        "hierarchy": [
            {"level": "buf", "class": "memory", "attrs": {}},
            {"level": "arr", "class": "compute", "attrs": {"dims": {"X": 4}}},
        ],
    }
    with pytest.raises(NotExpressibleError, match="no numeric attrs.size_kb"):
        architecture_ir_to_zigzag_accelerator(arch)


def test_generic_riscv_soc_example_is_rejected_no_compute_dims():
    """generic-riscv-soc-v1.yaml (docs/00-decisions.md D1's general-SoC example) has a `cpu0`
    compute node with no `dims` (a CPU core, not a systolic array) — this translator is
    DNN-accelerator-shaped and must fail loudly on it, not guess."""
    arch = flux_ir.load_document(FLUX_ROOT / "ir/architecture/examples/generic-riscv-soc-v1.yaml")
    with pytest.raises(NotExpressibleError):
        architecture_ir_to_zigzag_accelerator(arch)
