"""Pure-logic tests for the Flux -> ZigZag workload translation (no ZigZag execution — see
tests/integration/test_zigzag_adapter_live.py for that). docs/04.md §4.4: adapters must fail
loudly on what they cannot express, never silently approximate.
"""

from __future__ import annotations

from pathlib import Path

import flux_ir
import pytest
from flux_evaluator_zigzag import (
    NotExpressibleError,
    einsum_op_to_zigzag_layer,
    workload_to_zigzag_layers,
)

FLUX_ROOT = Path(__file__).resolve().parents[2]
GEMM_WORKLOAD = FLUX_ROOT / "ir/workload/examples/mlp-gemm0.yaml"
LLM_WORKLOAD = FLUX_ROOT / "ir/workload/examples/llama3-8b-decode-layer0.yaml"
DMA_WORKLOAD = FLUX_ROOT / "ir/workload/examples/soc-dma-desc-fetch.yaml"


def test_gemm_example_translates_to_a_single_zigzag_layer():
    workload = flux_ir.load_document(GEMM_WORKLOAD)
    layers = workload_to_zigzag_layers(workload)

    assert len(layers) == 1
    layer = layers[0]
    assert layer["operator_type"] == "Gemm"
    assert layer["equation"] == "O[B][K]+=I[B][C]*W[C][K]"
    assert layer["loop_dims"] == ["B", "K", "C"]
    assert layer["loop_sizes"] == [4, 32, 32]
    assert layer["operand_precision"] == {"I": 8, "W": 8, "O": 16, "O_final": 8}


def test_op_without_precision_gets_a_default():
    op = {"id": "x", "kind": "einsum", "expr": "a b, b c -> a c", "bounds": {"a": 1, "b": 2, "c": 3}}
    layer = einsum_op_to_zigzag_layer(op, layer_id=0)
    assert layer["operand_precision"] == {"I": 8, "W": 8, "O": 16, "O_final": 8}


@pytest.mark.parametrize("kind", ["data_dependent", "compute_kernel"])
def test_non_einsum_op_is_rejected(kind):
    op = {"id": "x", "kind": kind, "semantics": {}}
    with pytest.raises(NotExpressibleError, match="only 'einsum' ops"):
        einsum_op_to_zigzag_layer(op, layer_id=0)


def test_op_with_dynamic_bound_is_rejected():
    """The llama3 example's `s`/`t` dims are {dyn: [...]} — exactly the case ZigZag cannot
    consume (docs/03.md G5). Must fail loudly, not silently pick a bound."""
    workload = flux_ir.load_document(LLM_WORKLOAD)
    op = next(o for o in workload["ops"] if o["id"] == "attn.qk")
    with pytest.raises(NotExpressibleError, match="non-static bound"):
        einsum_op_to_zigzag_layer(op, layer_id=0)


def test_workload_with_no_einsum_ops_is_rejected():
    """The DMA example is entirely compute_kernel/data_dependent — nothing for ZigZag."""
    workload = flux_ir.load_document(DMA_WORKLOAD)
    with pytest.raises(NotExpressibleError, match="no 'einsum' ops"):
        workload_to_zigzag_layers(workload)


def test_expr_with_more_than_two_inputs_is_rejected():
    op = {"id": "x", "kind": "einsum", "expr": "a b, b c, c d -> a d", "bounds": {}}
    with pytest.raises(NotExpressibleError, match="two-input einsum"):
        einsum_op_to_zigzag_layer(op, layer_id=0)


def test_missing_expr_is_rejected():
    op = {"id": "x", "kind": "einsum", "bounds": {}}
    with pytest.raises(NotExpressibleError, match="missing 'expr'"):
        einsum_op_to_zigzag_layer(op, layer_id=0)
