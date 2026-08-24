"""Flux's ONNX frontend (docs/architecture.md L1): translates a pure MatMul/Gemm ONNX graph into a
Flux Workload IR document, and (docs/decisions.md D81) the reverse direction — a Flux Workload IR
document into a real ONNX model, for real external tools whose only workload input is ONNX
(Stream, docs/decisions.md D80).
"""

from __future__ import annotations

from .errors import NotExpressibleError
from .exporter import workload_ir_to_onnx_model
from .onnx_frontend import onnx_model_to_workload_ir

__all__ = ["onnx_model_to_workload_ir", "workload_ir_to_onnx_model", "NotExpressibleError"]
