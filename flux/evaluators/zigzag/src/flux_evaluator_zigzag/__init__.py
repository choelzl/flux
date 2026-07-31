"""Flux's ZigZag backend adapter (docs/04.md §4.4): the first evaluator implementing the
Evaluator ABI against a real, external cost model.
"""

from __future__ import annotations

from .adapter import ZigZagEvaluator, default_tpu_like_accelerator, default_tpu_like_mapping
from .architecture_translator import architecture_ir_to_zigzag_accelerator
from .errors import NotExpressibleError
from .mapping_translator import mapping_ir_to_zigzag_mapping
from .workload_translator import einsum_op_to_zigzag_layer, workload_to_zigzag_layers

__all__ = [
    "ZigZagEvaluator",
    "default_tpu_like_accelerator",
    "default_tpu_like_mapping",
    "NotExpressibleError",
    "einsum_op_to_zigzag_layer",
    "workload_to_zigzag_layers",
    "architecture_ir_to_zigzag_accelerator",
    "mapping_ir_to_zigzag_mapping",
]
