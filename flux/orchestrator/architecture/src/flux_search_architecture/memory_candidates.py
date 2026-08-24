"""Memory-hierarchy-size candidate generation for DSE (docs/decisions.md D26): sweeps one named
memory-class hierarchy level's `size_kb`, holding compute width, NoC config, and everything else
fixed. The third axis alongside `candidates.py`'s compute-array width and `noc_candidates.py`'s
NoC topology — same "generate a family of real Architecture IR documents, let a real evaluator
rank them" shape, over a third, independent IR path (`hierarchy[level].attrs.size_kb`). Feeds the
same `dse.py` engine the other two do — see that module's docstring for why the engine itself
doesn't need to know which axis is varying.

Unlike compute width (D13: strictly monotonic, wider always wins) and NoC topology (D16: non-
monotonic, an LLM has something real to find), the memory-size axis has a *third* real shape,
found empirically before this module was written, not assumed: below some workload-dependent
floor, ZigZag's own mapper rejects the candidate outright ("layer does not fit within the full
memory hierarchy" — the layer's live working set genuinely doesn't fit); at and above that floor,
`latency_cycles` is flat (buffer capacity isn't the bottleneck once the working set fits) but
`energy_pj` increases *monotonically with size* — a bigger SRAM costs more energy per access in
ZigZag's own cost model, even when the extra capacity goes unused. The real minimum-energy point
is therefore the *smallest feasible* size, not the largest — genuinely counter-intuitive if you
assumed "more cache is free," and a different landscape shape than either of this package's other
two axes (see docs/decisions.md D26 for the full empirical walk-through, including that this
axis's feasibility floor was checked and found *not* to shift with compute width for
`mlp-gemm0.yaml`/`simple-npu-1d-v1.yaml` — the two axes are separable for this workload, not
assumed to be).

`generate_joint_candidates` composes this axis with `candidates.py`'s width axis into a real
Cartesian product — genuine multi-parameter architecture DSE, not two single-axis sweeps run
separately — closing `search/architecture/README.md`'s previously-documented "Not implemented:
multi-parameter sweeps" gap.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any


class NotAMemorySweepCandidate(Exception):
    """Raised when `base_arch` has no hierarchy entry named `level` with class `memory` to vary
    — same "fail loudly, no silent default" posture as `candidates.NotAWidthSweepCandidate` and
    `noc_candidates.NotANocTopologyCandidate`.
    """


@dataclass(frozen=True, slots=True)
class MemorySizeCandidate:
    arch: dict[str, Any]
    level: str
    size_kb: float

    def to_dict(self) -> dict[str, Any]:
        return {"arch": self.arch, "level": self.level, "size_kb": self.size_kb}


@dataclass(frozen=True, slots=True)
class JointArchitectureCandidate:
    """A single point in the (compute width) x (memory size) joint space — the same `.arch`/
    `.to_dict()` shape `dse.py`'s `_ArchCandidateProtocol` needs, carrying both varied axes so a
    caller can tell which point in the 2D grid a given result belongs to.
    """

    arch: dict[str, Any]
    array_dim: str
    width: int
    level: str
    size_kb: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "arch": self.arch, "array_dim": self.array_dim, "width": self.width,
            "level": self.level, "size_kb": self.size_kb,
        }


def _find_memory_level(base_arch: dict[str, Any], level: str) -> None:
    hierarchy = base_arch.get("hierarchy", [])
    matches = [n for n in hierarchy if n.get("level") == level]
    if not matches:
        raise NotAMemorySweepCandidate(
            f"architecture {base_arch.get('id', '<no id>')!r} has no hierarchy entry named "
            f"{level!r} to vary."
        )
    if len(matches) > 1:
        raise NotAMemorySweepCandidate(
            f"architecture {base_arch.get('id', '<no id>')!r} has {len(matches)} hierarchy "
            f"entries named {level!r} — expected exactly one."
        )
    if matches[0].get("class") != "memory":
        raise NotAMemorySweepCandidate(
            f"architecture {base_arch.get('id', '<no id>')!r}'s hierarchy entry {level!r} has "
            f"class {matches[0].get('class')!r}, not 'memory'."
        )


def generate_memory_size_candidates(
    base_arch: dict[str, Any], level: str, sizes_kb: list[float]
) -> list[MemorySizeCandidate]:
    """One `MemorySizeCandidate` per size in `sizes_kb`: a deep copy of `base_arch` with the
    named memory-class hierarchy entry's `attrs.size_kb` set to that value. Everything else
    (compute width, NoC config, other memory levels) is copied unchanged.

    `level` (e.g. `"gbuf"`) must name exactly one `class: memory` hierarchy entry — explicit,
    not auto-selected, matching `noc_candidates.generate_noc_topology_candidates`'s preference
    for an explicit request over an inferred one. A size too small for the workload's working set
    to fit is a real, expected outcome, not a caller error — the evaluator raises for that specific
    candidate at screening time (see module docstring), same as any other infeasible candidate
    `dse.run_architecture_dse` already handles per-candidate.
    """
    _find_memory_level(base_arch, level)

    candidates: list[MemorySizeCandidate] = []
    for size_kb in sizes_kb:
        arch_copy = copy.deepcopy(base_arch)
        memory_node = next(n for n in arch_copy["hierarchy"] if n.get("level") == level)
        memory_node["attrs"]["size_kb"] = size_kb
        size_label = str(size_kb).replace(".", "p")
        arch_copy["id"] = f"{base_arch.get('id', 'arch')}-{level}{size_label}kb"
        candidates.append(MemorySizeCandidate(arch=arch_copy, level=level, size_kb=size_kb))
    return candidates


def generate_joint_candidates(
    base_arch: dict[str, Any], widths: list[int], level: str, sizes_kb: list[float]
) -> list[JointArchitectureCandidate]:
    """The full Cartesian product of `candidates.generate_width_candidates`'s width axis and this
    module's memory-size axis: `len(widths) * len(sizes_kb)` candidates, one per (width, size_kb)
    pair. Real joint architecture DSE — the two knobs varied together, not swept independently and
    combined after the fact — feeding the same axis-agnostic `dse.run_architecture_dse` engine
    every other candidate-generator module in this package does.

    Requires `base_arch` to satisfy both `generate_width_candidates`'s single-spatial-dim scope
    and this module's named-memory-level scope; raises whichever of `NotAWidthSweepCandidate` /
    `NotAMemorySweepCandidate` applies, same fail-loudly posture as the single-axis generators.
    """
    # Reuse the width generator's own validation/dim-finding instead of duplicating it.
    from .candidates import generate_width_candidates

    _find_memory_level(base_arch, level)
    width_candidates = generate_width_candidates(base_arch, widths)

    joint: list[JointArchitectureCandidate] = []
    for width_candidate in width_candidates:
        for size_kb in sizes_kb:
            arch_copy = copy.deepcopy(width_candidate.arch)
            memory_node = next(n for n in arch_copy["hierarchy"] if n.get("level") == level)
            memory_node["attrs"]["size_kb"] = size_kb
            size_label = str(size_kb).replace(".", "p")
            arch_copy["id"] = f"{base_arch.get('id', 'arch')}-width{width_candidate.width}-{level}{size_label}kb"
            joint.append(
                JointArchitectureCandidate(
                    arch=arch_copy, array_dim=width_candidate.array_dim,
                    width=width_candidate.width, level=level, size_kb=size_kb,
                )
            )
    return joint
