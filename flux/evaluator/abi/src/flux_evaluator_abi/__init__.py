"""Flux Evaluator ABI v0.1 (docs/evaluator-abi.md): the narrow contract that makes ZigZag, Timeloop,
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
    MetricMap,
    MetricOutcome,
    MissingMetricError,
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
    "MetricMap",
    "MetricOutcome",
    "MissingMetricError",
    "Provenance",
    "Result",
    "Roofline",
    "Validity",
    "WorkloadRef",
]
