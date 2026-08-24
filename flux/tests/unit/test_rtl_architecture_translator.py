"""Pure-logic tests for Flux Architecture IR -> mac_array.sv's LANES parameter (no Verilator
execution — see tests/integration/test_rtl_adapter_live.py for that).
"""

from __future__ import annotations

from pathlib import Path

import flux_ir
import pytest
from flux_evaluator_rtl import NotExpressibleError, architecture_ir_to_lanes

FLUX_ROOT = Path(__file__).resolve().parents[2]
SIMPLE_NPU_1D = FLUX_ROOT / "core/ir/architecture/examples/simple-npu-1d-v1.yaml"
SIMPLE_NPU_2D = FLUX_ROOT / "core/ir/architecture/examples/simple-npu-v1.yaml"


def test_simple_npu_1d_gives_lanes_8():
    arch = flux_ir.load_document(SIMPLE_NPU_1D)
    assert architecture_ir_to_lanes(arch) == 8


def test_2d_compute_node_is_rejected():
    arch = flux_ir.load_document(SIMPLE_NPU_2D)
    with pytest.raises(NotExpressibleError, match="only models a single spatial dimension"):
        architecture_ir_to_lanes(arch)


def test_zero_compute_nodes_is_rejected():
    arch = {"id": "x", "hierarchy": [{"level": "buf", "class": "memory", "attrs": {"size_kb": 1}}]}
    with pytest.raises(NotExpressibleError, match="0 compute nodes"):
        architecture_ir_to_lanes(arch)


def test_multiple_compute_nodes_is_rejected():
    arch = {
        "id": "x",
        "hierarchy": [
            {"level": "a", "class": "compute", "attrs": {"dims": {"X": 4}}},
            {"level": "b", "class": "compute", "attrs": {"dims": {"X": 4}}},
        ],
    }
    with pytest.raises(NotExpressibleError, match="2 compute nodes"):
        architecture_ir_to_lanes(arch)


def test_compute_node_without_dims_is_rejected():
    arch = {"id": "x", "hierarchy": [{"level": "arr", "class": "compute", "attrs": {}}]}
    with pytest.raises(NotExpressibleError, match="no attrs.dims"):
        architecture_ir_to_lanes(arch)
