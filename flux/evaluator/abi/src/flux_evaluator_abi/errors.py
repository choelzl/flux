"""The ABI's refusal (docs/evaluator-abi.md, docs/ir.md `not_expressible_in`).

Every backend must refuse a candidate it cannot express by raising `NotExpressibleError`
rather than silently approximating. Sixteen adapters each declared their own copy of this
class, so a caller that wanted to catch "any backend's refusal" had to import sixteen names
(or catch `ValueError` and hope). One class here; each adapter's `errors.py` re-exports it,
so `flux_evaluator_rtl.NotExpressibleError is flux_evaluator_abi.NotExpressibleError` and an
`except NotExpressibleError` written against the ABI catches every backend (D426).
"""

from __future__ import annotations


class NotExpressibleError(ValueError):
    """A workload, architecture or mapping this evaluator cannot express in its own
    representation. The message names the requirement it failed, so the caller can act on
    it (widen the search space, pick another stage, report the refusal as such)."""


class NotACandidate(ValueError):
    """A candidate generator cannot express this base architecture or workload on its axis
    (D439): the same refusal posture as `NotExpressibleError`, one class so a caller can catch
    "no candidates on this axis" whichever generator said so. The generators' own classes
    (`NotAWidthSweepCandidate`, `NotANocTopologyCandidate`, ...) subclass it and keep their
    names."""
