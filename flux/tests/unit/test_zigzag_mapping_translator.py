"""Pure-logic tests for Flux Mapping IR -> ZigZag mapping-YAML translation (no ZigZag execution
— see tests/integration/test_calibration_live.py for a real run through this translator).
"""

from __future__ import annotations

from pathlib import Path

import flux_ir
import pytest
from flux_evaluator_zigzag import NotExpressibleError, mapping_ir_to_zigzag_mapping

FLUX_ROOT = Path(__file__).resolve().parents[2]
MAPPING = FLUX_ROOT / "core/ir/mapping/examples/mlp-gemm0-simple-npu-1d-map0.yaml"
ARCH = FLUX_ROOT / "core/ir/architecture/examples/simple-npu-1d-v1.yaml"


def _mapping_and_arch():
    return flux_ir.load_document(MAPPING), flux_ir.load_document(ARCH)


def test_mlp_gemm0_map0_translates_to_a_valid_shape():
    mapping, arch = _mapping_and_arch()
    entry = mapping_ir_to_zigzag_mapping(mapping, arch)

    assert entry["name"] == "default"
    assert entry["spatial_mapping"] == {"D1": ["K, 8"]}
    assert entry["temporal_ordering"] == [["B", 4], ["C", 32], ["K", 4]]


def test_two_dim_array_dim_resolves_by_insertion_order():
    arch = {
        "id": "x",
        "hierarchy": [
            {"level": "buf", "class": "memory", "attrs": {"size_kb": 64}},
            {"level": "arr", "class": "compute", "attrs": {"dims": {"Y": 16, "X": 4}}},
        ],
    }
    mapping = {
        "id": "m",
        "for_op": "op",
        "operands": {"A": [{"level": "buf", "loops": [{"dim": "p", "size": 2, "order": 0}]}]},
        "spatial": [{"dim": "q", "array_dim": "X", "size": 4}],
    }
    entry = mapping_ir_to_zigzag_mapping(mapping, arch)
    # insertion order (Y then X) maps to D1 then D2, same convention as architecture_translator.
    assert entry["spatial_mapping"] == {"D2": ["q, 4"]}


def test_no_operands_is_rejected():
    _, arch = _mapping_and_arch()
    mapping = {"id": "m", "for_op": "op", "operands": {}}
    with pytest.raises(NotExpressibleError, match="no operands"):
        mapping_ir_to_zigzag_mapping(mapping, arch)


def test_loop_without_order_is_rejected():
    _, arch = _mapping_and_arch()
    mapping = {
        "id": "m",
        "for_op": "op",
        "operands": {"A": [{"level": "buf", "loops": [{"dim": "p", "size": 2}]}]},
    }
    with pytest.raises(NotExpressibleError, match="no 'order'"):
        mapping_ir_to_zigzag_mapping(mapping, arch)


def test_uneven_operand_loop_nests_are_rejected():
    _, arch = _mapping_and_arch()
    mapping = {
        "id": "m",
        "for_op": "op",
        "operands": {
            "A": [{"level": "buf", "loops": [{"dim": "p", "size": 2, "order": 0}]}],
            "B": [{"level": "buf", "loops": [{"dim": "p", "size": 4, "order": 0}]}],
        },
    }
    with pytest.raises(NotExpressibleError, match="uneven mapping"):
        mapping_ir_to_zigzag_mapping(mapping, arch)


def test_fusion_is_rejected():
    _, arch = _mapping_and_arch()
    mapping = {
        "id": "m",
        "for_op": "op",
        "operands": {"A": [{"level": "buf", "loops": [{"dim": "p", "size": 2, "order": 0}]}]},
        "fusion": {"group": ["op1", "op2"]},
    }
    with pytest.raises(NotExpressibleError, match="fusion"):
        mapping_ir_to_zigzag_mapping(mapping, arch)


def test_placement_is_rejected():
    _, arch = _mapping_and_arch()
    mapping = {
        "id": "m",
        "for_op": "op",
        "operands": {"A": [{"level": "buf", "loops": [{"dim": "p", "size": 2, "order": 0}]}]},
        "placement": {"core": "cluster0.core3"},
    }
    with pytest.raises(NotExpressibleError, match="placement"):
        mapping_ir_to_zigzag_mapping(mapping, arch)


def test_unknown_spatial_array_dim_is_rejected():
    _, arch = _mapping_and_arch()
    mapping = {
        "id": "m",
        "for_op": "op",
        "operands": {"A": [{"level": "buf", "loops": [{"dim": "p", "size": 2, "order": 0}]}]},
        "spatial": [{"dim": "q", "array_dim": "Z", "size": 4}],
    }
    with pytest.raises(NotExpressibleError, match="not one of arch"):
        mapping_ir_to_zigzag_mapping(mapping, arch)
