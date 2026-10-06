"""Whether a design qualifies (D899): the one stage-aware reading of the objectives' limits, shared by
the loop's decision (D900), the web's results and its charts.

A limit is judged on the numbers of the stage it names (`stage: deepest` is the chain's last; no
stage, the deepest the design reached). For each limit a design is one of:

- **met** -- measured on that stage and within the limit;
- **missed** -- measured there and outside it: `"area_um2 31 is above the limit 20 (confirm)"`;
- **not measured** -- the stage that must judge it measured the design without the metric (or with
  a number that is not finite), or the stage is not one the design can still reach (not in the
  chain, or the chain went no further for it): `"speed not measured (confirm)"`;
- **pending** -- the stage that judges it comes later in the chain than the design has reached and
  nothing stopped it: `"fmax_mhz waits for the confirm stage"`.

A design is **eligible** only when every limit is met: pending and not measured both lack the
evidence a strict answer needs. `pending` says the design misses nothing yet -- only later stages
can tell. A stage's cutoff prunes what climbs; it is not a requirement unless the document says it
as an objective's limit (D900)."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

__all__ = ["Eligibility", "eligibility", "judging_stage"]


@dataclass(frozen=True)
class Eligibility:
    """A design against every limit: `eligible` when all are met; `pending` when none is missed or
    unmeasured but some wait for a later stage; `reasons` one short line per limit not met; `unmet`
    the metrics of the limits missed or not measured."""

    eligible: bool
    pending: bool = False
    reasons: tuple[str, ...] = ()
    unmet: tuple[str, ...] = field(default_factory=tuple)

    def to_doc(self) -> dict[str, Any]:
        return {"eligible": self.eligible, "pending": self.pending, "reasons": list(self.reasons)}


def judging_stage(o: Any, measured: Sequence[str], stages: Sequence[str]) -> str | None:
    """The stage whose numbers judge limit `o` for a design measured on `measured` (in chain order):
    the stage it names, the chain's last for `deepest`, else the deepest the design reached."""
    named = getattr(o, "stage", None)
    if named in ("deepest", "last"):
        return list(stages)[-1] if stages else (measured[-1] if measured else None)
    if named:
        return named
    return measured[-1] if measured else None


def eligibility(objectives: Any, numbers: Mapping[str, Mapping[str, Any]], stages: Sequence[str],
                *, stopped: bool = False) -> Eligibility:
    """`numbers`: the design's latest numbers per stage; `stages`: the chain in order. `stopped`: the
    design will climb no further (a cutoff cut it, or the run measures no deeper), so a limit on a
    later stage is not measured rather than pending. No limits: eligible."""
    order = {s: i for i, s in enumerate(stages)}
    measured = sorted(numbers, key=lambda s: order.get(s, -1))
    deepest = order.get(measured[-1], -1) if measured else -1
    reasons: list[str] = []
    unmet: list[str] = []
    waiting = False
    for o in getattr(objectives, "limits", ()):
        st = judging_stage(o, measured, stages)
        if st is not None and st in numbers:
            v = _finite(numbers[st].get(o.metric))
            goal = o.goal_at(st, stages)
            if v is None:
                reasons.append(f"{o.metric} not measured ({st})")
                unmet.append(o.metric)
            elif not (v >= goal if o.direction == "maximize" else v <= goal):
                reasons.append(f"{o.metric} {v:g} {'is below' if o.direction == 'maximize' else 'is above'} "
                               f"the limit {goal:g} ({st})")
                unmet.append(o.metric)
            continue
        if st is not None and not stopped and order.get(st, -1) > deepest:
            reasons.append(f"{o.metric} waits for the {st} stage")
            waiting = True
            continue
        reasons.append(f"{o.metric} not measured ({st or 'no stage'})")
        unmet.append(o.metric)
    return Eligibility(eligible=not reasons, pending=waiting and not unmet, reasons=tuple(reasons), unmet=tuple(unmet))


def _finite(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) and not isinstance(v, bool) else None
