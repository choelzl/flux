"""Flux Evaluator ABI v0.1 (docs/04.md §4): the narrow contract that makes ZigZag, Timeloop,
Sparseloop, RTL simulation, and synthesis interchangeable behind one interface.
"""

from __future__ import annotations

from .protocol import Evaluator
from .types import (
    ArchRef,
    Bottleneck,
    Budget,
    Candidate,
    Constraint,
    Domain,
    Escalation,
    Estimate,
    Limiter,
    MappingRef,
    Method,
    Metric,
    Provenance,
    Result,
    Roofline,
    Validity,
    WorkloadRef,
)

__all__ = [
    "Evaluator",
    "ArchRef",
    "Bottleneck",
    "Budget",
    "Candidate",
    "Constraint",
    "Domain",
    "Escalation",
    "Estimate",
    "Limiter",
    "MappingRef",
    "Method",
    "Metric",
    "Provenance",
    "Result",
    "Roofline",
    "Validity",
    "WorkloadRef",
]
