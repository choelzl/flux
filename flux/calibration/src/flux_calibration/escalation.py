"""Escalation policy (docs/04.md §5): "spend high-fidelity budget where it changes the answer" —
points on or near the Pareto front, points where the analytic CI is wide, points out of
validated domain.

v0.1 implements two of those three triggers — CI width and domain — not Pareto-front relevance.
A single `Result` has no notion of "the front" without seeing its neighbours in a candidate set;
that's a property of a search run (L5), not something one Calibration (L3) call over one result
can determine.

`next_rung` names a real, invokable next step: `evaluators/rtl/`'s `RTLEvaluator` (registered as
`"rtl"` in `flows/cli/registry.py`) is a real Verilator-simulation adapter now — see its README
and docs/calibration-report.md for the first calibration records built against its real
measurement rather than another analytic model's estimate. It only covers the narrow shape that
adapter's hand-written `mac_array.sv` can express (docs/00-decisions.md D2's build-vs-reuse
scope), so escalating an arbitrary candidate can still fail with `NotExpressibleError` — that's
the caller's problem to handle (same as calling any evaluator on an out-of-scope candidate), not
this policy's; naming the rung is a recommendation, not a guarantee it will succeed.
"""

from __future__ import annotations

import dataclasses

from flux_evaluator_abi import Escalation, Estimate, Result

_DEFAULT_MAX_RELATIVE_CI_WIDTH = 0.5  # (ci_high - ci_low) / value > this triggers escalation
_NEXT_RUNG = "rtl"


def _relative_ci_width(estimate: Estimate) -> float:
    if estimate.value == 0:
        return 0.0
    return (estimate.ci_high - estimate.ci_low) / abs(estimate.value)


def apply_escalation_policy(
    result: Result, *, max_relative_ci_width: float = _DEFAULT_MAX_RELATIVE_CI_WIDTH
) -> Result:
    """Recompute `result.escalation` from its (already calibrated) metrics and domain.

    Call this *after* `calibrate_result()` — it trusts the confidence intervals and domain it's
    given, it doesn't compute them. Two independent triggers, either sufficient on its own:
    out-of-domain (per `result.domain.in_domain`), or a confidence interval wider than
    `max_relative_ci_width` of the point value for any metric.
    """
    reasons: list[str] = []
    if not result.domain.in_domain:
        reasons.append(f"out of validated domain (distance={result.domain.distance})")

    wide_metrics = [
        name
        for name, estimate in result.metrics.items()
        if _relative_ci_width(estimate) > max_relative_ci_width
    ]
    if wide_metrics:
        reasons.append(
            f"confidence interval exceeds {max_relative_ci_width:.0%} of value for: "
            f"{', '.join(sorted(wide_metrics))}"
        )

    if not reasons:
        escalation = Escalation(recommended=False)
    else:
        escalation = Escalation(recommended=True, next_rung=_NEXT_RUNG, reason="; ".join(reasons))
    return dataclasses.replace(result, escalation=escalation)
