"""Pure-logic tests for the Flux -> Timeloop workload translation (no Docker/Timeloop execution
— see tests/integration/test_timeloop_adapter_live.py for that).
"""

from __future__ import annotations

from pathlib import Path

import flux_ir
import pytest
from flux_evaluator_timeloop import (
    NotExpressibleError,
    einsum_op_to_timeloop_instance,
    flux_tensor_to_timeloop_dataspace,
    op_sparsity_to_timeloop_densities,
)

FLUX_ROOT = Path(__file__).resolve().parents[2]
GEMM_WORKLOAD = FLUX_ROOT / "core/ir/workload/examples/mlp-gemm0.yaml"
LLM_WORKLOAD = FLUX_ROOT / "core/ir/workload/examples/llama3-8b-decode-layer0.yaml"
SPARSE_GEMM_WORKLOAD = FLUX_ROOT / "core/ir/workload/examples/mlp-gemm0-sparse-v1.yaml"


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


# --- real sparsity (docs/decisions.md D78) ---


def test_flux_tensor_to_timeloop_dataspace_matches_mlp_gemm0_by_rank():
    workload = flux_ir.load_document(GEMM_WORKLOAD)
    op = workload["ops"][0]
    assert flux_tensor_to_timeloop_dataspace(workload, op) == {
        "I": "Inputs", "W": "Weights", "O": "Outputs",
    }


def test_tensor_dataspace_mapping_rejects_a_tensor_with_no_matching_rank():
    workload = flux_ir.load_document(GEMM_WORKLOAD)
    workload["tensors"][0]["rank"] = ["B"]  # I now has only 1 dim, can't match batch+reduction
    op = workload["ops"][0]
    with pytest.raises(NotExpressibleError, match="matched"):
        flux_tensor_to_timeloop_dataspace(workload, op)


def test_op_with_no_sparsity_field_returns_none():
    workload = flux_ir.load_document(GEMM_WORKLOAD)
    op = workload["ops"][0]
    assert op_sparsity_to_timeloop_densities(workload, op) is None


def test_real_sparse_example_translates_to_timeloop_densities():
    workload = flux_ir.load_document(SPARSE_GEMM_WORKLOAD)
    op = workload["ops"][0]
    densities = op_sparsity_to_timeloop_densities(workload, op)
    assert densities == {"Inputs": {"distribution": "hypergeometric", "density": 0.25}}


def test_sparsity_names_an_unknown_tensor_is_rejected():
    workload = flux_ir.load_document(SPARSE_GEMM_WORKLOAD)
    op = dict(workload["ops"][0])
    op["sparsity"] = {"NOT_A_TENSOR": {"distribution": "hypergeometric", "density": 0.5}}
    with pytest.raises(NotExpressibleError, match="not one of this op's own"):
        op_sparsity_to_timeloop_densities(workload, op)


def test_fixed_structured_distribution_is_rejected():
    """Deliberately excluded — verified non-monotonic against this repo's own pinned Timeloop
    Docker image before this translator was trusted (see this module's own docstring)."""
    workload = flux_ir.load_document(SPARSE_GEMM_WORKLOAD)
    op = dict(workload["ops"][0])
    op["sparsity"] = {"I": {"distribution": "fixed_structured", "density": 0.25}}
    with pytest.raises(NotExpressibleError, match="not supported"):
        op_sparsity_to_timeloop_densities(workload, op)


@pytest.mark.parametrize("bad_density", [-0.1, 1.1, "0.5", None])
def test_out_of_range_or_non_numeric_density_is_rejected(bad_density):
    workload = flux_ir.load_document(SPARSE_GEMM_WORKLOAD)
    op = dict(workload["ops"][0])
    op["sparsity"] = {"I": {"distribution": "hypergeometric", "density": bad_density}}
    with pytest.raises(NotExpressibleError, match="must be a real number in \\[0, 1\\]"):
        op_sparsity_to_timeloop_densities(workload, op)
