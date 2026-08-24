"""Unit tests for the Flux Workload IR -> ONNX exporter (docs/decisions.md D81) — the reverse
direction of `test_onnx_frontend.py`'s own ONNX -> Flux IR tests. See
tests/integration/test_stream_flux_export_live.py for the real, end-to-end proof this exported
ONNX actually runs through real Stream.
"""

from __future__ import annotations

from pathlib import Path

import flux_ir
import onnx
import pytest
from flux_frontend_onnx import NotExpressibleError, onnx_model_to_workload_ir, workload_ir_to_onnx_model

FLUX_ROOT = Path(__file__).resolve().parents[2]


def _gemm_workload(*, bounds=None) -> dict:
    bounds = bounds or {"B": 4, "C": 32, "K": 32}
    return {
        "schema_version": "0.1.0",
        "id": "test/single",
        "ops": [{"id": "mm0", "kind": "einsum", "expr": "B C, C K -> B K", "bounds": bounds}],
    }


def _chain_workload() -> dict:
    return {
        "schema_version": "0.1.0",
        "id": "test/chain",
        "ops": [
            {"id": "layer1", "kind": "einsum", "expr": "B C, C H -> B H", "bounds": {"B": 4, "C": 32, "H": 64}},
            {"id": "layer2", "kind": "einsum", "expr": "B H, H K -> B K", "bounds": {"B": 4, "H": 64, "K": 16}},
        ],
    }


def test_single_op_exports_to_one_gemm_node_and_passes_onnx_checker():
    model = workload_ir_to_onnx_model(_gemm_workload())
    onnx.checker.check_model(model)  # raises on failure — real ONNX validity, not assumed
    assert len(model.graph.node) == 1
    assert model.graph.node[0].op_type == "Gemm"
    assert model.graph.node[0].name == "mm0"
    assert model.graph.input[0].type.tensor_type.shape.dim[0].dim_value == 4
    assert model.graph.input[0].type.tensor_type.shape.dim[1].dim_value == 32
    assert model.graph.output[0].type.tensor_type.shape.dim[1].dim_value == 32


def test_chain_exports_to_two_chained_gemm_nodes():
    model = workload_ir_to_onnx_model(_chain_workload())
    onnx.checker.check_model(model)
    assert [n.name for n in model.graph.node] == ["layer1", "layer2"]
    # layer2's own first input must be layer1's own output — a real chained graph, not two
    # independent nodes both reading the graph input.
    assert model.graph.node[1].input[0] == model.graph.node[0].output[0]
    assert model.graph.output[0].type.tensor_type.shape.dim[1].dim_value == 16


def test_round_trips_through_the_forward_frontend_exactly():
    """The real, decisive correctness check: export then re-import via onnx_frontend.py's own
    forward direction, and confirm the bounds/shape survive exactly — not just "some ONNX came
    out," but the *same* GEMM shape.
    """
    original = _gemm_workload(bounds={"B": 4, "C": 32, "K": 32})
    model = workload_ir_to_onnx_model(original)
    reimported = onnx_model_to_workload_ir(model, workload_id="roundtrip")

    assert len(reimported["ops"]) == 1
    assert sorted(reimported["ops"][0]["bounds"].values()) == [4, 32, 32]


def test_round_trips_a_two_layer_chain():
    model = workload_ir_to_onnx_model(_chain_workload())
    reimported = onnx_model_to_workload_ir(model, workload_id="roundtrip-chain")

    assert len(reimported["ops"]) == 2
    assert sorted(reimported["ops"][0]["bounds"].values()) == [4, 32, 64]
    assert sorted(reimported["ops"][1]["bounds"].values()) == [4, 16, 64]


def test_the_real_mlp_gemm0_example_exports_correctly():
    workload = flux_ir.load_document(FLUX_ROOT / "core/ir/workload/examples/mlp-gemm0.yaml")
    model = workload_ir_to_onnx_model(workload)
    onnx.checker.check_model(model)
    reimported = onnx_model_to_workload_ir(model, workload_id="roundtrip")
    assert sorted(reimported["ops"][0]["bounds"].values()) == [4, 32, 32]


def test_non_einsum_op_is_rejected():
    workload = {"id": "x", "ops": [{"id": "op0", "kind": "data_dependent", "semantics": {}}]}
    with pytest.raises(NotExpressibleError, match="only translates 'einsum'"):
        workload_ir_to_onnx_model(workload)


def test_non_2d_gemm_expr_is_rejected():
    workload = {
        "id": "x",
        "ops": [{"id": "op0", "kind": "einsum", "expr": "a b c, c d -> a b d", "bounds": {}}],
    }
    with pytest.raises(NotExpressibleError, match="not a two-input 2D einsum"):
        workload_ir_to_onnx_model(workload)


def test_transposed_output_is_rejected():
    workload = {
        "id": "x",
        "ops": [{"id": "op0", "kind": "einsum", "expr": "b c, c k -> k b", "bounds": {"b": 1, "c": 2, "k": 3}}],
    }
    with pytest.raises(NotExpressibleError, match="no transposed output"):
        workload_ir_to_onnx_model(workload)


def test_broken_chain_reduction_size_mismatch_is_rejected():
    workload = {
        "id": "x",
        "ops": [
            {"id": "layer1", "kind": "einsum", "expr": "B C, C H -> B H", "bounds": {"B": 4, "C": 32, "H": 64}},
            # layer2's own reduction dim (H) should be 64 (layer1's output), but declares 99.
            {"id": "layer2", "kind": "einsum", "expr": "B H, H K -> B K", "bounds": {"B": 4, "H": 99, "K": 16}},
        ],
    }
    with pytest.raises(NotExpressibleError, match="doesn't match the previous op's own output size"):
        workload_ir_to_onnx_model(workload)


def test_dynamic_bound_is_rejected():
    workload = {
        "id": "x",
        "ops": [{
            "id": "op0", "kind": "einsum", "expr": "b c, c k -> b k",
            "bounds": {"b": 4, "c": {"dyn": [1, 128]}, "k": 32},
        }],
    }
    with pytest.raises(NotExpressibleError, match="non-static bound"):
        workload_ir_to_onnx_model(workload)


def test_empty_workload_is_rejected():
    with pytest.raises(NotExpressibleError, match="has no ops"):
        workload_ir_to_onnx_model({"id": "x", "ops": []})
