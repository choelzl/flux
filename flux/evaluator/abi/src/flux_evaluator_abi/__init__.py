"""Flux Evaluator ABI v0.1 (docs/evaluator-abi.md): the narrow contract that makes ZigZag, Timeloop,
RTL simulation, synthesis and every other backend interchangeable behind one interface -- the types, the
`Evaluator` protocol and the one refusal (`NotExpressibleError`). An application's own adapter
implements the protocol; no registry names one (D954).
"""

from __future__ import annotations

from .errors import NotExpressibleError
from .protocol import Evaluator, SequentialBatch
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
    "tool_fingerprint",
    "toolchain_fingerprint",
    "Evaluator",
    "SequentialBatch",
    "NotExpressibleError",
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
