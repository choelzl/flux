"""Flux's ZigZag backend adapter (docs/evaluator-abi.md): the first evaluator implementing the
Evaluator ABI against a real, external cost model.
"""

from __future__ import annotations

from .adapter import ZigZagEvaluator, default_tpu_like_accelerator, default_tpu_like_mapping
from .architecture_translator import architecture_ir_to_zigzag_accelerator
from .errors import NotExpressibleError
from .mapping_regime import CAVEAT, caveat_for, fully_unrolls_reduction_dim, reduction_dims
from .mapping_translator import mapping_ir_to_zigzag_mapping
from .workload_translator import einsum_op_to_zigzag_layer, workload_to_zigzag_layers

__all__ = [
    "ZigZagEvaluator",
    "default_tpu_like_accelerator",
    "default_tpu_like_mapping",
    "NotExpressibleError",
    "CAVEAT",
    "caveat_for",
    "fully_unrolls_reduction_dim",
    "reduction_dims",
    "einsum_op_to_zigzag_layer",
    "workload_to_zigzag_layers",
    "architecture_ir_to_zigzag_accelerator",
    "mapping_ir_to_zigzag_mapping",
]
