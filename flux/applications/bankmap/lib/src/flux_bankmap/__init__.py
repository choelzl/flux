"""Conflict-free bank mapping: the space, the checker, the solvers (docs/decisions.md D356)."""

from .check import StrideVerdict, Verdict, check, check_stride
from .impossible import Impossibility, difference_set, find_impossibility, max_feasible_concurrency
from .mapping import Expr, InvalidExpression, Mapping, Modulo, XorFold, from_dict, modulo_baseline
from .problem import InvalidRequest, MappingRequest, MappingResult, Stage, crossbar_stages
from .topology import Topology, parse as parse_topology

__all__ = [
    "Expr", "Impossibility", "InvalidExpression", "InvalidRequest", "Mapping", "MappingRequest", "MappingResult",
    "Modulo", "Stage", "StrideVerdict", "Verdict", "XorFold", "crossbar_stages", "check", "check_stride", "difference_set", "find_impossibility", "from_dict", "max_feasible_concurrency",
    "modulo_baseline", "Topology", "parse_topology",
]
