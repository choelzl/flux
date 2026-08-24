"""Real Yosys + OpenROAD rung for interconnect fabrics (docs/decisions.md D261)."""

from .adapter import InterconnectPhysicalEvaluator, NotAnInterconnectError, NotExpressibleError

__all__ = ["InterconnectPhysicalEvaluator", "NotAnInterconnectError", "NotExpressibleError"]
