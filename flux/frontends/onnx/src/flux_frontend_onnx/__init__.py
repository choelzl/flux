"""Flux's ONNX frontend (docs/04.md §2 L1): translates a pure MatMul/Gemm ONNX graph into a
Flux Workload IR document.
"""

from __future__ import annotations

from .errors import NotExpressibleError
from .onnx_frontend import onnx_model_to_workload_ir

__all__ = ["onnx_model_to_workload_ir", "NotExpressibleError"]
