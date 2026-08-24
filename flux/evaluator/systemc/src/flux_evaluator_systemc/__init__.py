"""SystemC coarse-grain backend adapter (docs/evaluator-abi.md, docs/calibration.md's escalation stage): a fast
functional-correctness + timing pre-check, escalating to evaluator/rtl for cycle-accurate
numbers.
"""

from __future__ import annotations

from .adapter import SystemCEvaluator
from .errors import NotExpressibleError

__all__ = ["SystemCEvaluator", "NotExpressibleError"]
