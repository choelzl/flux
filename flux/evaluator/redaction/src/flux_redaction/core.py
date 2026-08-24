"""Real redaction primitives (docs/decisions.md D93) — docs/gap-analysis.md G15's own named fix,
verbatim: "a redaction layer between evaluator outputs and model context (normalized metrics,
rank orderings, relative deltas instead of absolute numbers)". Two real, independent mechanisms,
both implemented here, generically, over plain `float` values — not tied to any one evaluator's
own result type, so a future confidential-PDK-backed `Result`-shaped evaluator can reuse this
exact code, not just `asap7.py`'s own thin adapter.

**Structurally, not just conventionally, non-leaking — with one honest, load-bearing caveat.**
`RelativeDelta` and `RankedCandidate` below have no field that could hold a real absolute value —
this isn't "please don't read the raw number," it's "the raw number was never assigned to
anything this object can return." What that guarantees, precisely: a caller holding only the
redacted object cannot recover the absolute value *from the object*. It does NOT guarantee the
delta is uninvertible in general — a caller who independently knows the baseline's absolute
value (e.g. a hand-built module of known public ASAP7 cells as `baseline_module_source`)
computes `candidate = baseline * (1 + relative_delta)` exactly (review finding). Genuine
non-invertibility additionally requires a baseline the caller cannot anchor — a
deployment/policy property (e.g. a fixed, operator-chosen reference design), not something this
data type alone can provide. Stated here so the guarantee is used for what it actually is.
"""

from __future__ import annotations

from dataclasses import dataclass


class NoBaselineError(ValueError):
    """Raised when a relative-delta redaction is asked for a metric with no real baseline value
    to compare against — computing "% different from nothing" would be fabricating a number, not
    redacting a real one.
    """


@dataclass(frozen=True, slots=True)
class RelativeDelta:
    """A real `(value - baseline) / baseline` fraction — the "relative deltas instead of
    absolute numbers" strategy G15's own fix names. `better_than_baseline` is a real, derived
    fact (does lower or higher count as better, per the real `minimize` the caller declared),
    computed once here so a downstream agent never needs the real absolute values to know which
    direction is good.
    """

    relative_delta: float
    better_than_baseline: bool

    def to_dict(self) -> dict[str, float | bool]:
        return {"relative_delta": self.relative_delta, "better_than_baseline": self.better_than_baseline}


def redact_relative(value: float, baseline_value: float, *, minimize: bool = True) -> RelativeDelta:
    """Real relative-delta redaction: `value` and `baseline_value` are real, absolute numbers
    (e.g. a real PDK-derived area) that never appear anywhere in the return value — only their
    real ratio does. Raises `NoBaselineError` if `baseline_value` is `0` (a real relative delta
    against a zero baseline is undefined, not silently `inf`/`nan`).
    """
    if baseline_value == 0:
        raise NoBaselineError(f"baseline_value=0 — a real relative delta against a zero baseline is undefined")
    relative_delta = (value - baseline_value) / baseline_value
    is_better = (relative_delta < 0) if minimize else (relative_delta > 0)
    return RelativeDelta(relative_delta=relative_delta, better_than_baseline=is_better)


@dataclass(frozen=True, slots=True)
class RankedCandidate:
    """A real rank (1 = best) among real candidates — the "rank orderings ... instead of
    absolute numbers" strategy G15's own fix names. No real metric value anywhere in this type.
    """

    candidate_id: str
    rank: int

    def to_dict(self) -> dict[str, str | int]:
        return {"candidate_id": self.candidate_id, "rank": self.rank}


def redact_ranking(candidates: list[tuple[str, float]], *, minimize: bool = True) -> list[RankedCandidate]:
    """Real rank-ordering redaction: sorts `candidates` (id, real absolute value) by their real
    value and returns only `(candidate_id, rank)` pairs, 1 = best. Ties share the same real
    comparison outcome but are broken by `candidate_id` for a real, deterministic order (never
    input order, which would silently leak a hint about relative closeness via list position).
    Raises `ValueError` if `candidates` is empty — nothing real to rank.
    """
    if not candidates:
        raise ValueError("candidates must be non-empty — nothing real to rank")
    ordered = sorted(candidates, key=lambda c: (c[1] if minimize else -c[1], c[0]))
    return [RankedCandidate(candidate_id=cid, rank=i + 1) for i, (cid, _value) in enumerate(ordered)]
