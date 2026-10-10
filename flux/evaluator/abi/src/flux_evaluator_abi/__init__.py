"""Flux's measurement records (docs/evaluator-abi.md): a measurement as a `Result` with its
method, validity, provenance and escalation -- what every record row stores -- plus `run_tool`
and the toolchain fingerprints a measurement is keyed by. The backend adapters and their protocol
went with ZigZag, Timeloop and npu_gemm (D958)."""

from __future__ import annotations

from .types import (
    Bottleneck,
    Constraint,
    Domain,
    Escalation,
    Estimate,
    Limiter,
    Method,
    Metric,
    MetricMap,
    MetricOutcome,
    MissingMetricError,
    Provenance,
    Result,
    Roofline,
    Validity,
)

from .toolchain import (  # noqa: F401
    MEASURING_TOOLS,
    tool_fingerprint,
    toolchain_fingerprint,
)
from .tools import (  # noqa: F401
    TAIL_CHARS, ToolRun, run_tool, tails,
)

__all__ = [
    "TAIL_CHARS",
    "ToolRun",
    "run_tool",
    "tails",
    "MEASURING_TOOLS",
    "tool_fingerprint",
    "toolchain_fingerprint",
    "Bottleneck",
    "Constraint",
    "Domain",
    "Escalation",
    "Estimate",
    "Limiter",
    "Method",
    "Metric",
    "MetricMap",
    "MetricOutcome",
    "MissingMetricError",
    "Provenance",
    "Result",
    "Roofline",
    "Validity",
]
