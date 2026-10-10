"""Flux's ZigZag backend adapter: the Evaluator ABI (docs/evaluator-abi.md) over ZigZag's cost model."""

from __future__ import annotations

from .adapter import ZigZagEvaluator, default_tpu_like_accelerator, default_tpu_like_mapping
from .architecture_translator import architecture_ir_to_zigzag_accelerator
from .errors import NotExpressibleError
from .workload_translator import einsum_op_to_zigzag_layer, workload_to_zigzag_layers

__all__ = [
    "ZigZagEvaluator",
    "default_tpu_like_accelerator",
    "default_tpu_like_mapping",
    "NotExpressibleError",
    "einsum_op_to_zigzag_layer",
    "workload_to_zigzag_layers",
    "architecture_ir_to_zigzag_accelerator",
]
