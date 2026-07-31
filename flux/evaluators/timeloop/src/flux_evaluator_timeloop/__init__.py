"""Flux's Timeloop+Accelergy backend adapter (docs/04.md §4.4): the second evaluator
implementing the Evaluator ABI, run via Docker.
"""

from __future__ import annotations

from .adapter import TimeloopEvaluator
from .architecture_translator import architecture_ir_to_timeloop_architecture_yaml
from .errors import NotExpressibleError
from .mapping_translator import mapping_ir_to_timeloop_constraints
from .workload_translator import einsum_op_to_timeloop_instance, flux_dims_to_timeloop_dims

__all__ = [
    "TimeloopEvaluator",
    "NotExpressibleError",
    "einsum_op_to_timeloop_instance",
    "flux_dims_to_timeloop_dims",
    "architecture_ir_to_timeloop_architecture_yaml",
    "mapping_ir_to_timeloop_constraints",
]
