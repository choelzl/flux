"""Integration tests for the ONNX frontend: a synthetic MLP through real ZigZag, and a real
bundled CNN (ResNet18, from zigzag-dse's own example inputs) correctly rejected rather than
silently mishandled.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import onnx
import pytest
import zigzag
from onnx import TensorProto, helper
from flux_evaluator_abi import Budget, Candidate
from flux_evaluator_zigzag import ZigZagEvaluator
import flux_ir
from flux_frontend_onnx import NotExpressibleError, onnx_model_to_workload_ir

logging.getLogger("zigzag").setLevel(logging.WARNING)

FLUX_ROOT = Path(__file__).resolve().parents[2]
SIMPLE_NPU_1D = FLUX_ROOT / "core/ir/architecture/examples/simple-npu-1d-v1.yaml"
RESNET18_ONNX = (
    Path(zigzag.__file__).resolve().parent / "inputs" / "workload" / "resnet18.onnx"
)


def _synthetic_two_layer_mlp() -> onnx.ModelProto:
    rng = np.random.default_rng(0)
    w1 = helper.make_tensor(
        "W1", TensorProto.FLOAT, [32, 64], rng.standard_normal((32, 64)).astype("float32").flatten()
    )
    w2 = helper.make_tensor(
        "W2", TensorProto.FLOAT, [64, 16], rng.standard_normal((64, 16)).astype("float32").flatten()
    )
    x = helper.make_tensor_value_info("X", TensorProto.FLOAT, [4, 32])
    y = helper.make_tensor_value_info("Y", TensorProto.FLOAT, [4, 16])
    node1 = helper.make_node("Gemm", ["X", "W1"], ["H"], name="layer1")
    node2 = helper.make_node("MatMul", ["H", "W2"], ["Y"], name="layer2")
    graph = helper.make_graph([node1, node2], "mlp2", [x], [y], initializer=[w1, w2])
    return helper.make_model(graph, producer_name="flux-test")


def test_synthetic_mlp_evaluates_through_real_zigzag():
    model = _synthetic_two_layer_mlp()
    workload = onnx_model_to_workload_ir(model, workload_id="test/mlp2")
    flux_ir.validate("workload", workload)
    arch = flux_ir.load_document(SIMPLE_NPU_1D)

    candidate = Candidate(workload=workload, arch=arch, mapping=None)
    result = ZigZagEvaluator().evaluate(candidate, Budget(), frozenset({"latency_cycles", "energy_pj"}))

    assert result.metrics["latency_cycles"].value > 0
    assert result.metrics["energy_pj"].value > 0
    assert result.provenance.inputs["workload_hash"] == flux_ir.content_hash(workload)


@pytest.mark.skipif(not RESNET18_ONNX.exists(), reason="zigzag-dse's bundled resnet18.onnx not found")
def test_real_resnet18_is_rejected_not_silently_mishandled():
    model = onnx.load(str(RESNET18_ONNX), load_external_data=False)

    with pytest.raises(NotExpressibleError):
        onnx_model_to_workload_ir(model, workload_id="resnet18")
