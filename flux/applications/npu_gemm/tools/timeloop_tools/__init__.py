"""Flux's Timeloop+Accelergy backend adapter (docs/evaluator-abi.md): the second evaluator
implementing the Evaluator ABI, run via Docker.
"""

from __future__ import annotations

from .adapter import TimeloopEvaluator
from .architecture_translator import architecture_ir_to_timeloop_architecture_yaml
from .errors import NotExpressibleError
from .mapping_translator import (
    mapping_ir_to_timeloop_constraints,
    spatial_dim_for_timeloop_architecture,
)
from .workload_translator import (
    einsum_op_to_timeloop_instance,
    flux_dims_to_timeloop_dims,
    flux_tensor_to_timeloop_dataspace,
    op_sparsity_to_timeloop_densities,
)

__all__ = [
    "TimeloopEvaluator",
    "NotExpressibleError",
    "einsum_op_to_timeloop_instance",
    "flux_dims_to_timeloop_dims",
    "flux_tensor_to_timeloop_dataspace",
    "op_sparsity_to_timeloop_densities",
    "architecture_ir_to_timeloop_architecture_yaml",
    "mapping_ir_to_timeloop_constraints",
    "spatial_dim_for_timeloop_architecture",
]
