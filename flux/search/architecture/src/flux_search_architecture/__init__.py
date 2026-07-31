"""Architecture design-space exploration (docs/00-decisions.md D5)."""

from __future__ import annotations

from .candidates import ArchitectureCandidate, NotAWidthSweepCandidate, generate_width_candidates
from .dse import ArchitectureDSEReport, EscalationStep, SweepPoint, run_architecture_dse

__all__ = [
    "ArchitectureCandidate",
    "NotAWidthSweepCandidate",
    "generate_width_candidates",
    "SweepPoint",
    "EscalationStep",
    "ArchitectureDSEReport",
    "run_architecture_dse",
]
