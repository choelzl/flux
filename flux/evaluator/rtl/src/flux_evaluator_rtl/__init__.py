"""Flux's RTL-sim backend adapter (docs/evaluator-abi.md, docs/calibration.md's escalation rung): a real Verilator
simulation of a hand-written mac_array.sv, implementing the Evaluator ABI.
"""

from __future__ import annotations

from .adapter import RTLEvaluator, generate_test_vectors
from .architecture_translator import architecture_ir_to_lanes
from .errors import NotExpressibleError
from .workload_translator import einsum_op_to_mac_array_shape

__all__ = [
    "RTLEvaluator",
    "NotExpressibleError",
    "einsum_op_to_mac_array_shape",
    "architecture_ir_to_lanes",
    "generate_test_vectors",
]
