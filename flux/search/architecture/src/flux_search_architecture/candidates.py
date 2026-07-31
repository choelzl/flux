"""Architecture-parameter candidate generation for DSE (docs/00-decisions.md D5: "really focus
architecture exploration"). Varies a single-spatial-dim architecture's array width, holding
memory sizes and everything else fixed — the one spatial degree of freedom every evaluator in
this repo already treats uniformly (ZigZag, Timeloop, RTL, SystemC all model exactly one compute
array dimension in v0.1).

This is deliberately about the *architecture*, not the mapping: search/exhaustive/ and
search/annealing/ hold (workload, architecture) fixed and search over Mapping IR; this holds
(workload, mapping=None) fixed and searches over Architecture IR instead. Different axis, same
"generate a family of real IR documents, let a real evaluator rank them" shape.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any


class NotAWidthSweepCandidate(Exception):
    """Raised when `base_arch` falls outside this generator's single-spatial-dim scope — same
    scope every evaluator adapter here already shares, not a new limitation.
    """


@dataclass(frozen=True, slots=True)
class ArchitectureCandidate:
    arch: dict[str, Any]
    array_dim: str
    width: int


def generate_width_candidates(
    base_arch: dict[str, Any], widths: list[int]
) -> list[ArchitectureCandidate]:
    """One `ArchitectureCandidate` per width in `widths`: a deep copy of `base_arch` with its
    single compute node's spatial dim size set to that width. Everything else (memory hierarchy,
    tech node, constraints) is copied unchanged.
    """
    hierarchy = base_arch.get("hierarchy", [])
    compute_nodes = [n for n in hierarchy if n.get("class") == "compute"]
    if len(compute_nodes) != 1:
        raise NotAWidthSweepCandidate(
            f"expected exactly one compute hierarchy node, found {len(compute_nodes)}"
        )
    dims = compute_nodes[0].get("attrs", {}).get("dims", {})
    if len(dims) != 1:
        raise NotAWidthSweepCandidate(
            f"expected exactly one spatial array dimension, found {len(dims)} (matches every "
            "evaluator adapter's own single-spatial-dim v0.1 limit)"
        )
    (array_dim, _original_width), = dims.items()

    candidates: list[ArchitectureCandidate] = []
    for width in widths:
        arch_copy = copy.deepcopy(base_arch)
        compute_node = next(n for n in arch_copy["hierarchy"] if n.get("class") == "compute")
        compute_node["attrs"]["dims"][array_dim] = width
        arch_copy["id"] = f"{base_arch.get('id', 'arch')}-width{width}"
        candidates.append(ArchitectureCandidate(arch=arch_copy, array_dim=array_dim, width=width))
    return candidates
