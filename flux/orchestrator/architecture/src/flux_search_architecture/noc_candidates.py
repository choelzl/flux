"""NoC topology candidate generation for DSE (docs/decisions.md D6): sweeps a k-ary n-cube
network's topology/dimensionality, holding everything else in the architecture fixed. The NoC
counterpart to `candidates.py`'s compute-array width sweep — same "generate a family of real
Architecture IR documents, let a real evaluator rank them" shape, over a different IR path
(`interconnect.noc`, not `hierarchy[].attrs.dims`) and a different axis (topology/dimensionality,
not compute width). Feeds the same `dse.py` engine `candidates.py` does — see that module's
docstring for why the engine itself doesn't need to know which axis is varying.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any


class NotANocTopologyCandidate(Exception):
    """Raised when `base_arch` has no `interconnect.noc` block to vary — same "fail loudly, no
    silent default" posture as `candidates.NotAWidthSweepCandidate`.
    """


@dataclass(frozen=True, slots=True)
class NocTopologyCandidate:
    arch: dict[str, Any]
    topology: str
    dimensions: tuple[int, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"arch": self.arch, "topology": self.topology, "dimensions": list(self.dimensions)}


def generate_noc_topology_candidates(
    base_arch: dict[str, Any], variants: list[tuple[str, list[int]]]
) -> list[NocTopologyCandidate]:
    """One `NocTopologyCandidate` per `(topology, dimensions)` pair in `variants` — a deep copy
    of `base_arch` with its `interconnect.noc.topology`/`dimensions` replaced. Everything else
    (routing function, VC config, traffic pattern) is copied unchanged.

    `variants` is explicit, not auto-derived from a target node count — matching this repo's
    v0.1 preference for a small, explicit search space over a cleverly-inferred one (the same
    call `search/exhaustive`'s own README makes). Comparing a 2D 8x8 mesh against a 3D 4x4x4 mesh
    (both 64 nodes) means passing `[("mesh", [8, 8]), ("mesh", [4, 4, 4])]` explicitly, not asking
    this function to solve for equal-node-count variants itself.
    """
    if "noc" not in base_arch.get("interconnect", {}):
        raise NotANocTopologyCandidate(
            f"architecture {base_arch.get('id', '<no id>')!r} has no interconnect.noc block to "
            "vary (see ir/architecture/examples/noc-mesh-2d-v1.yaml for the expected shape)."
        )

    candidates: list[NocTopologyCandidate] = []
    for topology, dimensions in variants:
        arch_copy = copy.deepcopy(base_arch)
        arch_copy["interconnect"]["noc"]["topology"] = topology
        arch_copy["interconnect"]["noc"]["dimensions"] = list(dimensions)
        dims_label = "x".join(str(d) for d in dimensions)
        arch_copy["id"] = f"{base_arch.get('id', 'arch')}-{topology}-{dims_label}"
        candidates.append(
            NocTopologyCandidate(arch=arch_copy, topology=topology, dimensions=tuple(dimensions))
        )
    return candidates
