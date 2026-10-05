"""Conflict-free bank mapping: the space, the checker, the solvers (D356).

The study runs from `applications/bankmap/problem.yaml`, its phases the commands of
`flux_bankmap.steps` (D799)."""

from .check import StrideVerdict, Verdict, check, check_verilog
from .impossible import Impossibility, find_impossibility, max_feasible_concurrency
from .mapping import (Expr, InvalidExpression, Mapping, Modulo, XorFold, from_dict, modulo_baseline,
                      module_verilog)
from .problem import InvalidRequest, MappingRequest, Stage
from .topology import Topology, crossbar_stages

__all__ = [
    "Expr", "Impossibility", "InvalidExpression", "InvalidRequest", "Mapping", "MappingRequest",
    "Modulo", "Stage", "StrideVerdict", "Verdict", "XorFold", "crossbar_stages", "check", "check_verilog",
    "find_impossibility", "from_dict", "max_feasible_concurrency", "modulo_baseline", "module_verilog",
    "Topology",
]
