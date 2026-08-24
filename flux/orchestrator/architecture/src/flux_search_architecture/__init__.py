"""Architecture design-space exploration (docs/decisions.md D5, D6)."""

from __future__ import annotations

from .candidates import ArchitectureCandidate, NotAWidthSweepCandidate, generate_width_candidates
from .fusion_candidates import (
    FusionTileCandidate,
    NotAFusionSweepCandidate,
    divisor_tile_sizes,
    generate_fusion_tile_candidates,
)
from .dse import ArchitectureDSEReport, EscalationStep, SweepPoint, contenders, run_architecture_dse
from .memory_candidates import (
    JointArchitectureCandidate,
    MemorySizeCandidate,
    NotAMemorySweepCandidate,
    generate_joint_candidates,
    generate_memory_size_candidates,
)
from .noc_candidates import NocTopologyCandidate, NotANocTopologyCandidate, generate_noc_topology_candidates

__all__ = [
    "ArchitectureCandidate",
    "FusionTileCandidate",
    "NotAFusionSweepCandidate",
    "divisor_tile_sizes",
    "generate_fusion_tile_candidates",
    "NotAWidthSweepCandidate",
    "generate_width_candidates",
    "NocTopologyCandidate",
    "NotANocTopologyCandidate",
    "generate_noc_topology_candidates",
    "MemorySizeCandidate",
    "JointArchitectureCandidate",
    "NotAMemorySweepCandidate",
    "generate_memory_size_candidates",
    "generate_joint_candidates",
    "SweepPoint",
    "EscalationStep",
    "ArchitectureDSEReport",
    "contenders",
    "run_architecture_dse",
]
