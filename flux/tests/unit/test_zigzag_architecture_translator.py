"""Pure-logic tests for Flux Architecture IR -> ZigZag accelerator-YAML translation (no ZigZag
execution — see tests/integration/test_zigzag_architecture_translation_live.py for that).
"""

from __future__ import annotations

from pathlib import Path

import flux_ir
import pytest
from flux_evaluator_zigzag import NotExpressibleError, architecture_ir_to_zigzag_accelerator

FLUX_ROOT = Path(__file__).resolve().parents[2]
SIMPLE_NPU = FLUX_ROOT / "core/ir/architecture/examples/simple-npu-v1.yaml"


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
    """generic-riscv-soc-v1.yaml (docs/decisions.md D1's general-SoC example) has a `cpu0`
    compute node with no `dims` (a CPU core, not a systolic array) — this translator is
    DNN-accelerator-shaped and must fail loudly on it, not guess."""
    arch = flux_ir.load_document(FLUX_ROOT / "core/ir/architecture/examples/generic-riscv-soc-v1.yaml")
    with pytest.raises(NotExpressibleError):
        architecture_ir_to_zigzag_accelerator(arch)


# --- port bandwidth derived from IR attrs, not hardcoded -------------------------------------
# Regression cover for the defect these tests exist because of: a fixed port bandwidth caps
# ZigZag's spatial unrolling at (bandwidth / precision) elements, so an array-width sweep above
# that cap returns identical latencies for every width and looks like a plateau in the design
# space rather than an artifact of the translator.


def _one_memory_arch(attrs: dict, **extra) -> dict:
    return {
        "id": "bw-test",
        "hierarchy": [
            {"level": "buf", "class": "memory", "attrs": {"size_kb": 64, **attrs}},
            {"level": "arr", "class": "compute", "attrs": {"dims": {"X": 8}}},
        ],
        **extra,
    }


def _bandwidth_of(accel: dict, level: str = "buf") -> int:
    port = accel["memories"][level]["ports"][0]
    assert port["bandwidth_min"] == port["bandwidth_max"]
    return port["bandwidth_min"]


def test_a_document_declaring_no_bandwidth_keeps_the_historical_default():
    # simple-npu-v1.yaml and every golden baseline pinned against it live in this branch.
    accel = architecture_ir_to_zigzag_accelerator(_one_memory_arch({}))
    assert _bandwidth_of(accel) == 2048


def test_width_bits_alone_is_one_port_wide():
    accel = architecture_ir_to_zigzag_accelerator(_one_memory_arch({"width_bits": 64}))
    assert _bandwidth_of(accel) == 64


def test_width_bits_is_multiplied_by_the_declared_port_count():
    accel = architecture_ir_to_zigzag_accelerator(
        _one_memory_arch({"width_bits": 64, "ports": {"r": 2, "w": 1}})
    )
    assert _bandwidth_of(accel) == 192


def test_explicit_port_bandwidth_bits_wins_over_width_bits():
    accel = architecture_ir_to_zigzag_accelerator(
        _one_memory_arch({"width_bits": 64, "port_bandwidth_bits": 4096})
    )
    assert _bandwidth_of(accel) == 4096


def test_bw_gbps_converts_through_a_declared_clock():
    # 25.6 GB/s at 1.6 GHz = 25.6 * 8 / 1.6 = 128 bits/cycle.
    accel = architecture_ir_to_zigzag_accelerator(
        _one_memory_arch({"bw_gbps": 25.6}, tech={"node": "n16", "freq_ghz": 1.6})
    )
    assert _bandwidth_of(accel) == 128


def test_bw_gbps_also_reads_a_clock_off_the_compute_node():
    arch = _one_memory_arch({"bw_gbps": 25.6})
    arch["hierarchy"][1]["attrs"]["freq_ghz"] = 1.6
    assert _bandwidth_of(architecture_ir_to_zigzag_accelerator(arch)) == 128


def test_width_bits_beats_bw_gbps_when_both_are_declared():
    accel = architecture_ir_to_zigzag_accelerator(
        _one_memory_arch({"width_bits": 512, "bw_gbps": 25.6}, tech={"freq_ghz": 1.6})
    )
    assert _bandwidth_of(accel) == 512


def test_bw_gbps_without_any_declared_clock_is_refused_not_defaulted():
    with pytest.raises(NotExpressibleError, match="no clock frequency"):
        architecture_ir_to_zigzag_accelerator(_one_memory_arch({"bw_gbps": 25.6}))
