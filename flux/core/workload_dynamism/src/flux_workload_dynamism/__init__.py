"""Real, honest cost estimation for a Workload IR op with a declared dynamic bound
(docs/gap-analysis.md G5, docs/decisions.md D63) or a real `data_dependent` MoE routing decision
(docs/decisions.md D68). See `sweep.sweep_dynamic_shape`/`moe_routing.sweep_moe_routing`'s own
docstrings for the real entry points.

`distributions.py` (docs/decisions.md D87) resolves a `dynamism.distributions` reference to real,
ingested empirical data and derives real quantile-based sample points from it — see its own
module docstring.
"""

from .distributions import (
    DistributionResolutionError,
    EmpiricalDistribution,
    load_empirical_distribution,
    parse_distribution_ref,
    quantile_sample_points,
)
from .moe_routing import MoeRoutingError, RoutingSample, resolve_moe_routing, sweep_moe_routing
from .sweep import DynamicShapeError, SamplePoint, dynamic_bound_range, resolve_dynamic_bound, sweep_dynamic_shape

__all__ = [
    "DynamicShapeError",
    "SamplePoint",
    "dynamic_bound_range",
    "resolve_dynamic_bound",
    "sweep_dynamic_shape",
    "MoeRoutingError",
    "RoutingSample",
    "resolve_moe_routing",
    "sweep_moe_routing",
    "DistributionResolutionError",
    "EmpiricalDistribution",
    "load_empirical_distribution",
    "parse_distribution_ref",
    "quantile_sample_points",
]
