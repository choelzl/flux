"""Flux Evaluator ABI v0.1 (docs/evaluator-abi.md): the narrow contract that makes ZigZag, Timeloop,
RTL simulation, synthesis and every other backend interchangeable behind one interface -- the types, the
`Evaluator` protocol, the one refusal (`NotExpressibleError`), and the registry that resolves an
evaluator by name (D426).
"""

from __future__ import annotations

from .errors import NotACandidate, NotExpressibleError
from .protocol import Evaluator, SequentialBatch
from .registry import (
    available_evaluators,
    escalation_stage,
    evaluator_class,
    evaluator_name_for,
    make_evaluator,
    register_evaluator,
    translates,
)
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

from .toolchain import (  # noqa: F401
    MEASURING_TOOLS,
    differs_from_current,
    is_unattributed,
    tool_fingerprint,
    toolchain_fingerprint,
)
from .tools import (  # noqa: F401
    TAIL_CHARS, ToolRun, ToolSource, build_step, clone, ensure_binary, run_tool, tails,
)

__all__ = [
    "TAIL_CHARS",
    "ToolRun",
    "ToolSource",
    "build_step",
    "clone",
    "ensure_binary",
    "run_tool",
    "tails",
    "MEASURING_TOOLS",
    "differs_from_current",
    "is_unattributed",
    "tool_fingerprint",
    "toolchain_fingerprint",
    "Evaluator",
    "SequentialBatch",
    "NotACandidate",
    "NotExpressibleError",
    "available_evaluators",
    "escalation_stage",
    "evaluator_class",
    "translates",
    "evaluator_name_for",
    "make_evaluator",
    "register_evaluator",
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
