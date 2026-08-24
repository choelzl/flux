"""Apply calibration to an Evaluator ABI `Result` (docs/calibration.md, docs/architecture.md's design principle 2: "a result
without a calibration id and a confidence interval is a bug").

This is deliberately a *post-processing* step over an existing `Result`, not something baked
into `evaluators/zigzag`/`evaluators/timeloop` themselves — matching the layering in
docs/architecture.md, where Calibration (L3) sits above the Evaluator ABI (L4) and consumes its output,
rather than every adapter having to know about calibration internals.
"""

from __future__ import annotations

import dataclasses

from flux_evaluator_abi import Domain, Estimate, Result

from .store import CalibrationStore, ResidualStats

# n < this many calibration records: too few to trust a computed std meaningfully (variance from
# a single pair, or two, is not a real distribution) — still widen the interval using what we
# have, but the record count itself (in Domain, surfaced via distance) says how little that is.
_MIN_TRUSTED_N = 3

# CI = [value / factor, value * factor], factor = 1 + Z*(|mean residual| + std residual).
# Multiplicative, not additive: these metrics are non-negative and only ever scale, and an additive
# interval really did produce a negative lower bound for a cycle count (a ~204% mean residual made
# the half-width exceed the value). Z=2 stands in for "~95% under a normal-residual assumption",
# which at n<=3 is unvalidated — see docs/calibration-report.md's CI-coverage KPI.
_Z = 2.0

# A measured spread of exactly zero is not evidence of zero true spread — it is what a finite
# sample of a deterministic-looking model produces (D105 measured `std = 0.00` for real ZigZag).
# Without a floor the debiased interval collapses to a point, which makes `check_conformance`'s
# `ci_low <= reference <= ci_high` a float-equality test (so conformance always fails) and drives
# `_relative_ci_width` to 0 (so escalation is never recommended and the flywheel stops for that
# evaluator+metric). 1% is deliberately small — enough to keep the interval non-degenerate,
# far too small to rescue a genuinely wrong correction. Found by review, docs/decisions.md D112.
_MIN_RELATIVE_SPREAD = 0.01

# The reciprocal `value / (centre - half)` explodes as the denominator approaches zero: at
# `centre - half = 1e-7` a raw 1000 becomes an upper bound of ~5e9. Requiring the denominator to
# keep a real fraction of the centre falls back to the conservative form well before that,
# instead of only at exactly <= 0 (D112).
_MIN_DENOM_FRACTION = 0.25


def calibrate_estimate(
    estimate: Estimate,
    stats: ResidualStats | None,
    *,
    trust_pool: bool = True,
    validated_here: bool = True,
) -> Estimate:
    """Calibrate an Estimate against real residual statistics. If `stats` is None (no calibration
    data at all), returns the estimate unchanged — still a bare point estimate, honestly, rather
    than inventing a CI from nothing.

    **Two regimes, split at `_MIN_TRUSTED_N` (docs/decisions.md D106).**

    *With enough residuals (`n >= _MIN_TRUSTED_N`) the bias is corrected, not just fenced.* The
    residual is defined as `r = (predicted - reference) / reference`, so `reference = predicted /
    (1 + r)`: a mean residual is a *known systematic offset*, and the calibrated value becomes
    `value / (1 + mean)`. The interval then spans only the real residual *spread*,
    `r in [mean - Z*std, mean + Z*std]`, giving `[value / (1 + mean + Z*std), value / (1 + mean
    - Z*std)]`. This is the fix D105 measured the need for: previously a perfectly-characterized
    2.94x overestimate (`mean=+1.94, std=0.00`) was reported as a ~24x-wide interval around the
    *uncorrected* value — treating a known bias as if it were uncertainty, so more calibration
    data made intervals wider, not tighter.

    *Below that threshold the old conservative behavior is kept deliberately*: widen around the
    uncorrected value by `Z*(|mean| + std)`. One or two residuals do not establish that a
    correction generalizes — docs/decisions.md D101 measured exactly that failure (a single
    width-32 residual did not transfer to width 8; the family residual ranges ~1.93x-2.93x).
    Correcting on that evidence would trade a wide-but-honest interval for a narrow-but-wrong
    one, which is the worse error for a calibration layer.

    Degenerate cases fall back to the conservative form rather than producing nonsense: a mean
    residual at or below -100% (`1 + mean <= 0`, a "prediction" non-positive relative to
    reference), and a spread wide enough to approach it (`_MIN_DENOM_FRACTION`, a real margin
    rather than the knife edge of `> 0`, which admitted ~5e9-wide upper bounds).

    `trust_pool=False` forces the conservative path even with plenty of residuals — for a
    candidate whose *own* record is caveated, i.e. one the store already knows the pooled
    statistics do not describe (docs/decisions.md D112). Correcting such a candidate by the pool
    produced a 34%-wrong point estimate inside a 1.4%-wide interval: confidently wrong, the worst
    failure mode a calibration layer has.

    `validated_here=False` — this exact (workload, arch) point has never been measured against a
    reference, i.e. the correction is being *extrapolated* — keeps the corrected **value** but
    widens the **interval** to the conservative form (docs/decisions.md D122). Those are two
    different claims and were wrongly coupled: the point estimate is the best available guess at
    the bias, while the interval asserts how well that bias is known *here*, which is not at all.
    Measured, not theorised: with three calibration architectures whose residuals agreed to within
    the spread floor, a held-out architecture was reported as 86.6 in [84.9, 88.4] when the real
    reference was 128.0 — a 2%-wide interval missing reality by 32%, because a degenerate pool
    spread says "this bias is precisely known", never "this bias is precisely known *for points
    like these*".
    """
    if stats is None or stats.n == 0 or estimate.value == 0:
        return estimate

    mean, std = stats.mean_relative_residual, stats.std_relative_residual
    # Floor the spread (see `_MIN_RELATIVE_SPREAD`): scaled by the correction magnitude, so the
    # floor stays proportionate for a model that is 3x off and for one that is 3% off.
    std = max(std, _MIN_RELATIVE_SPREAD * abs(1.0 + mean))
    centre, half = 1.0 + mean, _Z * std
    # Distinct measured *points*, not rows: the same (workload, arch) recorded three times is one
    # measurement repeated, and it used to satisfy this gate and collapse the interval 17x on zero
    # new information. Falls back to `n` when a caller built `stats` by hand (D171).
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
            # value they are attached to — a conservative band around the raw estimate can sit
            # entirely above a strongly-corrected value, and an interval excluding its own point
            # estimate is not an interval.
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
    # distance is a real, if coarse, 3-tier measure of "how calibrated is this exact query" —
    # not a fabricated placeholder: 0.0 = this exact (workload, arch) pair has been directly
    # compared against a reference; 1.0 = we have calibration data for this evaluator+metric
    # family but not this exact point (extrapolating within a calibrated family).
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

    Post-D106 this changes `Estimate.value`, not only its interval — a calibrated result is a
    *corrected* estimate, not the raw model's own claim.
    """
    evaluator = result.provenance.evaluator
    new_metrics: dict[str, Estimate] = {}
    domains: list[Domain] = []

    for metric_name, estimate in result.metrics.items():
        stats = store.residual_stats(evaluator, metric_name)
        # A caveated exact match means the store has measured *this* candidate and recorded that
        # it is not represented by the pooled statistics (docs/decisions.md D110/D112). Correcting
        # it by that pool would be confidently wrong, so fall back to the conservative form and
        # refuse to call it in-domain, which keeps escalation recommending a real re-measurement.
        own_caveat = store.exact_match_caveat(evaluator, metric_name, workload_hash, arch_hash)
        domain = _domain_for(store, evaluator, metric_name, workload_hash, arch_hash)
        # Correcting and *claiming a tight interval* are separate decisions (D122): the second
        # requires this exact point to have been measured, which is precisely what `in_domain`
        # already means. Reusing it here rather than recomputing keeps the interval's width and
        # the escalation trigger reading the same fact.
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

    # `nearest_calibration` is taken from any metric that actually has one, NOT from the worst
    # domain (D112): the worst is routinely an uncalibrated metric the reference can't produce
    # (real ZigZag always returns energy_pj, real RTL never does), which dropped the calibration
    # id from every result whose latency had in fact been corrected — a `Result` with no way to
    # tell it had been. Contradicted this module's own "a result without a calibration id ... is
    # a bug" opening.
    nearest = next((d.nearest_calibration for d in domains if d.nearest_calibration), None)

    return dataclasses.replace(
        result,
        metrics=new_metrics,
        domain=worst_domain,
        # Per-metric domains kept alongside the aggregate (docs/decisions.md D140): they were
        # already computed here and then discarded, which is what made a directly-measured latency
        # read as extrapolating whenever an uncalibrated metric rode along with it.
        metric_domains=dict(zip(result.metrics, domains)),
        provenance=dataclasses.replace(result.provenance, calibration=nearest),
    )
