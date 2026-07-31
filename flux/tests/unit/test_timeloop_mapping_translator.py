"""Pure-logic tests for Flux Mapping IR -> Timeloop `mapspace_constraints` translation (no
Timeloop/Docker execution — see
tests/integration/test_timeloop_mapping_translation_live.py for that).
"""

from __future__ import annotations

from pathlib import Path

import flux_ir
import pytest
from flux_evaluator_timeloop import NotExpressibleError, mapping_ir_to_timeloop_constraints

FLUX_ROOT = Path(__file__).resolve().parents[2]
MAPPING = FLUX_ROOT / "ir/mapping/examples/mlp-gemm0-simple-npu-1d-timeloop-map0.yaml"
ARCH = FLUX_ROOT / "ir/architecture/examples/simple-npu-1d-v1.yaml"
WORKLOAD = FLUX_ROOT / "ir/workload/examples/mlp-gemm0.yaml"


def _mapping_arch_op():
    mapping = flux_ir.load_document(MAPPING)
    arch = flux_ir.load_document(ARCH)
    workload = flux_ir.load_document(WORKLOAD)
    return mapping, arch, workload["ops"][0]


def test_timeloop_map0_translates_to_a_valid_shape():
    mapping, arch, op = _mapping_arch_op()
    constraints = mapping_ir_to_timeloop_constraints(mapping, arch, op)

    assert constraints["version"] == 0.4
    targets = {t["target"]: t for t in constraints["targets"]}
    assert set(targets) == {"dram", "gbuf", "pe_array"}

    gbuf = targets["gbuf"]
    assert gbuf["type"] == "temporal"
    assert gbuf["factors"] == "C=32 M=4 R=1 S=1 N=4 P=1 Q=1 G=1"
    assert gbuf["permutation"] == "MNCRSPQG"

    # Untouched levels get a trivial, fully-degenerate block.
    for level in ("dram", "pe_array"):
        assert targets[level]["factors"] == "C=1 M=1 R=1 S=1 N=1 P=1 Q=1 G=1"
        assert targets[level]["permutation"] == "CMRSNPQG"


def test_spatial_field_is_rejected():
    mapping, arch, op = _mapping_arch_op()
    mapping = dict(mapping, spatial=[{"dim": "K", "array_dim": "X", "size": 8}])
    with pytest.raises(NotExpressibleError, match="spatial"):
        mapping_ir_to_timeloop_constraints(mapping, arch, op)


def test_fusion_is_rejected():
    mapping, arch, op = _mapping_arch_op()
    mapping = dict(mapping, fusion={"group": ["op1", "op2"]})
    with pytest.raises(NotExpressibleError, match="fusion"):
        mapping_ir_to_timeloop_constraints(mapping, arch, op)


def test_placement_is_rejected():
    mapping, arch, op = _mapping_arch_op()
    mapping = dict(mapping, placement={"core": "cluster0.core3"})
    with pytest.raises(NotExpressibleError, match="placement"):
        mapping_ir_to_timeloop_constraints(mapping, arch, op)


def test_no_operands_is_rejected():
    _, arch, op = _mapping_arch_op()
    mapping = {"id": "m", "for_op": "op", "operands": {}}
    with pytest.raises(NotExpressibleError, match="no operands"):
        mapping_ir_to_timeloop_constraints(mapping, arch, op)


def test_loop_without_order_is_rejected():
    _, arch, op = _mapping_arch_op()
    mapping = {
        "id": "m",
        "for_op": "op",
        "operands": {"A": [{"level": "gbuf", "loops": [{"dim": "B", "size": 2}]}]},
    }
    with pytest.raises(NotExpressibleError, match="no 'order'"):
        mapping_ir_to_timeloop_constraints(mapping, arch, op)


def test_loop_without_level_is_rejected():
    _, arch, op = _mapping_arch_op()
    mapping = {
        "id": "m",
        "for_op": "op",
        "operands": {"A": [{"loops": [{"dim": "B", "size": 2, "order": 0}]}]},
    }
    with pytest.raises(NotExpressibleError, match="no 'level'"):
        mapping_ir_to_timeloop_constraints(mapping, arch, op)


def test_unknown_dim_name_is_rejected():
    _, arch, op = _mapping_arch_op()
    mapping = {
        "id": "m",
        "for_op": "op",
        "operands": {
            "A": [{"level": "gbuf", "loops": [{"dim": "nonsense", "size": 2, "order": 0}]}]
        },
    }
    with pytest.raises(NotExpressibleError, match="not one of this op's N/C/M dims"):
        mapping_ir_to_timeloop_constraints(mapping, arch, op)


def test_unknown_level_name_is_rejected():
    _, arch, op = _mapping_arch_op()
    mapping = {
        "id": "m",
        "for_op": "op",
        "operands": {"A": [{"level": "nonsense", "loops": [{"dim": "B", "size": 2, "order": 0}]}]},
    }
    with pytest.raises(NotExpressibleError, match="not one of arch"):
        mapping_ir_to_timeloop_constraints(mapping, arch, op)


def test_uneven_operand_loop_nests_are_rejected():
    _, arch, op = _mapping_arch_op()
    mapping = {
        "id": "m",
        "for_op": "op",
        "operands": {
            "A": [{"level": "gbuf", "loops": [{"dim": "B", "size": 2, "order": 0}]}],
            "B": [{"level": "gbuf", "loops": [{"dim": "B", "size": 4, "order": 0}]}],
        },
    }
    with pytest.raises(NotExpressibleError, match="uneven mapping"):
        mapping_ir_to_timeloop_constraints(mapping, arch, op)
