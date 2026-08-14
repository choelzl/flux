"""Instant structural screen for interconnect fabrics (docs/decisions.md D261)."""

from .adapter import InterconnectStructuralEvaluator, NotAnInterconnectError

__all__ = ["InterconnectStructuralEvaluator", "NotAnInterconnectError"]
