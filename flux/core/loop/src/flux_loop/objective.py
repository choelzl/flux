"""THE OBJECTIVE AS A VECTOR (docs/decisions.md D511): what a campaign is for, ordered, and the
one rule that says which of two measured designs is better.

    objectives:
      - {metric: fmax_mhz, direction: maximize, goal: 800, stage: confirm, tie: 0.03}
      - {metric: area_um2, direction: minimize}
      - {metric: power_w,  direction: minimize}

The first objective is the GOAL when it names one; the rest break ties in order. `better`
reads: a design at the goal beats one below it; two at the goal are ordered by the next
objectives; two below it by the first objective when they differ by more than its tie band,
else by the next objectives. The frontier's axes, the decision, the early stop and which of
a part's admitted designs stands at a reload all derive from this one vector -- before D511
the NLU carried each as its own method (`_better`, `FMAX_TIE`, `prefer_admitted`, `decide`,
`frontier_axes`, `good_enough`) and the task document carried a second, weaker `Objective`.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Sequence

__all__ = ["Objective", "Objectives"]

_DIRECTIONS = ("maximize", "minimize")


@dataclass(frozen=True)
class Objective:
    metric: str
    direction: str = "maximize"
    goal: float | None = None        # a floor (maximize) or a ceiling (minimize) the design must reach
    stage: str | None = None         # whose numbers count; None = wherever the loop measured it
    tie: float = 0.0                 # two values within this RELATIVE band are equal
    unit: str = ""                   # how a number is said ("MHz", "um2"); the metric's name when empty
    margin: float = 0.0              # D522: a stage SHALLOWER than `stage` must clear the goal by this fraction
    #: D562: the margin the RECORD measured per shallower stage -- (stage, margin) pairs from the
    #: calibration ratios between it and the objective's stage; the document's `margin` is the floor
    margins: tuple[tuple[str, float], ...] = ()

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

    @classmethod
    def from_doc(cls, doc: Any, index: int = 0) -> "Objective":
        """`"fmax_mhz"`, or `{metric, direction?, goal?, stage?, tie?}`; `goal` may be a number
        or `">= 800"` / `"<= 0.5"` (the comparator names the direction)."""
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
        return cls(doc["metric"], str(direction or "maximize"), goal, doc.get("stage"), float(doc.get("tie") or 0.0),
                   str(doc.get("unit") or ""), float(doc.get("margin") or 0.0))

    def to_doc(self) -> dict[str, Any]:
        """The document form, `from_doc`'s inverse (what the record keeps, D512)."""
        return {k: v for k, v in {"metric": self.metric, "direction": self.direction, "goal": self.goal,
                                  "stage": self.stage, "tie": self.tie or None, "unit": self.unit or None,
                                  "margin": self.margin or None}.items() if v is not None}

    def value(self, metrics: dict[str, Any] | None) -> float | None:
        v = (metrics or {}).get(self.metric)
        try:
            f = float(v)
        except (TypeError, ValueError):
            return None
        return f if math.isfinite(f) else None

    def signed(self, metrics: dict[str, Any] | None) -> float:
        """The value as a COST (lower is better), for frontier and knee code; nan when absent."""
        v = self.value(metrics)
        if v is None:
            return float("nan")
        return -v if self.direction == "maximize" else v

    def goal_at(self, stage: str | None = None, stages: Sequence[str] | None = None) -> float | None:
        """The goal a number measured on `stage` must clear (D522): the goal itself on the
        objective's own stage, deeper than it, or when either stage is unknown; on a SHALLOWER
        stage the goal plus the margin -- 800 MHz routed with a 3% margin is 824 placed, so a
        design a model or a placement over-estimates is not admitted at the edge. A stage that
        is not the objective's counts as shallower unless `stages` (the chain, in order) says
        otherwise."""
        if self.goal is None:
            return None
        margin = self.margin_at(stage)
        if not margin or self.stage is None or stage is None or stage == self.stage:
            return self.goal
        if stages and self.stage in stages and stage in stages and list(stages).index(stage) > list(stages).index(self.stage):
            return self.goal
        return self.goal * (1.0 + margin) if self.direction == "maximize" else self.goal / (1.0 + margin)

    def margin_at(self, stage: str | None) -> float:
        """The margin in force on `stage`: the document's, or the record's measured one for
        that stage when it is larger (D562: the document's margin is a floor, never the
        estimate)."""
        measured = dict(self.margins).get(stage or "", 0.0)
        return max(float(self.margin), float(measured))

    def meets(self, metrics: dict[str, Any] | None, stage: str | None = None,
              stages: Sequence[str] | None = None) -> bool:
        """Whether these numbers reach the goal -- the goal at `stage` (D522) when the numbers
        were measured on a stage shallower than the objective's."""
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

    def describe(self) -> str:
        where = f" ({self.stage}" + (f", +{self.margin:.0%} on a shallower stage" if self.margin else "") + ")" if self.stage else ""
        if self.goal is not None:
            return f"{self.metric} {'>=' if self.direction == 'maximize' else '<='} {self.goal:g}{where}"
        return f"{'most' if self.direction == 'maximize' else 'least'} {self.metric}{where}"


class Objectives(tuple):
    """An ordered vector of `Objective`; the first is the goal when it names one."""

    def __new__(cls, items: Iterable[Objective] = ()) -> "Objectives":
        return super().__new__(cls, tuple(items))

    @classmethod
    def from_doc(cls, docs: Any) -> "Objectives":
        return cls(Objective.from_doc(d, i) for i, d in enumerate(docs or ()))

    @property
    def goal(self) -> Objective | None:
        return self[0] if self and self[0].goal is not None else None

    def describe(self) -> str:
        if not self:
            return ""
        parts = [o.describe() for o in self]
        return parts[0] + ("".join(f", then {p}" for p in parts[1:]) if len(parts) > 1 else "")

    # ---- the one rule
    def better(self, new: dict[str, Any] | None, old: dict[str, Any] | None,
               stage: str | None = None, stages: Sequence[str] | None = None) -> bool:
        """Whether `new` stands over `old`. Nothing measured on the old side: yes. A goal:
        at it beats below it; both at it, the NEXT objectives decide (an exact tie keeps the
        new one -- it is not worse). Below it, or no goal: the first objective by more than
        its tie band; within the band the next objectives, strictly. `stage` is where both
        were measured (D522: the goal there carries the margin)."""
        if not self:
            return True
        first = self[0]
        if first.value(old) is None:
            return True
        if first.value(new) is None:
            return False
        if first.goal is not None:
            n_ok, o_ok = first.meets(new, stage, stages), first.meets(old, stage, stages)
            if n_ok and not o_ok:
                return True
            if o_ok and not n_ok:
                return False
            if n_ok and o_ok:
                return self._rest_not_worse(new, old, self[1:])
        c = first.compare(new, old)
        if c != 0:
            return c > 0
        return self._rest_strictly_better(new, old, self[1:])

    @staticmethod
    def _rest_not_worse(new, old, rest: Sequence[Objective]) -> bool:
        for o in rest:
            c = o.compare(new, old)
            if c != 0:
                return c > 0
        return True

    @staticmethod
    def _rest_strictly_better(new, old, rest: Sequence[Objective]) -> bool:
        for o in rest:
            c = o.compare(new, old)
            if c != 0:
                return c > 0
        return False

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
        """The pick and why: with a goal, the least on the next objective among those at the
        goal (the best on the first when nothing reaches it); without, the knee over every
        objective; with one objective, its best. The pool is ONE stage's; the goal there
        carries the margin when that stage is shallower than the objective's (D522)."""
        if not pool:
            return None, "nothing measured"
        if not self:
            return pool[0], "the only kind of answer this problem has"
        first = self[0]
        if first.goal is not None and len(self) >= 2:
            stage = getattr(pool[0], "stage", None)
            goal = first.goal_at(stage, stages)
            at_goal = [p for p in pool if first.meets(p.metrics, getattr(p, "stage", None), stages)]
            margin = f" (the {first.goal:g} asked for, plus the margin on the {stage} stage)" if goal != first.goal else ""
            if at_goal:
                pick = min(at_goal, key=lambda p: self[1].signed(p.metrics))
                return pick, f"the least {self[1].metric} at {first.metric} {'>=' if first.direction == 'maximize' else '<='} {goal:g}{margin}"
            pick = min(pool, key=lambda p: first.signed(p.metrics))
            return pick, f"nothing reaches {first.metric} {goal:g}{margin}; the {'most' if first.direction == 'maximize' else 'least'} {first.metric}"
        if len(self) == 1:
            pick = min(pool, key=lambda p: first.signed(p.metrics))
            return pick, f"the {'largest' if first.direction == 'maximize' else 'smallest'} {first.metric}"
        from flux_frontier import knee_ranked

        ranked = knee_ranked(pool, [(lambda p, o=o: o.signed(p.metrics)) for o in self])
        return (ranked[0] if ranked else pool[0]), "the knee of " + " / ".join(o.metric for o in self)

    def good_enough(self, metrics: dict[str, Any] | None, stage: str | None = None,
                    stages: Sequence[str] | None = None) -> str | None:
        """Whether the vector is GOOD ENOUGH by these numbers, said in words; None when there is
        no goal, it is not met, or an objective without a goal remains -- a goal met with area
        or power still to shrink is a floor reached, not a search finished (D543: the macarray
        stopped at the first PE that made 1 GHz where the ask was the smallest one that does; a
        campaign keeps tuning until it is stopped). On a stage shallower than the goal's, the
        goal there is the margin's (D522), and the words say so."""
        g = self.goal
        if g is None or not g.meets(metrics, stage, stages):
            return None
        if any(o.goal is None for o in self):
            return None                              # more to improve: the vector's next objectives
        goal = g.goal_at(stage, stages)
        return (f"{g.metric} is {g.value(metrics):g}, the {goal:g} asked for"
                + (f" on the {stage} stage ({g.goal:g} {g.stage or ''} with a {g.margin_at(stage):.0%} margin)".replace("  ", " ") if goal != g.goal else ""))
