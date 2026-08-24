"""Conflict-free bank mapping: the space, the checker, the solvers (docs/decisions.md D356).
The study runs from `applications/bankmap/bankmap.problem.yaml`; `flux_bankmap.world.World`
is its world (review 2 step R3)."""

from .check import StrideVerdict, Verdict, check, check_stride
from .impossible import Impossibility, difference_set, find_impossibility, max_feasible_concurrency
from .mapping import Expr, InvalidExpression, Mapping, Modulo, XorFold, from_dict, modulo_baseline
from .problem import InvalidRequest, MappingRequest, Stage, crossbar_stages
from .topology import Topology, parse as parse_topology

__all__ = [
    "Expr", "Impossibility", "InvalidExpression", "InvalidRequest", "Mapping", "MappingRequest",
    "Modulo", "Stage", "StrideVerdict", "Verdict", "XorFold", "crossbar_stages", "check", "check_stride", "difference_set", "find_impossibility", "from_dict", "max_feasible_concurrency",
    "modulo_baseline", "Topology", "parse_topology",
]
