"""Pure-logic tests for the Flux -> Timeloop workload translation (no Docker/Timeloop execution
— see tests/integration/test_timeloop_adapter_live.py for that).
"""

from __future__ import annotations

from pathlib import Path

import flux_ir
import pytest
from flux_evaluator_timeloop import NotExpressibleError, einsum_op_to_timeloop_instance

FLUX_ROOT = Path(__file__).resolve().parents[2]
GEMM_WORKLOAD = FLUX_ROOT / "ir/workload/examples/mlp-gemm0.yaml"
LLM_WORKLOAD = FLUX_ROOT / "ir/workload/examples/llama3-8b-decode-layer0.yaml"


def test_attn_qk_op_is_rejected_for_being_4d_not_2d_gemm():
    """attn.qk ('b h s d, b h t d -> b h s t') is 4 dims per operand — this translator only
    handles plain 2D GEMM, so it must fail on the dim-count check, before ever looking at
    whether any bound is dynamic. See test_op_with_dynamic_bound_is_rejected for that case."""
    workload = flux_ir.load_document(LLM_WORKLOAD)
    op = next(o for o in workload["ops"] if o["id"] == "attn.qk")
    with pytest.raises(NotExpressibleError, match="exactly two dims"):
        einsum_op_to_timeloop_instance(op)


def test_gemm_example_translates_to_instance_overrides():
    workload = flux_ir.load_document(GEMM_WORKLOAD)
    op = workload["ops"][0]
    overrides = einsum_op_to_timeloop_instance(op)
    assert overrides == {"N": 4, "C": 32, "M": 32}


@pytest.mark.parametrize("kind", ["data_dependent", "compute_kernel"])
def test_non_einsum_op_is_rejected(kind):
    op = {"id": "x", "kind": kind, "semantics": {}}
    with pytest.raises(NotExpressibleError, match="only 'einsum' ops"):
        einsum_op_to_timeloop_instance(op)


def test_op_with_dynamic_bound_is_rejected():
    op = {
        "id": "x",
        "kind": "einsum",
        "expr": "b c, c k -> b k",
        "bounds": {"b": 4, "c": {"dyn": [1, 128]}, "k": 32},
    }
    with pytest.raises(NotExpressibleError, match="non-static bound"):
        einsum_op_to_timeloop_instance(op)


def test_operand_with_wrong_dim_count_is_rejected():
    op = {"id": "x", "kind": "einsum", "expr": "a, a b -> b", "bounds": {"a": 1, "b": 2}}
    with pytest.raises(NotExpressibleError, match="exactly two dims"):
        einsum_op_to_timeloop_instance(op)


def test_no_shared_reduction_dim_is_rejected():
    op = {
        "id": "x",
        "kind": "einsum",
        "expr": "a b, c d -> a d",
        "bounds": {"a": 1, "b": 2, "c": 3, "d": 4},
    }
    with pytest.raises(NotExpressibleError, match="exactly one dim shared"):
        einsum_op_to_timeloop_instance(op)


def test_transposed_output_is_rejected():
    """out_dims must be [batch, output] in that order — 'b c, c k -> k b' is a valid einsum
    (transposed GEMM) but not one this v0.1 translator's fixed problem shape can represent."""
    op = {
        "id": "x",
        "kind": "einsum",
        "expr": "b c, c k -> k b",
        "bounds": {"b": 1, "c": 2, "k": 3},
    }
    with pytest.raises(NotExpressibleError, match="no transposed output"):
        einsum_op_to_timeloop_instance(op)


def test_missing_expr_is_rejected():
    op = {"id": "x", "kind": "einsum", "bounds": {}}
    with pytest.raises(NotExpressibleError, match="missing 'expr'"):
        einsum_op_to_timeloop_instance(op)
