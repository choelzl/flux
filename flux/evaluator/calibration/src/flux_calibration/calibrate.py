"""Apply calibration to an Evaluator ABI `Result` (docs/calibration.md): a result without a
calibration id and a confidence interval is a bug.

A post-processing step over an existing `Result`: calibration (L3) sits above the Evaluator
ABI (L4), so adapters need not know calibration internals.
"""

from __future__ import annotations

import dataclasses

from flux_evaluator_abi import Domain, Estimate, Result

from .store import CalibrationStore, ResidualStats

# Below this many calibration points a computed std is not trusted: the interval is widened
# conservatively instead of the bias being corrected.
_MIN_TRUSTED_N = 3

# CI = [value / factor, value * factor], factor = 1 + Z*(|mean residual| + std residual).
# Multiplicative, not additive: the metrics are non-negative, and an additive interval can give
# a negative lower bound. Z=2 is ~95% under a normal-residual assumption (unvalidated at n<=3).
_Z = 2.0

# A measured spread of zero is not zero true spread. Without a floor the interval collapses to
# a point: conformance becomes a float-equality test and escalation is never recommended.
# 1% keeps the interval non-degenerate without rescuing a wrong correction (D112).
_MIN_RELATIVE_SPREAD = 0.01

# `value / (centre - half)` explodes as the denominator nears zero; below this fraction of the
# centre, fall back to the conservative form (D112).
_MIN_DENOM_FRACTION = 0.25


def calibrate_estimate(
    estimate: Estimate,
    stats: ResidualStats | None,
    *,
    trust_pool: bool = True,
    validated_here: bool = True,
) -> Estimate:
    """Calibrate an Estimate against residual statistics. With `stats` None (no calibration
    data), the estimate is returned unchanged rather than inventing an interval.

    Two regimes, split at `_MIN_TRUSTED_N` (D106):

    - Enough residuals: the bias is corrected. With `r = (predicted - reference) / reference`,
      the value becomes `value / (1 + mean)` and the interval spans only the spread:
      `[value / (1 + mean + Z*std), value / (1 + mean - Z*std)]`.
    - Fewer: widen around the uncorrected value by `Z*(|mean| + std)`, since one or two
      residuals do not show a correction generalizes (D101).

    Degenerate cases (`1 + mean <= 0`, or a spread approaching it per `_MIN_DENOM_FRACTION`)
    fall back to the conservative form.

    `trust_pool=False` forces the conservative path, for a candidate whose own record is
    caveated as not described by the pooled statistics (D112).

    `validated_here=False` (this exact point was never measured against a reference) keeps the
    corrected value but widens the interval to the conservative form (D122): a tight pool
    spread says how well the bias is known, not how well it transfers to this point.
    """
    if stats is None or stats.n == 0 or estimate.value == 0:
        return estimate

    mean, std = stats.mean_relative_residual, stats.std_relative_residual
    # Floor the spread (see `_MIN_RELATIVE_SPREAD`): scaled by the correction magnitude, so the
    # floor stays proportionate for a model that is 3x off and for one that is 3% off.
    std = max(std, _MIN_RELATIVE_SPREAD * abs(1.0 + mean))
    centre, half = 1.0 + mean, _Z * std
    # Distinct measured points, not rows: a repeated (workload, arch) is one measurement (D171).
    # Falls back to `n` when a caller built `stats` by hand.
    effective_n = stats.distinct_points if stats.distinct_points is not None else stats.n
    trusted = effective_n >= _MIN_TRUSTED_N and trust_pool
    correctable = trusted and centre > 0 and centre - half > _MIN_DENOM_FRACTION * centre

    # The conservative interval, around the *uncorrected* value: what is claimable when the
    # correction itself has not been validated at this point.
    factor = 1 + _Z * (abs(mean) + std)
    conservative_low = estimate.value / factor
    conservative_high = estimate.value * factor
    if conservative_low > conservative_high:
        conservative_low, conservative_high = conservative_high, conservative_low

    if correctable:
        value = estimate.value / centre
        if validated_here:
            ci_low = estimate.value / (centre + half)
            ci_high = estimate.value / (centre - half)
        else:
            # Corrected value, unvalidated interval (D122). The bounds must still bracket the
            # corrected value, which a band around the raw estimate may not.
            ci_low = min(conservative_low, value)
            ci_high = max(conservative_high, value)
        return dataclasses.replace(estimate, value=value, ci_low=ci_low, ci_high=ci_high)

    return dataclasses.replace(estimate, ci_low=conservative_low, ci_high=conservative_high)


def _domain_for(
    store: CalibrationStore, evaluator: str, metric: str, workload_hash: str, arch_hash: str | None
) -> Domain:
    stats = store.residual_stats(evaluator, metric)
    if stats is None:
        return Domain(in_domain=False, distance=float("inf"), nearest_calibration=None)

    exact = store.has_exact_match(evaluator, metric, workload_hash, arch_hash)
    # distance, a coarse measure of how calibrated this query is: 0.0 = this exact (workload,
    # arch) pair was compared against a reference; 1.0 = calibration data exists for this
    # evaluator+metric family but not this point.
    distance = 0.0 if exact else 1.0
    effective_n = stats.distinct_points if stats.distinct_points is not None else stats.n
    in_domain = exact and effective_n >= _MIN_TRUSTED_N  # distinct points, not rows (D171)
    calibration_id = f"{evaluator}:{metric}:n={stats.n}"
    return Domain(in_domain=in_domain, distance=distance, nearest_calibration=calibration_id)


def calibrate_result(
    result: Result, store: CalibrationStore, *, workload_hash: str, arch_hash: str | None = None
) -> Result:
    """Return a new Result with every metric's Estimate calibrated against residual data (bias
    corrected where the data supports it, see `calibrate_estimate`) and `domain` recomputed from
    the calibration store, leaving the original Result untouched.

    This changes `Estimate.value`, not only its interval: a calibrated result is a corrected
    estimate, not the raw model's claim.
    """
    evaluator = result.provenance.evaluator
    new_metrics: dict[str, Estimate] = {}
    domains: list[Domain] = []

    for metric_name, estimate in result.metrics.items():
        stats = store.residual_stats(evaluator, metric_name)
        # A caveated exact match is not described by the pooled statistics (D112): use the
        # conservative form and not in-domain, so escalation still recommends re-measurement.
        own_caveat = store.exact_match_caveat(evaluator, metric_name, workload_hash, arch_hash)
        domain = _domain_for(store, evaluator, metric_name, workload_hash, arch_hash)
        # A tight interval requires this exact point to have been measured, which is what
        # `in_domain` means (D122); reusing it keeps interval width and escalation consistent.
        new_metrics[metric_name] = calibrate_estimate(
            estimate, stats,
            trust_pool=own_caveat is None,
            validated_here=domain.in_domain,
        )
        if own_caveat is not None:
            domain = dataclasses.replace(domain, in_domain=False)
        domains.append(domain)

    # A Result has one `domain`, not one per metric — conservatively take the worst (furthest,
    # least in-domain) of the per-metric domains rather than averaging or picking the first.
    worst_domain = min(
        domains, key=lambda d: (d.in_domain, -d.distance), default=result.domain
    ) if domains else result.domain

    # `nearest_calibration` comes from any metric that has one, not from the worst domain: the
    # worst is often an uncalibrated metric, which would drop the calibration id (D112).
    nearest = next((d.nearest_calibration for d in domains if d.nearest_calibration), None)

    return dataclasses.replace(
        result,
        metrics=new_metrics,
        domain=worst_domain,
        # Per-metric domains kept alongside the aggregate, so a measured metric does not read
        # as extrapolating because an uncalibrated one rode along (D140).
        metric_domains=dict(zip(result.metrics, domains)),
        provenance=dataclasses.replace(result.provenance, calibration=nearest),
    )
