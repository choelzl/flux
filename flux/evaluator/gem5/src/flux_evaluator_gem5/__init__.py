"""gem5 evaluator (docs/decisions.md D38): real cycle-accurate CPU simulation, called through
CHIA's own `chia.simulators.gem5.Gem5Node`.
"""

from __future__ import annotations

from .adapter import Gem5Evaluator
from .architecture_translator import architecture_ir_to_gem5_config_args
from .errors import NotExpressibleError

__all__ = [
    "Gem5Evaluator",
    "NotExpressibleError",
    "architecture_ir_to_gem5_config_args",
]
