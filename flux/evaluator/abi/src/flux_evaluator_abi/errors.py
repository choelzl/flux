"""The ABI's refusal (docs/evaluator-abi.md, docs/ir.md `not_expressible_in`).

Every backend refuses a candidate it cannot express by raising `NotExpressibleError` rather
than approximating. Each adapter's `errors.py` re-exports this one class, so an
`except NotExpressibleError` against the ABI catches every backend (D426).
"""

from __future__ import annotations


class NotExpressibleError(ValueError):
    """A workload, architecture or mapping this evaluator cannot express in its own
    representation. The message names the requirement it failed, so the caller can act on
    it (widen the search space, pick another stage, report the refusal as such)."""
