"""The objective as a vector (D511): what a campaign is for, ordered, and the one rule that
says which of two measured designs is better.

    objectives:
      - {metric: fmax_mhz, direction: maximize, goal: 800, stage: confirm, tie: 0.03}
      - {metric: area_um2, direction: minimize, goal: 80}
      - {metric: power_w,  direction: minimize}

Every objective with a `goal` is a LIMIT that must hold: at least the goal when maximizing, at
most it when minimizing (D658). Among designs meeting every limit, the objectives without a goal
decide in the order written, each next one breaking the ties of those before; the ones marked
`balance: true` decide together, as the knee of their front. A design meeting every limit beats
one that does not; two that miss are ordered by fewer limits missed, then the smaller relative
shortfall. The frontier's axes, the decision, the early stop and which of a part's admitted
designs stands at a reload all derive from this one vector.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, replace
from typing import Any, Callable, Iterable, Sequence

__all__ = ["Objective", "Objectives"]

_DIRECTIONS = ("maximize", "minimize")


#: the unit a known metric is said in (D628); another metric is said by its name
UNITS = {"fmax_mhz": "MHz", "area_um2": "um2", "area_mm2": "mm2", "power_w": "W", "power_mw": "mW",
         "time_ms": "ms", "latency_cycles": "cycles", "energy_pj": "pJ", "cell_count": "cells",
         # `flux prog` (D661)
         "time_ms_stddev": "ms", "time_ms_min": "ms", "instructions": "instr", "d1_misses": "misses",
         "ll_misses": "misses", "branch_mispredicts": "mispredicts",
         "text_bytes": "B", "data_bytes": "B", "bss_bytes": "B"}


@dataclass(frozen=True)
class Objective:
    metric: str
    direction: str = "maximize"
    goal: float | None = None        # a floor (maximize) or a ceiling (minimize) the design must reach
    stage: str | None = None         # whose numbers count; None = wherever the loop measured it
    tie: float = 0.0                 # two values within this relative band are equal
    unit: str = ""                   # how a number is said ("MHz", "um2"); the metric's name when empty
    margin: float = 0.0              # a stage shallower than `stage` must clear the goal by this fraction (D522)
    #: the margin the record measured per shallower stage, as (stage, margin) pairs from the
    #: calibration ratios; the document's `margin` is the floor (D562)
    margins: tuple[tuple[str, float], ...] = ()
    keep: float | None = None        # a goal relative to the best measured: keep this share of its gain
    above: float = 0.0               # ... over this value (1.0 for a speedup)
    balance: bool = False            # decided with the other balance objectives, as their knee (D658)

    @property
    def label(self) -> str:
        return self.unit or self.metric

    def __post_init__(self) -> None:
        if self.direction not in _DIRECTIONS:
            raise ValueError(f"objective {self.metric}: direction must be one of {_DIRECTIONS}, got {self.direction!r}")
        if self.tie < 0:
            raise ValueError(f"objective {self.metric}: tie must be >= 0")
        if self.margin < 0:
            raise ValueError(f"objective {self.metric}: margin must be >= 0")
        if self.keep is not None and (self.goal is not None or not 0 < self.keep <= 1):
            raise ValueError(f"objective {self.metric}: keep is a share in (0, 1], instead of a goal")
        if self.balance and (self.goal is not None or self.keep is not None):
            raise ValueError(f"objective {self.metric}: a balance objective has no goal (a goal is a limit)")

    @classmethod
    def from_doc(cls, doc: Any, index: int = 0) -> "Objective":
        """`"fmax_mhz"`, or `{metric, direction?, goal?, stage?, tie?, balance?}`; `goal` may be a
        number or `">= 800"` / `"<= 0.5"` (the comparator names the direction)."""
        if isinstance(doc, str):
            doc = {"metric": doc}
        if not isinstance(doc, dict) or not isinstance(doc.get("metric"), str) or not doc["metric"].strip():
            raise ValueError(f"objectives[{index}] needs a `metric`")
        direction = doc.get("direction")
        goal = doc.get("goal")
        if isinstance(goal, str):
            m = re.fullmatch(r"\s*(>=|<=|>|<|=)?\s*([-+0-9.eE]+)\s*", goal)
            if not m:
                raise ValueError(f"objectives[{index}].goal {goal!r}: a number, or '>= 800' / '<= 0.5'")
            if m.group(1) in (">=", ">"):
                direction = direction or "maximize"
            elif m.group(1) in ("<=", "<"):
                direction = direction or "minimize"
            goal = float(m.group(2))
        elif goal is not None:
            goal = float(goal)
        # D628: a goal is judged on the deepest stage unless another is named; a known metric
        # has its unit
        stage = doc.get("stage") or ("deepest" if goal is not None else None)
        unit = str(doc.get("unit") or UNITS.get(doc["metric"], ""))
        keep = doc.get("keep")
        return cls(doc["metric"], str(direction or "maximize"), goal, stage, float(doc.get("tie") or 0.0),
                   unit, float(doc.get("margin") or 0.0), keep=float(keep) if keep is not None else None,
                   above=float(doc.get("above") or 0.0), balance=bool(doc.get("balance")))

    def to_doc(self) -> dict[str, Any]:
        """The document form, `from_doc`'s inverse (what the record keeps)."""
        return {k: v for k, v in {"metric": self.metric, "direction": self.direction, "goal": self.goal,
                                  "stage": self.stage, "tie": self.tie or None, "unit": self.unit or None,
                                  "margin": self.margin or None, "keep": self.keep,
                                  "above": self.above if self.keep is not None and self.above else None,
                                  "balance": True if self.balance else None}.items()
                if v is not None}

    def value(self, metrics: dict[str, Any] | None) -> float | None:
        v = (metrics or {}).get(self.metric)
        try:
            f = float(v)
        except (TypeError, ValueError):
            return None
        return f if math.isfinite(f) else None

    def signed(self, metrics: dict[str, Any] | None) -> float:
        """The value as a cost (lower is better), for frontier and knee code; nan when absent."""
        v = self.value(metrics)
        if v is None:
            return float("nan")
        return -v if self.direction == "maximize" else v

    def goal_at(self, stage: str | None = None, stages: Sequence[str] | None = None) -> float | None:
        """The goal a number measured on `stage` must clear (D522).

        The goal itself on the objective's stage, a deeper one, or when either is unknown; on
        a shallower stage the goal plus the margin (800 MHz routed with 3% is 824 placed). A
        stage that is not the objective's counts as shallower unless `stages` (the chain, in
        order) says otherwise."""
        if self.goal is None:
            return None
        margin = self.margin_at(stage)
        if not margin or self.stage is None or stage is None or stage == self.stage:
            return self.goal
        if stages and self.stage in stages and stage in stages and list(stages).index(stage) > list(stages).index(self.stage):
            return self.goal
        return self.goal * (1.0 + margin) if self.direction == "maximize" else self.goal / (1.0 + margin)

    def margin_at(self, stage: str | None) -> float:
        """The margin in force on `stage`: the document's (a floor), or the record's measured
        one for that stage when larger."""
        measured = dict(self.margins).get(stage or "", 0.0)
        return max(float(self.margin), float(measured))

    def meets(self, metrics: dict[str, Any] | None, stage: str | None = None,
              stages: Sequence[str] | None = None) -> bool:
        """Whether these numbers reach the goal -- the goal at `stage` when they were
        measured on a stage shallower than the objective's."""
        v = self.value(metrics)
        goal = self.goal_at(stage, stages)
        if goal is None or v is None:
            return False
        return v >= goal if self.direction == "maximize" else v <= goal

    def compare(self, a: dict[str, Any] | None, b: dict[str, Any] | None) -> int:
        """1 when `a` is better than `b` on this metric by more than the tie band, -1 when
        worse, 0 within the band or when either is unmeasured."""
        va, vb = self.value(a), self.value(b)
        if va is None or vb is None:
            return 0
        if self.direction == "minimize":
            va, vb = -va, -vb
        band = abs(vb) * self.tie
        if va > vb + band:
            return 1
        if va < vb - band:
            return -1
        return 0

    def resolved(self, pool: Iterable[Any]) -> "Objective":
        """With `keep`, the goal it means over this pool: the best value's gain over `above`,
        times `keep`. Unchanged otherwise, or when nothing in the pool is measured."""
        vals = [v for p in pool if (v := self.value(getattr(p, "metrics", p))) is not None]
        if self.keep is None or not vals:
            return self
        best = max(vals) if self.direction == "maximize" else min(vals)
        return replace(self, goal=self.above + self.keep * (best - self.above), keep=None)

    def describe(self) -> str:
        if self.keep is not None:
            return (f"{self.metric} within {self.keep:.0%} of the best's gain over {self.above:g}")
        named = self.stage if self.stage != "deepest" else ""     # the default goes unsaid (D628)
        where = (f" ({named or 'deepest'}" + (f", +{self.margin:.0%} on a shallower stage" if self.margin else "") + ")"
                 if named or self.margin else "")
        if self.goal is not None:
            return f"{self.metric} at {'least' if self.direction == 'maximize' else 'most'} {self.goal:g}{where}"
        return f"{'most' if self.direction == 'maximize' else 'least'} {self.metric}{where}"

    def shortfall(self, metrics: dict[str, Any] | None, stage: str | None = None,
                  stages: Sequence[str] | None = None) -> float:
        """How far these numbers are from the goal, relative to it: 0 when met, inf unmeasured."""
        v, goal = self.value(metrics), self.goal_at(stage, stages)
        if goal is None:
            return 0.0
        if v is None:
            return float("inf")
        gap = goal - v if self.direction == "maximize" else v - goal
        return max(0.0, gap) / max(abs(goal), 1e-12)

    def said(self, stage: str | None = None, stages: Sequence[str] | None = None) -> str:
        """The limit in force on `stage`, short: `fmax_mhz >= 824 (the 800 asked for, plus the
        margin on the confirm stage)`."""
        goal = self.goal_at(stage, stages)
        return f"{self.metric} {'>=' if self.direction == 'maximize' else '<='} {goal:g}{self._margin_said(stage, stages)}"

    def _margin_said(self, stage: str | None, stages: Sequence[str] | None) -> str:
        if self.goal_at(stage, stages) == self.goal:
            return ""
        return f" (the {self.goal:g} asked for, plus the margin on the {stage} stage)"


class Objectives(tuple):
    """An ordered vector of `Objective`: the ones with a goal are limits, the rest decide among
    the designs meeting every limit, in order (the `balance` ones together, as a knee)."""

    def __new__(cls, items: Iterable[Objective] = ()) -> "Objectives":
        return super().__new__(cls, tuple(items))

    @classmethod
    def from_doc(cls, docs: Any) -> "Objectives":
        return cls(Objective.from_doc(d, i) for i, d in enumerate(docs or ()))

    @property
    def limits(self) -> tuple[Objective, ...]:
        """Every objective with a goal: each must hold."""
        return tuple(o for o in self if o.goal is not None)

    @property
    def goal(self) -> Objective | None:
        """The first limit, for what speaks of one goal (the report's stage, a plot's line)."""
        return next(iter(self.limits), None)

    @property
    def balance(self) -> tuple[Objective, ...]:
        return tuple(o for o in self if o.balance)

    def missed(self, metrics: dict[str, Any] | None, stage: str | None = None,
               stages: Sequence[str] | None = None) -> list[Objective]:
        """The limits these numbers miss (an unmeasured one is missed)."""
        return [o for o in self.limits if not o.meets(metrics, stage, stages)]

    @staticmethod
    def _judged(o: Objective, pool: list[Any], stage: str | None, stages: Sequence[str] | None) -> bool:
        """Whether a pool on `stage` can judge limit `o`: it is the limit's own stage, or some
        design in it measured the metric (a failed measurement still misses)."""
        own = o.stage is None or stage is None or o.stage == stage or (
            o.stage == "deepest" and (not stages or stage == list(stages)[-1]))
        return own or any(o.value(getattr(p, "metrics", None)) is not None for p in pool)

    def _steps(self) -> list[Objective | tuple[Objective, ...]]:
        """What orders the designs past the limits: each goal-less objective in written order,
        the balance ones as one group where the first of them stands."""
        steps: list[Objective | tuple[Objective, ...]] = []
        for o in self:
            if o.goal is not None:
                continue
            if o.balance:
                if self.balance[0] is o:
                    steps.append(self.balance)
                continue
            steps.append(o)
        return steps

    def describe(self) -> str:
        """In plain words: "fmax_mhz at least 1000, area_um2 at most 80, then least power_w"."""
        limits = [o.describe() for o in self if o.goal is not None or o.keep is not None]
        rest = []
        for step in self._steps():
            if isinstance(step, tuple):
                names = [o.metric for o in step]
                rest.append("the balance of " + (", ".join(names[:-1]) + " and " + names[-1] if len(names) > 1 else names[0]))
            elif step.keep is None:
                rest.append(step.describe())
        text = ", ".join(limits)
        for part in rest:
            text = f"{text}, then {part}" if text else part
        return text

    # ---- the one rule
    def better(self, new: dict[str, Any] | None, old: dict[str, Any] | None,
               stage: str | None = None, stages: Sequence[str] | None = None) -> bool:
        """Whether `new` stands over `old`. Nothing measured on the old side: yes. Meeting every
        limit beats missing one; both meeting, the goal-less objectives decide (an exact tie
        keeps the new one). Both missing: fewer limits missed, then the nearer -- on the one
        limit both miss by more than its tie band, else by the smaller total relative
        shortfall -- then the goal-less objectives, strictly. No limits: the goal-less
        objectives in order, each by more than its tie band, strictly. `stage` is where both
        were measured (a goal there carries the margin)."""
        if not self:
            return True
        first = self[0]
        if first.value(old) is None:
            return True
        if first.value(new) is None:
            return False
        if self.limits:
            n_miss, o_miss = self.missed(new, stage, stages), self.missed(old, stage, stages)
            if len(n_miss) != len(o_miss):
                return len(n_miss) < len(o_miss)
            if not n_miss:
                return self._ordered(new, old, strict=False)
            if len(n_miss) == 1 and n_miss[0] is o_miss[0]:
                c = n_miss[0].compare(new, old)
            else:
                sn = sum(o.shortfall(new, stage, stages) for o in n_miss)
                so = sum(o.shortfall(old, stage, stages) for o in o_miss)
                c = (sn < so) - (sn > so)
            if c != 0:
                return c > 0
        return self._ordered(new, old, strict=True)

    def _ordered(self, new, old, strict: bool) -> bool:
        """The goal-less objectives in written order. Two designs alone have no knee, so the
        balance ones are read in order too: a design that dominates on them still wins."""
        for o in self:
            if o.goal is None:
                c = o.compare(new, old)
                if c != 0:
                    return c > 0
        return not strict

    def best_of(self, rows: Iterable[tuple[Any, dict[str, Any] | None]], stage: str | None = None,
                stages: Sequence[str] | None = None) -> Any | None:
        """The tournament of `better` over (thing, its numbers): a thing never measured cannot
        win over one that was; a dead heat keeps the later one."""
        best: tuple[Any, dict[str, Any]] | None = None
        for thing, m in rows:
            if not m or (self and self[0].value(m) is None):
                continue
            if best is None or self.better(m, best[1], stage, stages) or self._same(m, best[1]):
                best = (thing, m)
        return best[0] if best is not None else None

    def _same(self, a: dict[str, Any], b: dict[str, Any]) -> bool:
        return all(o.value(a) == o.value(b) for o in self)

    # ---- what the loop derives
    def frontier_axes(self) -> tuple[Callable[[Any], float], Callable[[Any], float]] | None:
        """(better, cost) over `Scored` from the first two objectives, for the frontier."""
        if len(self) < 2:
            return None
        first, second = self[0], self[1]

        def better(p: Any) -> float:
            return -first.signed(p.metrics)

        def cost(p: Any) -> float:
            return second.signed(p.metrics)

        return better, cost

    def decide(self, pool: list[Any], stages: Sequence[str] | None = None) -> tuple[Any | None, str]:
        """The pick and why. Among the designs meeting every limit: the knee of the balance
        objectives (the other goal-less ones breaking ties), else the goal-less objectives in
        order, else the first limit's best. None meets every limit: the closest -- fewest
        limits missed, then the smallest relative shortfall. A `keep` goal is resolved over the
        pool first. The pool is one stage's; a goal there carries the margin when that stage
        is shallower than the objective's."""
        if not pool:
            return None, "nothing measured"
        if not self:
            return pool[0], "the only kind of answer this problem has"
        if any(o.keep is not None for o in self):
            resolved = Objectives(o.resolved(pool) for o in self)
            if resolved != self:
                return resolved.decide(pool, stages)
        stage = getattr(pool[0], "stage", None)
        waiting = [o for o in self.limits if not self._judged(o, pool, stage, stages)]
        if waiting:
            # D878: a limit on another stage's metric, which nothing in this stage's pool
            # measures, misses for every design alike: it waits for its stage instead
            return Objectives(o for o in self if o not in waiting).decide(pool, stages)
        limits = self.limits
        at = " and ".join(o.said(stage, stages) for o in limits)
        meeting = [p for p in pool if not self.missed(p.metrics, getattr(p, "stage", None), stages)]
        if limits and not meeting:
            def gap(p: Any) -> tuple[int, float]:
                miss = self.missed(p.metrics, getattr(p, "stage", None), stages)
                return len(miss), sum(o.shortfall(p.metrics, getattr(p, "stage", None), stages) for o in miss)

            pick = min(pool, key=gap)
            if len(limits) == 1:
                o = limits[0]
                return pick, (f"nothing reaches {o.metric} {o.goal_at(stage, stages):g}{o._margin_said(stage, stages)}; "
                              f"the {'most' if o.direction == 'maximize' else 'least'} {o.metric}")
            short = ", ".join(f"{o.metric} ({_num(o.value(pick.metrics))} for {o.goal_at(stage, stages):g})"
                              for o in self.missed(pick.metrics, getattr(pick, "stage", None), stages))
            return pick, f"nothing meets every limit ({at}); the closest misses {short}"
        steps = self._steps()
        free = [s for s in steps if not isinstance(s, tuple)]
        if self.balance:
            from flux_frontier import knee_ranked

            ties = sorted(meeting, key=lambda p: tuple(_cost(o, p) for o in free))    # stable: the knee's ties
            ranked = knee_ranked(ties, [(lambda p, o=o: o.signed(p.metrics)) for o in self.balance])
            why = "the knee of " + " / ".join(o.metric for o in self.balance)
            return (ranked[0] if ranked else meeting[0]), why + (f" at {at}" if limits else "")
        if not free:
            # only limits: the first one's best
            o = limits[0]
            pick = min(meeting, key=lambda p: _cost(o, p))
            words = f"the {'largest' if o.direction == 'maximize' else 'smallest'} {o.metric}"
            return pick, words + (f" at {at}" if len(limits) > 1 else "")
        cands, used = list(meeting), []
        for o in free:
            used.append(o)
            best = min(_cost(o, p) for p in cands)
            band = abs(best) * o.tie if math.isfinite(best) else 0.0
            cands = [p for p in cands if _cost(o, p) <= best + band]
            if len(cands) <= 1:
                break
        pick = min(cands, key=lambda p: tuple(_cost(o, p) for o in used))
        head = used[0]
        if limits:
            words = f"the {'least' if head.direction == 'minimize' else 'most'} {head.metric} at {at}"
        else:
            words = f"the {'smallest' if head.direction == 'minimize' else 'largest'} {head.metric}"
        return pick, words + "".join(f", then {o.describe()}" for o in used[1:])

    def good_enough(self, metrics: dict[str, Any] | None, stage: str | None = None,
                    stages: Sequence[str] | None = None) -> str | None:
        """Whether the vector is good enough by these numbers, said in words; None when there is
        no limit, one is not met, or an objective without a goal remains (a goal met with area
        still to shrink is a floor reached, not a search finished, D543). On a shallower stage
        a goal carries the margin, and the words say so."""
        limits = self.limits
        if not limits or self.missed(metrics, stage, stages) or any(o.goal is None for o in self):
            return None
        said = []
        for g in limits:
            goal = g.goal_at(stage, stages)
            said.append(f"{g.metric} is {g.value(metrics):g}, the {goal:g} asked for"
                        + (f" on the {stage} stage ({g.goal:g} {g.stage or ''} with a {g.margin_at(stage):.0%} margin)".replace("  ", " ") if goal != g.goal else ""))
        return "; ".join(said)


def _cost(o: Objective, p: Any) -> float:
    """The objective's cost for sorting: unmeasured sorts last."""
    v = o.signed(getattr(p, "metrics", p))
    return v if math.isfinite(v) else float("inf")


def _num(v: float | None) -> str:
    return "unmeasured" if v is None else f"{v:g}"
