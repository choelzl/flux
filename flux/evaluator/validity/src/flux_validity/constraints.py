"""Checks a `Result` against an Architecture IR document's own declared `constraints` block
(docs/ir.md: "Constraints are part of the architecture document, machine-checkable and
independent of the cost model. This is a direct anti-reward-hacking measure (G14): the validity
checker enforces them even when the cost model has no opinion.") — the schema already had a slot
for this (`architecture.schema.json`'s `constraints: [{kind, max, min, model}]`); nothing
evaluated it independently until this module.

Deliberately does not import any evaluator adapter: this reads two plain dicts (an Architecture
IR document, a `Result`'s `metrics`) and compares numbers. That is what "independent of the cost
model" has to mean structurally — sharing code with the thing being checked would defeat the
point.
"""

from __future__ import annotations

from typing import Any

from flux_evaluator_abi import Constraint, Result, Validity

# A constraint's `kind` (docs/architecture.md's example IR docs use "area_mm2", "tdp_w", "thermal", ...)
# doesn't always spell the same metric name a Result reports (docs/evaluator-abi.md's `Metric` enum:
# "power_w", not "tdp_w") — this is the one place that mapping is spelled out, not guessed at
# per-caller.
_KIND_TO_METRIC_ALIASES: dict[str, str] = {
    "tdp_w": "power_w",
}

# Constraint kinds that name a real physical concern but have no evaluator-independent way to
# check yet (docs/architecture.md: "Thermal and NoC have declared slots even before we have good models") —
# skipped honestly (not silently passed, not falsely failed), same as an absent metric.
_NOT_YET_CHECKABLE_KINDS = frozenset({"thermal"})


def check_declared_constraints(arch: dict[str, Any] | str | None, result: Result) -> Validity:
    """Every constraint in `arch["constraints"]` whose metric is present in `result.metrics` is
    checked against that metric's point value; a metric a constraint names but which no evaluator
    computed (or `arch` being `None`/a bare content-hash string with no inline `constraints` to
    read, or a not-yet-checkable kind like `"thermal"`) is honestly skipped, not treated as a
    pass — `checker_version` reports `checked=<n>/<total>` so a caller can tell the difference
    between "checked and fine" and "nothing to check."

    Compares against `Estimate.value`, not `ci_high` — a v0.1 simplification (docs/architecture.md's own
    "narrow but honest" convention): this does not yet account for calibrated uncertainty when
    a point estimate sits just under a hard limit but its confidence interval crosses it.
    """
    constraints = _constraints_of(arch)
    violations: list[Constraint] = []
    checked = 0

    for constraint in constraints:
        kind = constraint.get("kind")
        if kind in _NOT_YET_CHECKABLE_KINDS:
            continue
        metric_name = _KIND_TO_METRIC_ALIASES.get(kind, kind)
        estimate = result.metrics.get(metric_name)
        if estimate is None:
            continue  # this evaluator run didn't compute a metric this constraint names

        max_bound = constraint.get("max")
        min_bound = constraint.get("min")
        if max_bound is None and min_bound is None:
            # A constraint with neither bound constrains nothing. The IR schema requires only
            # `kind`, so this is schema-valid, and counting it as checked reported
            # `checked=1/1, ok=True` for a comparison that never happened — defeating the exact
            # distinction this function exists to preserve, inside the anti-reward-hacking
            # mechanism G14 names (docs/decisions.md D188). Left in the denominator so the gap
            # between checked and declared stays visible.
            continue

        checked += 1
        value = estimate.value
        if max_bound is not None and value > max_bound:
            violations.append(
                Constraint(
                    kind=kind,
                    detail=f"{metric_name}={value} exceeds declared max={max_bound}",
                )
            )
        if min_bound is not None and value < min_bound:
            violations.append(
                Constraint(
                    kind=kind,
                    detail=f"{metric_name}={value} is below declared min={min_bound}",
                )
            )

    return Validity(
        ok=not violations,
        violations=tuple(violations),
        checker_version=f"constraints-v0.1:checked={checked}/{len(constraints)}",
    )


def _constraints_of(arch: dict[str, Any] | str | None) -> list[dict[str, Any]]:
    if not isinstance(arch, dict):
        return []  # None, or an unresolved content-hash string — no inline document to read
    return arch.get("constraints", [])
