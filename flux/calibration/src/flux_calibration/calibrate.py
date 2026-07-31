"""Apply calibration to an Evaluator ABI `Result` (docs/04.md §5, §1 principle 2: "a result
without a calibration id and a confidence interval is a bug").

This is deliberately a *post-processing* step over an existing `Result`, not something baked
into `evaluators/zigzag`/`evaluators/timeloop` themselves — matching the layering in
docs/04.md §2, where Calibration (L3) sits above the Evaluator ABI (L4) and consumes its output,
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

# CI = [value / factor, value * factor], factor = 1 + Z*(|mean relative residual| + std relative
# residual). Multiplicative, not additive: metrics/04.md's domain — latency, energy, area — are
# all strictly non-negative and only ever scale, never shift, so an additive interval
# (value ± half_width) can and did produce a negative lower bound for a cycle count the first
# time this was run against real data (mean_relative_residual ~204% on one metric made
# half_width larger than value itself). A multiplicative interval can't go negative by
# construction, at the cost of being asymmetric around the point value — which is the honest
# shape for a residual this large, not a cosmetic choice.
#
# Z=2 is a deliberately loose, round choice standing in for "~95% coverage under a
# normal-residual assumption" — with n<=3 samples that assumption is itself unvalidated; see
# docs/calibration-report.md's KPI section (CI coverage) for why this is flagged, not asserted.
_Z = 2.0


def calibrate_estimate(estimate: Estimate, stats: ResidualStats | None) -> Estimate:
    """Widen an Estimate's confidence interval using residual statistics. If `stats` is None
    (no calibration data exists for this evaluator+metric at all), returns the estimate
    unchanged — still a bare point estimate, honestly, rather than inventing a CI from nothing.
    """
    if stats is None or stats.n == 0 or estimate.value == 0:
        return estimate
    factor = 1 + _Z * (abs(stats.mean_relative_residual) + stats.std_relative_residual)
    ci_low = estimate.value / factor
    ci_high = estimate.value * factor
    if ci_low > ci_high:
        ci_low, ci_high = ci_high, ci_low
    return dataclasses.replace(estimate, ci_low=ci_low, ci_high=ci_high)


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
    in_domain = exact and stats.n >= _MIN_TRUSTED_N
    calibration_id = f"{evaluator}:{metric}:n={stats.n}"
    return Domain(in_domain=in_domain, distance=distance, nearest_calibration=calibration_id)


def calibrate_result(
    result: Result, store: CalibrationStore, *, workload_hash: str, arch_hash: str | None = None
) -> Result:
    """Return a new Result with every metric's Estimate CI widened by calibration data and
    `domain` recomputed from the calibration store, leaving the original Result untouched.
    """
    evaluator = result.provenance.evaluator
    new_metrics: dict[str, Estimate] = {}
    domains: list[Domain] = []

    for metric_name, estimate in result.metrics.items():
        stats = store.residual_stats(evaluator, metric_name)
        new_metrics[metric_name] = calibrate_estimate(estimate, stats)
        domains.append(_domain_for(store, evaluator, metric_name, workload_hash, arch_hash))

    # A Result has one `domain`, not one per metric — conservatively take the worst (furthest,
    # least in-domain) of the per-metric domains rather than averaging or picking the first.
    worst_domain = min(
        domains, key=lambda d: (d.in_domain, -d.distance), default=result.domain
    ) if domains else result.domain

    return dataclasses.replace(
        result,
        metrics=new_metrics,
        domain=worst_domain,
        provenance=dataclasses.replace(
            result.provenance, calibration=worst_domain.nearest_calibration
        ),
    )
