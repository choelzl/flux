"""Escalation policy (docs/calibration.md): "spend high-fidelity budget where it changes the answer" —
points on or near the Pareto front, points where the analytic CI is wide, points out of
validated domain.

v0.1 implements two of those three triggers — CI width and domain — not Pareto-front relevance.
A single `Result` has no notion of "the front" without seeing its neighbours in a candidate set;
that's a property of a search run (L5), not something one Calibration (L3) call over one result
can determine.

`next_stage` names a real, invokable next step, and since D448 it is CHECKED to be one: the name
is validated against the ABI registry as it is written (`escalation_stage`), so what a result
recommends is always something `make_evaluator` can build. `evaluator/rtl/`'s `RTLEvaluator`
(registered as `"rtl"`) is a real Verilator-simulation adapter — see its README
and docs/calibration-report.md for the first calibration records built against its real
measurement rather than another analytic model's estimate. It only covers the narrow shape that
adapter's hand-written `mac_array.sv` can express (docs/decisions.md D2's build-vs-reuse
scope), so escalating an arbitrary candidate can still fail with `NotExpressibleError` — that's
the caller's problem to handle (same as calling any evaluator on an out-of-scope candidate), not
this policy's; naming the stage is a recommendation, not a guarantee it will succeed.
"""

from __future__ import annotations

import dataclasses

from flux_evaluator_abi import Escalation, Estimate, Result, escalation_stage

_DEFAULT_MAX_RELATIVE_CI_WIDTH = 0.5  # (ci_high - ci_low) / value > this triggers escalation
#: The backend a recommendation names, as a registry name -- checked against the registry when
#: it is written, not trusted as a word (D448).
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

    `next_stage` is the backend to recommend, validated against the ABI registry (D448): a
    caller escalating to something other than `rtl` (an OpenROAD stage for a physical claim,
    `gem5` for a CPU one) names it here and gets a loud error for a name nothing registers,
    rather than writing a word no one can invoke.
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
