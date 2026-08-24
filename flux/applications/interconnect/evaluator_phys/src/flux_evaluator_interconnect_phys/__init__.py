"""Real Yosys + OpenROAD stage for interconnect fabrics (docs/decisions.md D261)."""

from .adapter import InterconnectPhysicalEvaluator, NotExpressibleError

__all__ = ["InterconnectPhysicalEvaluator", "", "NotExpressibleError"]
