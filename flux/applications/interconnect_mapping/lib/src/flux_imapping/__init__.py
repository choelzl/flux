"""flux_imapping -- conflict-aware banked-L1 interconnect study (D378).

B=32 single-ported banks of 128-bit rows against 28R+24W client ports across three
units; tensors in 12 storage modes with runtime dims; tile accesses whose write and
read tilings differ. Solutions = hash x placement x schedule x fabric, judged on a
four-cost Pareto (area, padding, latency, throughput) with proofs by exhaustion for
what is claimed conflict-free. The study runs from
`applications/interconnect_mapping/interconnect_mapping.problem.yaml`;
`flux_imapping.world.World` is its world.
"""

from .conflict import BankHash, intra_operand, shared_cycle
from .fabric import butterfly, generate_fabrics, xbar_full
from .flow import certify, fit_fabric, mapping_loop, pareto_front, score
from .model import Memory, Mode, TensorLayout, TileAccess
from .simulate import cross_check, simulate_traffic
from .solutions import catalog, injective, swizzle_for
from .workloads import Workload, generate, train_holdout
from .world import Ask, STAGE, Study, World, app_scored

__all__ = [
    "Ask", "BankHash", "Memory", "Mode", "STAGE", "Study", "TensorLayout", "TileAccess",
    "Workload", "World", "app_scored", "butterfly", "catalog", "certify", "cross_check",
    "fit_fabric", "generate", "generate_fabrics", "injective", "intra_operand", "mapping_loop",
    "pareto_front", "score", "shared_cycle", "simulate_traffic", "swizzle_for", "train_holdout",
    "xbar_full",
]
