"""Real redaction layer between evaluator outputs and model context (docs/decisions.md D93,
docs/gap-analysis.md G15). See `core.py`'s own module docstring for the two real mechanisms;
`asap7.py` for the concrete, wired application against this repo's own real PDK-derived data.
"""

from .asap7 import RedactedAsap7Result, redact_asap7_result, redact_asap7_ranking
from .core import NoBaselineError, RankedCandidate, RelativeDelta, redact_ranking, redact_relative
from .policy import (
    ConfidentialPdkError,
    PdkConfidentiality,
    UnknownPdkError,
    is_confidential,
    register_pdk,
    require_not_confidential,
)

__all__ = [
    "NoBaselineError",
    "RelativeDelta",
    "redact_relative",
    "RankedCandidate",
    "redact_ranking",
    "RedactedAsap7Result",
    "redact_asap7_result",
    "redact_asap7_ranking",
    "ConfidentialPdkError",
    "UnknownPdkError",
    "PdkConfidentiality",
    "register_pdk",
    "is_confidential",
    "require_not_confidential",
]
