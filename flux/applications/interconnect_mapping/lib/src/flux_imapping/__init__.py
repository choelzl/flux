"""flux_imapping -- conflict-aware banked-L1 interconnect study (docs/decisions.md D378).

B=32 single-ported banks of 128-bit rows against 28R+24W client ports across three
units; tensors in 12 storage modes with runtime dims; tile accesses whose write and
read tilings differ. Solutions = hash x placement x schedule x fabric, judged on a
four-cost Pareto (area, padding, latency, throughput) with proofs by exhaustion for
what is claimed conflict-free and pigeonhole floors for what cannot be. The study runs
from `applications/interconnect_mapping/interconnect_mapping.problem.yaml`;
`flux_imapping.world.World` is its world (review 2 step R3).
"""

from .conflict import BankHash, TrafficMetrics, intra_operand, run_traffic, shared_cycle
from .fabric import FabricModel, butterfly, catalog_fabrics, generate_fabrics, unit_split, xbar_full
from .flow import (
    Certificate, Scored, category_breakdown, certify, climb_xor, conclude, coordinate, fit_fabric,
    interconnect_loop, mapping_loop, pareto_front, pigeonhole_floor, score, z3_mapping_policy,
)
from .model import BLOCK_OF, Memory, Mode, TensorLayout, TileAccess, VECTOR_MODES
from .simulate import cross_check, simulate_traffic
from .solutions import CLIENT_PORTS, Solution, catalog, injective, swizzle_for
from .workloads import Workload, generate, train_holdout
from .world import STAGE, Study, World, app_scored

__all__ = [
    "BankHash", "TrafficMetrics", "intra_operand", "run_traffic", "shared_cycle",
    "Certificate", "Scored", "Study", "World", "app_scored", "certify", "climb_xor",
    "category_breakdown", "conclude", "coordinate", "fit_fabric", "generate_fabrics",
    "interconnect_loop", "mapping_loop", "z3_mapping_policy", "pareto_front", "pigeonhole_floor",
    "score", "STAGE", "BLOCK_OF", "Memory", "Mode", "TensorLayout", "TileAccess", "VECTOR_MODES",
    "CLIENT_PORTS", "FabricModel", "butterfly", "catalog_fabrics", "unit_split", "xbar_full",
    "cross_check", "simulate_traffic", "Solution", "catalog", "injective", "swizzle_for",
    "Workload", "generate", "train_holdout",
]
