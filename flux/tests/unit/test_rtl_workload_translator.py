"""Pure-logic tests for Flux Workload IR -> mac_array.sv shape translation (no Verilator
execution — see tests/integration/test_rtl_adapter_live.py for that).
"""

from __future__ import annotations

from pathlib import Path

import flux_ir
import pytest
from flux_evaluator_rtl import NotExpressibleError, einsum_op_to_mac_array_shape

FLUX_ROOT = Path(__file__).resolve().parents[2]
GEMM_WORKLOAD = FLUX_ROOT / "ir/workload/examples/mlp-gemm0.yaml"
LLM_WORKLOAD = FLUX_ROOT / "ir/workload/examples/llama3-8b-decode-layer0.yaml"


def test_gemm_example_translates_to_shape():
    workload = flux_ir.load_document(GEMM_WORKLOAD)
    op = workload["ops"][0]
    shape = einsum_op_to_mac_array_shape(op)
    assert shape == {"B": 4, "C": 32, "K": 32}


def test_attn_qk_op_is_rejected_for_being_4d_not_2d_gemm():
    workload = flux_ir.load_document(LLM_WORKLOAD)
    op = next(o for o in workload["ops"] if o["id"] == "attn.qk")
    with pytest.raises(NotExpressibleError, match="exactly two dims"):
        einsum_op_to_mac_array_shape(op)


@pytest.mark.parametrize("kind", ["data_dependent", "compute_kernel"])
def test_non_einsum_op_is_rejected(kind):
    op = {"id": "x", "kind": kind, "semantics": {}}
    with pytest.raises(NotExpressibleError, match="only 'einsum' ops"):
        einsum_op_to_mac_array_shape(op)


def test_op_with_dynamic_bound_is_rejected():
    op = {
        "id": "x",
        "kind": "einsum",
        "expr": "b c, c k -> b k",
        "bounds": {"b": 4, "c": {"dyn": [1, 128]}, "k": 32},
    }
    with pytest.raises(NotExpressibleError, match="non-static bound"):
        einsum_op_to_mac_array_shape(op)


def test_no_shared_reduction_dim_is_rejected():
    op = {
        "id": "x",
        "kind": "einsum",
        "expr": "a b, c d -> a d",
        "bounds": {"a": 1, "b": 2, "c": 3, "d": 4},
    }
    with pytest.raises(NotExpressibleError, match="exactly one dim shared"):
        einsum_op_to_mac_array_shape(op)


def test_transposed_output_is_rejected():
    op = {
        "id": "x",
        "kind": "einsum",
        "expr": "b c, c k -> k b",
        "bounds": {"b": 1, "c": 2, "k": 3},
    }
    with pytest.raises(NotExpressibleError, match="no transposed output"):
        einsum_op_to_mac_array_shape(op)


def test_missing_expr_is_rejected():
    op = {"id": "x", "kind": "einsum", "bounds": {}}
    with pytest.raises(NotExpressibleError, match="missing 'expr'"):
        einsum_op_to_mac_array_shape(op)
