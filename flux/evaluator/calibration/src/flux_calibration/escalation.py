"""Escalation policy (docs/calibration.md): spend high-fidelity budget where it changes the answer.

Implements two triggers: a wide analytic CI and out-of-domain. Pareto-front relevance needs a
candidate set, so it belongs to the search, not to a per-result call.

`next_stage` is validated against the ABI registry (D448), so a recommendation always names
something `make_evaluator` can build. The default `"rtl"` evaluator covers a narrow design
shape, so escalating an arbitrary candidate may still raise `NotExpressibleError`; handling
that is the caller's job.
"""

from __future__ import annotations

import dataclasses

from flux_evaluator_abi import Escalation, Estimate, Result, escalation_stage

_DEFAULT_MAX_RELATIVE_CI_WIDTH = 0.5  # (ci_high - ci_low) / value > this triggers escalation
#: The backend a recommendation names, as a registry name (checked against the registry, D448).
_NEXT_STAGE = "rtl"


def _relative_ci_width(estimate: Estimate) -> float:
    if estimate.value == 0:
        return 0.0
    return (estimate.ci_high - estimate.ci_low) / abs(estimate.value)


def apply_escalation_policy(
    result: Result, *, max_relative_ci_width: float = _DEFAULT_MAX_RELATIVE_CI_WIDTH,
    next_stage: str = _NEXT_STAGE,
) -> Result:
    """Recompute `result.escalation` from its (already calibrated) metrics and domain.

    Call this *after* `calibrate_result()` — it trusts the confidence intervals and domain it's
    given, it doesn't compute them. Two independent triggers, either sufficient on its own:
    out-of-domain (per `result.domain.in_domain`), or a confidence interval wider than
    `max_relative_ci_width` of the point value for any metric.

    `next_stage` is the backend to recommend, validated against the ABI registry (D448); an
    unregistered name raises.
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
        escalation = Escalation(recommended=True, next_stage=escalation_stage(next_stage),
                                reason="; ".join(reasons))
    return dataclasses.replace(result, escalation=escalation)
