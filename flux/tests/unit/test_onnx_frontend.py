"""Unit tests for the ONNX frontend (docs/architecture.md L1). Builds small ONNX graphs in-process via
onnx.helper — no external .onnx files needed. See tests/integration/test_onnx_frontend_live.py
for the real-model round trip (synthetic MLP through ZigZag) and the real ResNet18 rejection.
"""

from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # annotation-only: the name is a forward reference and pyflakes
    import onnx   # reports it undefined without this (D334)


import numpy as np
import pytest
from onnx import TensorProto, helper
from flux_frontend_onnx import NotExpressibleError, onnx_model_to_workload_ir


def _tensor(name: str, shape: tuple[int, ...]) -> "onnx.TensorProto":  # noqa: F821
    rng = np.random.default_rng(0)
    return helper.make_tensor(
        name, TensorProto.FLOAT, list(shape), rng.standard_normal(shape).astype("float32").flatten()
    )


def _mlp_model(nodes, input_shape, output_shape, initializers):
    x = helper.make_tensor_value_info("X", TensorProto.FLOAT, list(input_shape))
    y = helper.make_tensor_value_info("Y", TensorProto.FLOAT, list(output_shape))
    graph = helper.make_graph(nodes, "test-graph", [x], [y], initializer=initializers)
    return helper.make_model(graph, producer_name="flux-test")


def test_single_matmul_translates_to_one_einsum_op():
    w = _tensor("W", (32, 16))
    node = helper.make_node("MatMul", ["X", "W"], ["Y"], name="mm0")
    model = _mlp_model([node], (4, 32), (4, 16), [w])

    doc = onnx_model_to_workload_ir(model, workload_id="test/single")

    assert doc["id"] == "test/single"
    assert doc["provenance"]["source"] == "onnx"
    assert len(doc["ops"]) == 1
    op = doc["ops"][0]
    assert op["id"] == "mm0"
    assert op["kind"] == "einsum"
    dims = op["bounds"]
    assert sorted(dims.values()) == [4, 16, 32]


def test_two_layer_chain_shares_the_reduction_dim_across_ops():
    w1 = _tensor("W1", (32, 64))
    w2 = _tensor("W2", (64, 16))
    node1 = helper.make_node("Gemm", ["X", "W1"], ["H"], name="layer1")
    node2 = helper.make_node("MatMul", ["H", "W2"], ["Y"], name="layer2")
    model = _mlp_model([node1, node2], (4, 32), (4, 16), [w1, w2])

    doc = onnx_model_to_workload_ir(model, workload_id="test/chain")

    assert [op["id"] for op in doc["ops"]] == ["layer1", "layer2"]
    # layer1's output dim name must be layer2's reduction dim name (chained).
    layer1_out_dim = doc["ops"][0]["expr"].split("->")[1].split()[1]
    layer2_reduce_dim = doc["ops"][1]["expr"].split(",")[1].split()[0]
    assert layer1_out_dim == layer2_reduce_dim
    assert doc["ops"][0]["bounds"][layer1_out_dim] == 64
    assert doc["ops"][1]["bounds"][layer2_reduce_dim] == 64


def test_conv_node_is_rejected():
    w = _tensor("W", (8, 3, 3, 3))
    node = helper.make_node("Conv", ["X", "W"], ["Y"], name="conv0")
    x = helper.make_tensor_value_info("X", TensorProto.FLOAT, [1, 3, 8, 8])
    y = helper.make_tensor_value_info("Y", TensorProto.FLOAT, [1, 8, 6, 6])
    graph = helper.make_graph([node], "conv-graph", [x], [y], initializer=[w])
    model = helper.make_model(graph, producer_name="flux-test")

    with pytest.raises(NotExpressibleError, match="4 dims, not 2"):
        onnx_model_to_workload_ir(model, workload_id="test/conv")


def test_transposed_gemm_is_rejected():
    w = _tensor("W", (32, 16))
    node = helper.make_node("Gemm", ["X", "W"], ["Y"], name="gemm0", transB=1)
    model = _mlp_model([node], (4, 32), (4, 16), [w])

    with pytest.raises(NotExpressibleError, match="transB=1"):
        onnx_model_to_workload_ir(model, workload_id="test/transposed")


def test_non_initializer_weight_is_rejected():
    node = helper.make_node("MatMul", ["X", "NotAWeight"], ["Y"], name="mm0")
    x = helper.make_tensor_value_info("X", TensorProto.FLOAT, [4, 32])
    not_a_weight = helper.make_tensor_value_info("NotAWeight", TensorProto.FLOAT, [32, 16])
    y = helper.make_tensor_value_info("Y", TensorProto.FLOAT, [4, 16])
    graph = helper.make_graph([node], "g", [x], [y])
    model = helper.make_model(graph, producer_name="flux-test")

    with pytest.raises(NotExpressibleError, match="not a constant initializer"):
        onnx_model_to_workload_ir(model, workload_id="test/dynamic-weight")


def test_mismatched_shapes_are_rejected():
    w = _tensor("W", (99, 16))  # doesn't match X's feature dim (32)
    node = helper.make_node("MatMul", ["X", "W"], ["Y"], name="mm0")
    model = _mlp_model([node], (4, 32), (4, 16), [w])

    with pytest.raises(NotExpressibleError, match="expects input size 99"):
        onnx_model_to_workload_ir(model, workload_id="test/mismatched")


def test_symbolic_batch_dim_is_rejected():
    w = _tensor("W", (32, 16))
    node = helper.make_node("MatMul", ["X", "W"], ["Y"], name="mm0")
    x = helper.make_tensor_value_info("X", TensorProto.FLOAT, ["batch", 32])
    y = helper.make_tensor_value_info("Y", TensorProto.FLOAT, ["batch", 16])
    graph = helper.make_graph([node], "g", [x], [y], initializer=[w])
    model = helper.make_model(graph, producer_name="flux-test")

    with pytest.raises(NotExpressibleError, match="not a static positive size"):
        onnx_model_to_workload_ir(model, workload_id="test/symbolic")


def test_non_chained_dag_is_rejected():
    """Two independent MatMuls both reading X (a branch, not a chain) — this frontend only
    handles linear chains."""
    w1 = _tensor("W1", (32, 16))
    w2 = _tensor("W2", (32, 8))
    node1 = helper.make_node("MatMul", ["X", "W1"], ["Y1"], name="mm1")
    node2 = helper.make_node("MatMul", ["X", "W2"], ["Y2"], name="mm2")
    x = helper.make_tensor_value_info("X", TensorProto.FLOAT, [4, 32])
    y2 = helper.make_tensor_value_info("Y2", TensorProto.FLOAT, [4, 8])
    graph = helper.make_graph([node1, node2], "g", [x], [y2], initializer=[w1, w2])
    model = helper.make_model(graph, producer_name="flux-test")

    with pytest.raises(NotExpressibleError, match="strictly chained"):
        onnx_model_to_workload_ir(model, workload_id="test/branch")


def test_generated_workload_validates_against_the_schema():
    import flux_ir

    w = _tensor("W", (32, 16))
    node = helper.make_node("MatMul", ["X", "W"], ["Y"], name="mm0")
    model = _mlp_model([node], (4, 32), (4, 16), [w])

    doc = onnx_model_to_workload_ir(model, workload_id="test/schema-check")
    flux_ir.validate("workload", doc)  # raises on failure


def test_initializers_listed_as_graph_inputs_are_not_counted_as_inputs():
    """ONNX IR version < 4 *requires* every initializer to also appear in `graph.input`, so models
    from older tooling legitimately carry `graph.input = [activation, W0, ...]`. Counting raw
    `graph.input` rejected those as multi-input MLPs — a false negative naming the wrong reason on
    a graph this frontend fully supports.
    """
    w = _tensor("W", (32, 16))
    node = helper.make_node("MatMul", ["X", "W"], ["Y"], name="mm0")
    model = _mlp_model([node], (4, 32), (4, 16), [w])
    model.graph.input.append(helper.make_tensor_value_info("W", TensorProto.FLOAT, [32, 16]))

    doc = onnx_model_to_workload_ir(model, workload_id="test/ir3")

    assert len(doc["ops"]) == 1
    assert doc["ops"][0]["id"] == "mm0"


def test_two_real_inputs_are_still_rejected_by_name():
    w = _tensor("W", (32, 16))
    node = helper.make_node("MatMul", ["X", "W"], ["Y"], name="mm0")
    model = _mlp_model([node], (4, 32), (4, 16), [w])
    model.graph.input.append(helper.make_tensor_value_info("X2", TensorProto.FLOAT, [4, 32]))

    with pytest.raises(NotExpressibleError) as exc:
        onnx_model_to_workload_ir(model, workload_id="test/two-inputs")
    assert "X2" in str(exc.value)


def test_dropped_gemm_bias_is_recorded_in_provenance():
    """The IR's einsum op cannot express a bias add, and rejecting biased Gemms would reject
    almost every real MLP export — so the bias is dropped, but never silently.
    """
    w = _tensor("W", (32, 16))
    b = _tensor("B", (16,))
    node = helper.make_node("Gemm", ["X", "W", "B"], ["Y"], name="gemm0")
    model = _mlp_model([node], (4, 32), (4, 16), [w, b])

    doc = onnx_model_to_workload_ir(model, workload_id="test/bias")

    assert len(doc["ops"]) == 1
    assert any("bias" in note and "gemm0" in note for note in doc["provenance"]["dropped"])


def test_unbiased_gemm_records_nothing_dropped():
    w = _tensor("W", (32, 16))
    node = helper.make_node("Gemm", ["X", "W"], ["Y"], name="gemm0")
    model = _mlp_model([node], (4, 32), (4, 16), [w])

    doc = onnx_model_to_workload_ir(model, workload_id="test/no-bias")

    assert "dropped" not in doc["provenance"]
