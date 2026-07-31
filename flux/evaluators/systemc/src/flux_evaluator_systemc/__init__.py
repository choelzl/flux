"""SystemC coarse-grain backend adapter (docs/04.md §4.4, §5's escalation rung): a fast
functional-correctness + timing pre-check, escalating to evaluators/rtl for cycle-accurate
numbers.
"""

from __future__ import annotations

from .adapter import SystemCEvaluator
from .errors import NotExpressibleError

__all__ = ["SystemCEvaluator", "NotExpressibleError"]
