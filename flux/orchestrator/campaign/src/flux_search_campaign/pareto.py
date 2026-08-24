"""Multi-objective dominance with calibrated confidence intervals (docs/decisions.md D218).

Two related sets, deliberately distinct:

- `pareto_frontier` — **point-value** dominance. Crisp and deterministic, which is what the stop
  criterion ("frontier membership unchanged for N trials") and status reporting need. With
  uncalibrated evaluators every estimate is a point anyway, so this is the only frontier there is.

- `frontier_contenders` — the **escalation set**: everything the screening data cannot rule out,
  generalizing `flux_search_architecture.dse.contenders()`'s closed-interval overlap rule (D105)
  to multiple objectives. A candidate is only excluded when some other candidate is strictly
  interval-better on at least one objective, never interval-worse on any, *and* never point-worse
  on any — the last clause is the conservative addition multi-objective needs: an interval can
  strictly beat another on metric A while its point value quietly loses on metric B inside
  overlapping intervals, and eliminating the point-B-winner on that evidence would buy the wrong
  candidate. Contenders is always a superset of the frontier.

Every metric read goes through `estimate_of` (D201) — callers pass only trials whose results
carry every objective metric; a result that legally omitted one is a per-candidate refusal the
runner classified upstream, not an input to dominance in either direction (D112).
"""

from __future__ import annotations

from typing import Any, Sequence

from .objective import Objective, ObjectiveMetric

BETTER, WORSE, UNRESOLVED = "better", "worse", "unresolved"


def _as_result(trial_or_result: Any) -> Any:
    """The set functions take trial objects (anything exposing `.result`) or bare Results; the
    pairwise functions take Results. One normalization point instead of two calling conventions."""
    return getattr(trial_or_result, "result", trial_or_result)


def _oriented(result: Any, om: ObjectiveMetric) -> tuple[float, float, float]:
    """(lo, value, hi) with maximize negated so that lower-is-better always holds below."""
    est = result.estimate_of(om.metric)
    if om.minimize:
        return (est.ci_low, est.value, est.ci_high)
    return (-est.ci_high, -est.value, -est.ci_low)


def compare_metric(a: Any, b: Any, om: ObjectiveMetric) -> str:
    """Three-valued interval comparison of results a, b on one objective. Closed intervals,
    matching `dse.contenders()`'s `<=` overlap test exactly: touching intervals are UNRESOLVED.
    Point estimates (ci_low == ci_high) fall out correctly — strict inequality on the values
    decides, exact equality is UNRESOLVED."""
    a_lo, _, a_hi = _oriented(a, om)
    b_lo, _, b_hi = _oriented(b, om)
    if a_hi < b_lo:
        return BETTER
    if a_lo > b_hi:
        return WORSE
    return UNRESOLVED


def point_dominates(a: Any, b: Any, objectives: Sequence[ObjectiveMetric]) -> bool:
    """Classic weak dominance with strict improvement, on point values only."""
    strictly_better = False
    for om in objectives:
        _, av, _ = _oriented(a, om)
        _, bv, _ = _oriented(b, om)
        if av > bv:
            return False
        if av < bv:
            strictly_better = True
    return strictly_better


def interval_dominates(a: Any, b: Any, objectives: Sequence[ObjectiveMetric]) -> bool:
    """a rules b out: interval-BETTER on >= 1 objective, interval-WORSE on none, and point-worse
    on none. The point clause is what keeps elimination safe under overlap — see module
    docstring."""
    strictly_better = False
    for om in objectives:
        verdict = compare_metric(a, b, om)
        if verdict == WORSE:
            return False
        if verdict == BETTER:
            strictly_better = True
        _, av, _ = _oriented(a, om)
        _, bv, _ = _oriented(b, om)
        if av > bv:
            return False
    return strictly_better


def pareto_frontier(trials: Sequence[Any], objective: Objective) -> list[Any]:
    """The reporting/stop-criterion frontier, branching on the objective's mode (docs/decisions.md
    D221): `pareto` = trials not point-dominated by any other; `weighted` = the trial(s) achieving
    the minimal weighted scalar point value (ties all kept, so the stop criterion's membership
    test stays exact). O(n^2) in pareto mode — measured fine at campaign scale; do not build an
    incremental structure until a measurement says otherwise."""
    if objective.mode == "weighted":
        if not trials:
            return []
        scored = [(weighted_scalar(t, objective)[1], i, t) for i, t in enumerate(trials)]
        best = min(s for s, _, _ in scored)
        return [t for s, _, t in scored if s == best]
    out = []
    for t in trials:
        if not any(
            point_dominates(_as_result(other), _as_result(t), objective.metrics)
            for other in trials
            if other is not t
        ):
            out.append(t)
    return out


def frontier_contenders(trials: Sequence[Any], objective: Objective) -> list[Any]:
    """The escalation set: frontier members first (in input order), then — in pareto mode —
    every other trial no trial interval-dominates; in weighted mode, every trial whose scalar
    interval overlaps the leader's (docs/decisions.md D221). Single objective + uniformly-CI'd
    inputs reproduces `dse.contenders()`'s membership exactly in BOTH modes (unit-tested against
    the real function)."""
    frontier = pareto_frontier(trials, objective)
    on_frontier = {id(t) for t in frontier}
    if objective.mode == "weighted":
        # `dse.contenders()`'s own rule on the scalarized axis: a contender's scalar interval
        # overlaps the leader's (closed intervals). With point estimates this degenerates to the
        # leader alone, exactly like the single-metric case.
        leader_lo, _, leader_hi = weighted_scalar(frontier[0], objective)
        rest = [
            t
            for t in trials
            if id(t) not in on_frontier
            and (lambda lo_hi: lo_hi[0] <= leader_hi and leader_lo <= lo_hi[2])(
                weighted_scalar(t, objective)
            )
        ]
        return frontier + rest
    rest = [
        t
        for t in trials
        if id(t) not in on_frontier
        and not any(
            interval_dominates(_as_result(other), _as_result(t), objective.metrics)
            for other in trials
            if other is not t
        )
    ]
    return frontier + rest


def weighted_scalar(trial_or_result: Any, objective: Objective) -> tuple[float, float, float]:
    """(lo, value, hi) of the weighted sum of oriented metrics — interval arithmetic on the CI
    bounds. Only meaningful in weighted mode, where weights are schema-required on every
    objective and hashed into the objective identity (a weight change is a new campaign)."""
    result = _as_result(trial_or_result)
    lo = value = hi = 0.0
    for om in objective.metrics:
        m_lo, m_val, m_hi = _oriented(result, om)
        assert om.weight is not None  # parse_objective enforced this for weighted mode
        lo += om.weight * m_lo
        value += om.weight * m_val
        hi += om.weight * m_hi
    return (lo, value, hi)
