"""Instant structural screen for interconnect fabrics (docs/decisions.md D261)."""

from .adapter import InterconnectStructuralEvaluator, NotAnInterconnectError, NotExpressibleError

__all__ = ["InterconnectStructuralEvaluator", "NotAnInterconnectError", "NotExpressibleError"]
