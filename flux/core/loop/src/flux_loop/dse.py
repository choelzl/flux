"""The DSE box (D553): search policies over a declared space, as selectable orchestrators.

A problem declares its space -- `space: {multiplier: [behavioral, booth4], pipeline: [0, 1, 2]}`
in the document, or a world's `space(state)` -- each knob's choices in a MEANINGFUL ORDER, so
that "a neighbour" means "a little different". A policy proposes POINTS of it as
`Candidate(knobs=point)`; the problem's `instantiate(points, state)` turns them into what its
gate can build and judge (the default keeps them as knobs, a world that generates RTL from a
point generates it there), and the loop measures each batch on the first stage and hands the
numbers back to the policy (D446) before it chooses the next batch. Points already measured
this campaign are never proposed again.

    sweep       every point, in batches: the exhaustive answer, the one that proves what the
                others only approach
    montecarlo  uniform random points, never the same one twice, `samples` of them, from `seed`
    anneal      one neighbour of the incumbent per step, accepted when better or when the
                temperature says so (Metropolis, geometric cooling)
    gradient    coordinate descent: every one-knob neighbour of the incumbent at once, the
                best becomes the incumbent, until none improves
    genetic     a population from `seed`, the better half bred by crossover and one-knob
                mutation, for `generations`
    llm         the model reads the space, the objective and what was measured, and names
                the next points (`model` in the registry; the document says `dse: llm`)
    pareto      a Pareto-UCT tree over the first two objectives: each wave expands the
                measured design whose branch has been buying hypervolume, half the wave by a
                nearest-neighbour estimate, half nearest-first (D368, general since D583)
    control     one measurement: the starting design with only the named knobs of the
                incumbent -- what the search's other choices bought (D583)

PHASES (D583): `flow: {dse: [...]}` is a list of walks run in order, each starting from the
incumbent the last one ended on. Every policy takes the phase fields: `name` (its label in
the log, the record and the lessons), `knobs`/`hold` (the knobs it moves, or the ones it
keeps at the incumbent's), `metric`/`direction` (its own objective), `floor` (what a design
must hold to be admitted -- `{metric, at}`, or `{metric, keep, above}`: keep this fraction of
the start's gain over `above`; a design under it is REFUSED, never ranked) and `margin` (an
improvement smaller than this is none). The gradient also takes `wave` (points a step, taken
round-robin across knobs), `patience` (flat steps before it ends), `budget` (points it may
measure) and `reach` (`adjacent` choices or `any` other choice of a knob).

A world with a structured space fills two hooks: `seeds(state)` -- the points measured before
any walk -- and `moves(point, phase, state)` -- the legal moves from a point for a phase, in
the order worth measuring (None: the space's own neighbours).

The objective a policy reads is the document's first (D511): its metric and direction. A
policy is also the rules orchestrator for a document with parts, so the two can coexist.
"""

from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass, field, fields
from fnmatch import fnmatchcase
from typing import Any, Iterator

from .roles import Rules, register

#: A proposed point not yet made into a design (D739): `instantiate_taken` makes it.
POINT = "_point"


def instantiate_taken(problem: Any, state: Any, cands: list[Candidate]) -> list[Candidate]:
    """The designs for the candidates a pass takes: proposed points made by the problem's
    `instantiate` (all at once, so a world that generates a batch still does), the rest as
    they are. A point the problem could not make is dropped, as before."""
    points = [c for c in cands if c.meta.get(POINT)]
    if not points:
        return list(cands)
    made = {_key(c.knobs): c for c in problem.instantiate([dict(c.knobs) for c in points], state)}
    out = []
    for c in cands:
        if not c.meta.get(POINT):
            out.append(c)
            continue
        m = made.get(_key(c.knobs))
        if m is not None:
            m.meta.setdefault("strategy", c.meta.get("strategy"))
            out.append(m)
    return out
from .types import Candidate, Scored

__all__ = ["Anneal", "CommandSearch", "Control", "Genetic", "Gradient", "ModelSearch", "MonteCarlo", "Pareto", "Phases", "Policy",
           "Sweep", "neighbour", "points", "point_of", "validate_phase"]


def points(space: dict[str, list]) -> list[dict[str, Any]]:
    """The grid, in the declared order: the first knob slowest."""
    if not space:
        return []
    out: list[dict[str, Any]] = [{}]
    for k, vals in space.items():
        out = [{**p, k: v} for p in out for v in vals]
    return out


def neighbour(space: dict[str, list], point: dict[str, Any], rng: random.Random) -> dict[str, Any]:
    """One knob moved to an adjacent choice; the ends of a range stay inside it."""
    knobs = [k for k, vals in space.items() if len(vals) > 1]
    if not knobs:
        return dict(point)
    k = rng.choice(knobs)
    vals = list(space[k])
    i = vals.index(point[k]) if point[k] in vals else 0
    j = i + rng.choice((-1, 1))
    if not 0 <= j < len(vals):
        j = i - (j - i)
    return {**point, k: vals[j]}


def neighbours(space: dict[str, list], point: dict[str, Any]) -> list[dict[str, Any]]:
    """Every one-knob neighbour, in knob order, the lower choice first."""
    out = []
    for k, vals in space.items():
        vals = list(vals)
        i = vals.index(point[k]) if point[k] in vals else 0
        for j in (i - 1, i + 1):
            if 0 <= j < len(vals):
                out.append({**point, k: vals[j]})
    return out


def _seeded(state: Any) -> bool:
    return any(d.get("label") == "seed" and d.get("points") for d in state.dse)


def first(space: dict[str, list]) -> dict[str, Any]:
    """The grid's first point without building the grid (a space of eleven knobs is billions)."""
    return {k: vals[0] for k, vals in space.items() if vals}


def size(space: dict[str, list]) -> int:
    n = 1
    for vals in space.values():
        n *= max(1, len(vals))
    return n


def around(space: dict[str, list], point: dict[str, Any], movable: list[str], reach: str = "adjacent") -> list[dict[str, Any]]:
    """The one-knob moves of the `movable` knobs, round-robin across knobs so the first k
    touch k different knobs (a wave that takes the head of a knob-ordered list spends itself
    on one axis), each knob's nearest choices first; `reach: any` offers every other choice."""
    per: list[list[dict[str, Any]]] = []
    for k in movable:
        vals = list(space.get(k) or ())
        if point.get(k) not in vals:
            continue
        i = vals.index(point[k])
        order = sorted((j for j in range(len(vals)) if j != i), key=lambda j: (abs(j - i), -j))
        if reach != "any":
            order = [j for j in order if abs(j - i) == 1]
        per.append([{**point, k: vals[j]} for j in order])
    out: list[dict[str, Any]] = []
    while any(per):
        for bucket in per:
            if bucket:
                out.append(bucket.pop(0))
    return out


def canonical(space: dict[str, list], when: dict[str, dict[str, list]], point: dict[str, Any],
              home: dict[str, Any] | None = None) -> dict[str, Any]:
    """The point with every knob whose `when:` does not hold at its home value (the first
    seed's, else its first choice), so two points that differ only in a knob that does nothing
    there are one point, and a knob that starts to matter starts from home."""
    out = dict(point)
    for k, cond in when.items():
        if k in out and space.get(k) and not all(out.get(c) in allowed for c, allowed in cond.items()):
            out[k] = (home or {}).get(k, space[k][0])
    return out


def _matches(knob: str, patterns: tuple) -> bool:
    return any(fnmatchcase(knob, str(p)) for p in patterns)


def _key(point: dict[str, Any]) -> tuple:
    return tuple(sorted((str(k), repr(v)) for k, v in point.items()))


def point_of(cand: Candidate) -> dict[str, Any]:
    """The point of the space a candidate is: what the world said (`meta["point"]`) when its
    candidates carry knobs of their own shape, else its knobs."""
    return dict(cand.meta.get("point") or cand.knobs or {})


@dataclass
class Policy(Rules):
    """What every policy shares: the space from the problem, the points measured already,
    the objective it reads (the document's first, or the phase's own), the phase fields
    (`knobs`/`hold`, `floor`, `margin`), the seeds, the batch it hands to the gate."""

    name: str = "policy"
    label: str = ""                       # the phase's name (log, record, lessons)
    knobs: tuple = ()                     # the knobs this walk moves; empty = every knob
    hold: tuple = ()                      # ... or the ones it keeps at the incumbent's
    metric: str = ""                      # the phase's own objective (with `direction`)
    direction: str = ""
    floor: Any = None                     # {metric, at} | {metric, keep, above}: admission
    margin: float = 0.0                   # an improvement smaller than this is none
    start: Any = field(default=None, repr=False)     # the incumbent a phase starts from (Phases)

    def search(self, problem: Any, state: Any) -> Iterator[list[Candidate]] | None:
        space = {k: list(v) for k, v in dict(problem.space(state) or {}).items() if v}
        if not space:
            state.say(f"  {self.name}: no `flow.dse.space` is declared and the world names none; nothing to search")
            return None
        self._space, self._when = space, dict(getattr(getattr(problem, "task", None), "when", None) or {})
        seeds = [dict(p) for p in (getattr(problem, "seeds", None) or (lambda _s: []))(state) or []]
        self._home = dict(seeds[0]) if seeds else {}
        return self._seeded(problem, state, space, seeds)

    def _seeded(self, problem, state, space, seeds):
        """The seeds first (D583), once, then the walk. What is measured is read when the walk
        starts, so a resumed record's points (D682) count as seen."""
        seen = {_key(point_of(s.candidate)) for s in state.scored if point_of(s.candidate)}
        state.dse.append({"label": "seed", "points": seeds})
        fresh = [p for p in seeds if _key(p) not in seen]
        if fresh:
            state.say(f"  seeds: {len(fresh)} point(s) measured before the walk")
            yield self.batch(problem, state, fresh, seen, label="seed")
        yield from self.walk(problem, state, space, seen)

    def walk(self, problem: Any, state: Any, space: dict[str, list], seen: set) -> Iterator[list[Candidate]]:
        raise NotImplementedError

    # ---- what the subclasses use
    @property
    def tag(self) -> str:
        return self.label or self.name

    def movable(self, space: dict[str, list]) -> list[str]:
        return [k for k in space if (not self.knobs or _matches(k, self.knobs)) and not _matches(k, self.hold)]

    def canon(self, point: dict[str, Any]) -> dict[str, Any]:
        return canonical(getattr(self, "_space", {}), getattr(self, "_when", {}), point, getattr(self, "_home", {}))

    def batch(self, problem: Any, state: Any, pts: list[dict[str, Any]], seen: set,
              label: str | None = None) -> list[Candidate]:
        fresh = []
        for p in map(self.canon, pts):
            k = _key(p)
            if k in seen:
                continue
            seen.add(k)
            fresh.append(p)
        # D739: points, not designs -- a pass makes the ones it takes (`instantiate_taken`), so a
        # sweep's 6 designs are written over its 6 passes, not all in the first
        from .document import _point_name

        return [Candidate(name=_point_name(p), knobs=dict(p),
                          meta={"strategy": label or self.tag, POINT: True}) for p in fresh]

    def objective(self, problem: Any, state: Any) -> tuple[str, float] | None:
        """(metric, sign): the value times sign is what a policy MINIMISES."""
        objs = list(problem.objectives() or [])
        if self.metric:
            direction = self.direction or next((o.direction for o in objs if o.metric == self.metric), "")
            if direction not in ("minimize", "maximize"):
                state.say(f"  {self.tag}: `{self.metric}` needs a `direction` (minimize or maximize)")
                return None
            return self.metric, (1.0 if direction == "minimize" else -1.0)
        if not objs:
            state.say(f"  {self.tag}: the document declares no objective; the policy cannot rank")
            return None
        return objs[0].metric, (1.0 if objs[0].direction == "minimize" else -1.0)

    def metrics_at(self, state: Any, point: dict[str, Any] | None) -> dict[str, float]:
        if point is None:
            return {}
        k = _key(point)
        hit = [s for s in state.scored if _key(point_of(s.candidate)) == k]
        return dict(hit[-1].metrics) if hit else {}

    def floor_value(self, state: Any) -> tuple[str, float] | None:
        """The floor as (metric, minimum), resolved against the phase's start."""
        f = self.floor
        if not f:
            return None
        if "at" in f:
            return str(f["metric"]), float(f["at"])
        base = self.metrics_at(state, self.start).get(str(f["metric"]))
        if base is None:
            return None
        above = float(f.get("above", 0.0))
        return str(f["metric"]), above + float(f["keep"]) * (float(base) - above)

    def admitted(self, state: Any, scored: list[Scored] | None) -> list[Scored]:
        """The scored the floor admits; the others are refused, with the reason, once."""
        fl = self.floor_value(state)
        out = []
        for s in scored or []:
            v = s.metrics.get(fl[0]) if fl else None
            if fl and v is not None and float(v) < fl[1]:
                state.refused.append((s.candidate.name, f"{self.tag}: below the floor, {fl[0]} {float(v):.6g} < {fl[1]:.6g}"))
                continue
            out.append(s)
        return out

    def values(self, scored: list[Scored] | None, metric: str, sign: float) -> list[tuple[float, dict[str, Any]]]:
        out = []
        for s in scored or []:
            p = point_of(s.candidate)
            v = s.metrics.get(metric)
            if p and v is not None:
                out.append((sign * float(v), p))
        return out

    def incumbent(self, state: Any, metric: str, sign: float) -> tuple[float, dict[str, Any]] | None:
        if self.start is not None:
            v = self.metrics_at(state, self.start).get(metric)
            return (sign * float(v), dict(self.start)) if v is not None else None
        best = self.values(self.admitted_quiet(state, list(state.scored)), metric, sign)
        return min(best, key=lambda t: t[0]) if best else None

    def admitted_quiet(self, state: Any, scored: list[Scored]) -> list[Scored]:
        fl = self.floor_value(state)
        if not fl:
            return scored
        return [s for s in scored if (v := s.metrics.get(fl[0])) is None or float(v) >= fl[1]]

    def record(self, state: Any, metric: str, sign: float, start: tuple | None, end: tuple | None,
               spent: int, stopped: str) -> None:
        """What a walk did, for the next phase, the world and the lessons (D583)."""
        state.dse.append({"label": self.tag, "policy": self.name, "metric": metric,
                          "start": start[1] if start else None, "start_value": sign * start[0] if start else None,
                          "end": end[1] if end else None, "end_value": sign * end[0] if end else None,
                          "measured": spent, "stopped": stopped})
        if start and end and spent:
            moved = {k: v for k, v in end[1].items() if start[1].get(k) != v}
            state.lessons.append(
                f"[{self.tag}] {metric} {sign * start[0]:.6g} -> {sign * end[0]:.6g} over {spent} measured"
                + (f", moving {', '.join(f'{k}={v}' for k, v in list(moved.items())[:4])}" if moved else ", nothing better")
                + f"; ended: {stopped}")

    def begin(self, problem, state, space, seen, metric, sign):
        """The incumbent a walk starts from: the phase's start, else the best measured, else
        the space's first point measured now."""
        here = self.incumbent(state, metric, sign)
        if here is not None or _seeded(state):
            return here                   # the world's seeds are the start; none measured, nothing to walk from
        got = yield self.batch(problem, state, [first(space)], seen)
        vals = self.values(self.admitted(state, got), metric, sign)
        return min(vals, key=lambda t: t[0]) if vals else None


@dataclass
class Sweep(Policy):
    name: str = "sweep"
    batch_size: int = 0          # 0 = every point at once

    def walk(self, problem, state, space, seen):
        moving = self.movable(space)
        if len(moving) < len(space):
            # `knobs`/`hold` in a sweep: every point of the moved knobs, the others at the
            # incumbent (the phase's start, else the best measured, else the first choice) (D608)
            obj = self.objective(problem, state)
            here = dict(self.start) if self.start is not None else (self.incumbent(state, *obj) if obj else None)
            base = dict(here[1]) if isinstance(here, tuple) else dict(here or first(space))
            pts = [{**base, **p} for p in points({k: space[k] for k in moving})]
        else:
            pts = points(space)
        pts = list({_key(p): p for p in map(self.canon, pts)}.values())
        todo = [p for p in pts if _key(p) not in seen]
        if not todo:
            state.search_done = True                  # every point is on record: nothing to propose
        size = int(self.batch_size) or max(1, len(todo))
        state.say(f"  sweep: {len(todo)} point(s) of {len(pts)}"
                  + (f" (over {', '.join(moving)})" if len(moving) < len(space) else "") + f", {size} a batch")
        for i in range(0, len(todo), size):
            if i + size >= len(todo):
                state.search_done = True              # the last batch: nothing left to propose
            got = yield self.batch(problem, state, todo[i:i + size], seen)
            del got


@dataclass
class MonteCarlo(Policy):
    name: str = "montecarlo"
    samples: int = 24
    batch_size: int = 8
    seed: int = 0

    def walk(self, problem, state, space, seen):
        rng = random.Random(int(self.seed))
        todo = [p for p in points(space) if _key(p) not in seen]
        rng.shuffle(todo)
        todo = todo[:max(1, int(self.samples))]
        size = max(1, int(self.batch_size))
        state.say(f"  montecarlo: {len(todo)} of {len(points(space))} point(s), seed {self.seed}, {size} a batch")
        for i in range(0, len(todo), size):
            got = yield self.batch(problem, state, todo[i:i + size], seen)
            del got


@dataclass
class Anneal(Policy):
    name: str = "anneal"
    steps: int = 32
    seed: int = 0
    temperature: float = 1.0
    cooling: float = 0.9

    def walk(self, problem, state, space, seen):
        obj = self.objective(problem, state)
        if obj is None:
            return
        metric, sign = obj
        rng = random.Random(int(self.seed))
        here = self.incumbent(state, metric, sign)
        if here is None:
            got = yield self.batch(problem, state, [first(space)], seen)
            vals = self.values(got, metric, sign)
            if not vals:
                return
            here = min(vals, key=lambda t: t[0])
        temp = float(self.temperature)
        for _ in range(int(self.steps)):
            cand = next((n for n in (neighbour(space, here[1], rng) for _ in range(20)) if _key(n) not in seen), None)
            if cand is None:
                return
            got = yield self.batch(problem, state, [cand], seen)
            vals = self.values(got, metric, sign)
            if vals:
                delta = vals[0][0] - here[0]
                if delta <= 0 or rng.random() < math.exp(-delta / max(1e-9, temp)):
                    here = vals[0]
            temp *= float(self.cooling)


@dataclass
class Gradient(Policy):
    """Coordinate descent: a wave of one-knob moves from the incumbent (the world's `moves`,
    else the space's neighbours), the best admitted one becomes the incumbent when it clears
    `margin`, until `patience` steps improve nothing, the `budget` is spent or no move is
    left. `wave: 0` takes every move a step."""

    name: str = "gradient"
    steps: int = 16
    wave: int = 0
    patience: int = 1
    budget: int = 0
    reach: str = "adjacent"

    def moves_from(self, problem, state, space, point, seen) -> list[dict[str, Any]]:
        hook = getattr(problem, "moves", None)
        given = hook(dict(point), self, state) if callable(hook) else None
        pts = [self.canon(p) for p in (list(given) if given is not None else around(space, point, self.movable(space), self.reach))]
        out, keys = [], set()
        for p in pts:
            k = _key(p)
            if k not in seen and k not in keys:
                keys.add(k)
                out.append(dict(p))
        return out

    def walk(self, problem, state, space, seen):
        obj = self.objective(problem, state)
        if obj is None:
            return
        metric, sign = obj
        here = yield from self.begin(problem, state, space, seen, metric, sign)
        if here is None:
            self.record(state, metric, sign, None, None, 0, "nothing to start from")
            return
        start, spent, flat, stopped = here, 0, 0, f"{self.steps} step(s)"
        for _ in range(int(self.steps)):
            room = int(self.budget) - spent if self.budget else None
            if room is not None and room <= 0:
                stopped = "budget spent"
                break
            pts = self.moves_from(problem, state, space, here[1], seen)
            cap = min(x for x in (int(self.wave) or len(pts), room if room is not None else len(pts), len(pts)))
            if not pts or cap <= 0:
                stopped = "no unexplored move left"
                break
            got = yield self.batch(problem, state, pts[:cap], seen)
            spent += cap
            vals = self.values(self.admitted(state, got), metric, sign)
            best = min(vals, key=lambda t: t[0]) if vals else None
            if best is not None and here[0] - best[0] > float(self.margin):
                here, flat = best, 0
                state.say(f"  {self.tag}: {cap} measured, {metric} now {sign * best[0]:.6g}")
            else:
                flat += 1
                state.say(f"  {self.tag}: {cap} measured, none improved {metric} "
                          f"({flat}/{self.patience} flat)" + (f"; best of the wave {sign * best[0]:.6g}" if best else ""))
                if flat >= int(self.patience):
                    stopped = f"{self.patience} flat step(s)"
                    break
        self.record(state, metric, sign, start, here, spent, stopped)


@dataclass
class Control(Policy):
    """One measurement (D583): the first seed -- the design the search started from -- with
    only the `keep` knobs of the incumbent. Measured beside the winner, it says what the
    search's OTHER choices bought (the prefetcher's stack at its shipped defaults)."""

    name: str = "control"
    keep: tuple = ()

    def walk(self, problem, state, space, seen):
        obj = self.objective(problem, state)
        metric, sign = obj if obj else ("", 1.0)
        seeds = next((d["points"] for d in state.dse if d.get("label") == "seed"), [])
        here = dict(self.start) if self.start is not None else (self.incumbent(state, metric, sign) or (0, None))[1]
        base = dict(seeds[0]) if seeds else (first(space) if space else None)
        if base is None or here is None:
            return
        point = {**base, **{k: v for k, v in here.items() if _matches(k, self.keep)}}
        if _key(point) not in seen:
            yield self.batch(problem, state, [point], seen)
        v = self.metrics_at(state, point).get(metric)
        w = self.metrics_at(state, here).get(metric)
        state.dse.append({"label": self.tag, "policy": self.name, "metric": metric, "start": here, "end": here,
                          "control": point, "control_value": v, "measured": 1, "stopped": "one point"})
        if v is not None and w is not None:
            state.lessons.append(f"[{self.tag}] the starting design with the incumbent's {', '.join(self.keep) or 'nothing'} "
                                 f"measures {metric} {float(v):.6g} against the incumbent's {float(w):.6g}: "
                                 f"the search's other choices are worth {float(w) - float(v):+.6g}")


@dataclass
class Pareto(Policy):
    """A Pareto-UCT tree over the first two objectives (D368): each
    wave expands the measured design whose branch has been buying hypervolume, with crowding
    pulling toward the front's gaps. Half the wave is the moves a nearest-neighbour estimate
    says gain the most, half the nearest-first order -- an estimate from this run's own points
    is confidently wrong about directions nothing has measured. `reference`/`scale`, in
    maximise form (a minimised metric negated), default to the seeds' range."""

    name: str = "pareto"
    budget: int = 24
    wave: int = 6
    reference: tuple = ()
    scale: tuple = ()
    reach: str = "adjacent"

    moves_from = Gradient.moves_from

    def walk(self, problem, state, space, seen):
        from flux_frontier.pareto_uct import ParetoUCT

        objs = list(problem.objectives() or [])[:2]
        if len(objs) < 2:
            state.say(f"  {self.tag}: the Pareto tree needs two objectives")
            return
        axes = [(o.metric, -1.0 if o.direction == "minimize" else 1.0) for o in objs]

        def of(metrics: dict) -> tuple[float, float] | None:
            if any(metrics.get(m) is None for m, _ in axes):
                return None
            return tuple(sgn * float(metrics[m]) for m, sgn in axes)

        measured = [(point_of(s.candidate), o) for s in self.admitted_quiet(state, list(state.scored))
                    if point_of(s.candidate) and (o := of(s.metrics)) is not None]
        if not measured:
            if _seeded(state):
                return                    # the world's seeds are the start; none measured, nothing to grow from
            got = yield self.batch(problem, state, [first(space)], seen)
            measured = [(point_of(s.candidate), o) for s in self.admitted(state, got) if (o := of(s.metrics)) is not None]
            if not measured:
                return
        lo = [min(o[i] for _, o in measured) for i in (0, 1)]
        hi = [max(o[i] for _, o in measured) for i in (0, 1)]
        span = [max(hi[i] - lo[i], abs(hi[i]) * 0.1, 1e-9) for i in (0, 1)]
        ref = tuple(float(x) for x in self.reference) or (lo[0] - 0.1 * span[0], lo[1] - 0.1 * span[1])
        scale = tuple(float(x) for x in self.scale) or (span[0], span[1])
        tree = ParetoUCT(reference=ref, scale=scale, budget=int(self.budget), identity=_key)
        tree.grow([(p, o) for p, o in measured])
        spent, barren = 0, 0
        while spent < int(self.budget) and barren < 32:
            node = tree.select()
            base = node.candidate or max(measured, key=lambda t: t[1][0])[0]
            pts = self.moves_from(problem, state, space, base, seen)
            if not pts:
                tree.exhausted(node)
                barren += 1
                if node is tree.root:
                    break
                continue
            barren = 0
            width = min(int(self.wave), int(self.budget) - spent)
            ranked = sorted(((tree.predicted_gain(est) if (est := _estimate(p, measured, space)) else 0.0, i)
                             for i, p in enumerate(pts)), key=lambda g: -g[0])
            pick = [pts[i] for g, i in ranked[:(width + 1) // 2] if g > 0]
            for p in pts:
                if len(pick) >= width:
                    break
                if all(_key(p) != _key(q) for q in pick):
                    pick.append(p)
            got = yield self.batch(problem, state, pick, seen)
            spent += len(pick)
            for s in self.admitted(state, got):
                o = of(s.metrics)
                if o is not None:
                    tree.record(node, point_of(s.candidate), o)
                    measured.append((point_of(s.candidate), o))
            state.say(f"  {self.tag}: expanded one design, {len(pick)} measured; the front holds {len(tree.front())}")
        front = tree.front()
        state.dse.append({"label": self.tag, "policy": self.name, "metric": axes[0][0], "measured": spent,
                          "front": [n.candidate for n in front], "stopped": "budget spent" if spent >= int(self.budget) else "no move left",
                          "start": None, "end": max(front, key=lambda n: n.objectives[0]).candidate if front else None})


def _distance(a: dict[str, Any], b: dict[str, Any], space: dict[str, list]) -> float:
    """Knob by knob: a numeric knob's log2 step (a doubling is one), anything else 0 or 1."""
    total = 0.0
    for k in space:
        x, y = a.get(k), b.get(k)
        if isinstance(x, (int, float)) and isinstance(y, (int, float)) and not isinstance(x, bool):
            total += abs(math.log2(max(1e-9, abs(float(x)) + 1)) - math.log2(max(1e-9, abs(float(y)) + 1)))
        else:
            total += 0.0 if x == y else 1.0
    return total


def _estimate(p: dict[str, Any], measured: list[tuple[dict, tuple]], space: dict[str, list]) -> tuple[float, float] | None:
    """The three nearest measured points' inverse-distance vote; None under five points."""
    if len(measured) < 5:
        return None
    near = sorted(((_distance(p, q, space), o) for q, o in measured), key=lambda t: t[0])[:3]
    if near[0][0] == 0.0:
        return near[0][1]
    w = [1.0 / d for d, _ in near]
    return tuple(sum(wi * o[i] for wi, (_, o) in zip(w, near)) / sum(w) for i in (0, 1))


@dataclass
class Phases(Policy):
    """Walks in order (D583), each from the incumbent the last ended on."""

    name: str = "phases"
    phases: tuple = ()

    def walk(self, problem, state, space, seen):
        here = None
        for i, spec in enumerate(self.phases):
            sub = make_phase(spec, default_label=f"phase {i + 1}")
            sub.start = here
            sub._space, sub._when, sub._home = self._space, self._when, self._home
            state.say(f"{sub.tag}: {sub.name}" + (f" over {', '.join(sub.movable(space))}" if sub.knobs or sub.hold else ""))
            yield from sub.walk(problem, state, space, seen)
            done = next((d for d in reversed(state.dse) if d.get("label") == sub.tag), None)
            if done is not None and done.get("end") is not None:
                here = dict(done["end"])


@dataclass
class Genetic(Policy):
    name: str = "genetic"
    population: int = 8
    generations: int = 6
    seed: int = 0
    mutation: float = 0.3

    def walk(self, problem, state, space, seen):
        obj = self.objective(problem, state)
        if obj is None:
            return
        metric, sign = obj
        rng = random.Random(int(self.seed))
        size = max(2, int(self.population))
        pop: list[dict[str, Any]] = []
        for _ in range(size * 50):                 # sampled knob by knob: never the whole grid
            if len(pop) >= size:
                break
            p = {k: rng.choice(vals) for k, vals in space.items()}
            if _key(p) not in seen and all(_key(p) != _key(q) for q in pop):
                pop.append(p)
        got = yield self.batch(problem, state, pop, seen)
        ranked = sorted(self.values(got, metric, sign) + self.values(list(state.scored), metric, sign), key=lambda t: t[0])
        for _ in range(int(self.generations)):
            parents = [p for _v, p in ranked[:max(2, size // 2)]]
            if len(parents) < 2:
                return
            children: list[dict[str, Any]] = []
            tries = 0
            while len(children) < size and tries < size * 20:
                tries += 1
                a, b = rng.sample(parents, 2)
                child = {k: (a[k] if rng.random() < 0.5 else b[k]) for k in space}
                if rng.random() < float(self.mutation):
                    child = neighbour(space, child, rng)
                if _key(child) in seen or any(_key(child) == _key(c) for c in children):
                    continue
                children.append(child)
            if not children:
                return
            got = yield self.batch(problem, state, children, seen)
            ranked = sorted(self.values(got, metric, sign) + ranked, key=lambda t: t[0])


@dataclass
class ModelSearch(Policy):
    """The model's half of the DSE box (`flow: {dse: llm}`, D554): each round the model reads
    the space, the objective and every point measured so far (best first) and proposes the
    next `batch_size` NEW points; a point outside the space or measured already is dropped
    and said. One retry when a round proposes nothing usable, then the walk ends."""

    name: str = "model"
    batch_size: int = 4
    rounds: int = 8
    shown: int = 40
    agent: Any = None            # a coding agent proposes the points instead of the model (D640)

    def walk(self, problem, state, space, seen):
        from .boxes import box_turn
        from .model import _ask, _json
        from .objective import Objectives

        if state.proposer is None and self.agent is None:
            state.say("  dse: llm asks a model for the next points and this run has none")
            return
        obj = self.objective(problem, state)
        metric, sign = obj if obj else ("", 1.0)
        objs = list(problem.objectives() or [])
        goal = objs[0] if objs else None
        full = space
        base: dict[str, Any] = {}
        if self.knobs or self.hold:              # a phase moves some knobs, holds the rest
            here = dict(self.start) if self.start is not None else (self.incumbent(state, metric, sign) or (0, {}))[1]
            base = {k: v for k, v in (here or {}).items() if k not in self.movable(full)}
            space = {k: full[k] for k in self.movable(full)}
        schema = {"type": "object",
                  "properties": {"points": {"type": "array", "items": {"type": "object"}},
                                 "why": {"type": "string"}},
                  "required": ["points"]}
        complaint = ""
        for round_ in range(int(self.rounds)):
            measured = self.values(list(state.scored), metric, sign) if metric else []
            measured.sort(key=lambda t: t[0])
            rows = [f"  {json.dumps({k: p.get(k) for k in space}, default=str)} -> {metric} {sign * v:g}" for v, p in measured[:int(self.shown)]]
            prefix = ""
            hook = getattr(problem, "prompt_prefix", None)
            if callable(hook):
                try:
                    prefix = str(hook(None, state) or "")
                except Exception:  # noqa: BLE001 -- a prefix is context, never a reason to stop
                    prefix = ""
            lines = [
                *([prefix.rstrip()] if prefix.strip() else []),
                f"DESIGN-SPACE EXPLORATION, round {round_ + 1} of {self.rounds}. The space (each knob and its choices, in order):",
                *(f"  {k}: {json.dumps(v)}" for k, v in space.items()),
                *([f"The other knobs are held at the incumbent's: {json.dumps(base, default=str)}"] if base else []),
                ("OBJECTIVE: " + Objectives(objs).describe()) if goal else        # every limit, then what decides (D660)
                "OBJECTIVE: none declared; propose points that cover the space",
                (f"MEASURED SO FAR ({len(measured)} point(s), best first):\n" + "\n".join(rows)) if rows else "MEASURED SO FAR: nothing",
                f"{size(space)} point(s) in this space.",
                f"Propose the {int(self.batch_size)} NEW points most worth measuring next -- near the best when the trend is clear, "
                "away from it when the measured points do not tell. Every point names EVERY knob with one of its choices, exactly as written.",
            ]
            if complaint:
                lines.append("LAST ROUND: " + complaint)
            if self.agent is not None:
                def usable(d: dict) -> str | None:
                    ok = [q for p in d.get("points") or [] if (q := _coerce(space, p)) is not None
                          and _key({**base, **q} if base else q) not in seen]
                    return None if ok else "no point is new and inside the space (every knob, one of its choices)"
                doc = box_turn("dse", self.agent, "\n".join(lines), schema, state, check=usable,
                               home=str(getattr(getattr(problem, "task", None), "home", "") or ""), problem=problem)
                if doc is None:
                    return                          # the agent fell back: the phase ends, the next one runs
            else:
                lines.append("Reply as JSON: {\"points\": [{knob: choice, ...}, ...], \"why\": \"one line\"}.")
                try:
                    doc = _json(_ask(state, "\n".join(lines), schema).text)
                except Exception as exc:  # noqa: BLE001 -- the model's turn failed; the walk ends
                    state.say(f"  dse: the model's round did not run ({exc!s:.100})")
                    return
            raw = (doc or {}).get("points") if isinstance(doc, dict) else None
            good, bad = [], []
            for p in raw or []:
                q = _coerce(space, p)
                if q is not None and base:
                    q = {**base, **q}
                if q is None:
                    bad.append(f"{json.dumps(p)[:80]} is not a point of the space")
                elif _key(q) in seen or any(_key(q) == _key(g) for g in good):
                    bad.append(f"{json.dumps(q)} is measured already")
                else:
                    good.append(q)
            why = str((doc or {}).get("why") or "")[:160] if isinstance(doc, dict) else ""
            state.say(f"  dse: the {'agent' if self.agent is not None else 'model'} proposes {len(good)} point(s)" + (f" -- {why}" if why else "")
                      + (f"; {len(bad)} dropped ({bad[0]})" if bad else ""))
            if not good:
                if complaint:
                    return
                complaint = "; ".join(bad[:3]) or "no points in the reply"
                continue
            complaint = ""
            got = yield self.batch(problem, state, good, seen)
            del got


def _coerce(space: dict[str, list], point: Any) -> dict[str, Any] | None:
    """The point with each value as the space spells it (a "2" for a 2), or None."""
    if not isinstance(point, dict) or set(point) != set(space):
        return None
    out = {}
    for k, vals in space.items():
        v = point[k]
        match = next((c for c in vals if c == v or str(c) == str(v)), None)
        if match is None:
            return None
        out[k] = match
    return out


_TUPLES = ("knobs", "hold", "keep", "reference", "scale")


def _config(cls, config: dict[str, Any]) -> dict[str, Any]:
    names = {f.name for f in fields(cls)} - {"name", "start"}
    cfg = dict(config or {})
    if "batch" in cfg and "batch_size" in names:
        cfg["batch_size"] = cfg.pop("batch")
    if "name" in cfg:                        # in a phase, `name` is its label
        cfg["label"] = cfg.pop("name")
    unknown = sorted(set(cfg) - names)
    if unknown:
        raise ValueError(f"{cls.name}: {', '.join(unknown)} is not one of its fields ({', '.join(sorted(names))})")
    for k in _TUPLES:
        if k in cfg and isinstance(cfg[k], (list, tuple)):
            cfg[k] = tuple(cfg[k])
    if "phases" in cfg:
        cfg["phases"] = tuple(cfg["phases"])
        for i, spec in enumerate(cfg["phases"]):
            make_phase(spec, default_label=f"phase {i + 1}")        # a typo is a load error
    return cfg


_POLICIES: dict[str, type] = {}


def make_phase(spec: Any, default_label: str = "") -> Policy:
    """One phase of `flow: {dse: [...]}` from its spec: `policy` (default gradient) and the
    policy's own fields."""
    if isinstance(spec, str):
        spec = {"policy": spec}
    if not isinstance(spec, dict):
        raise ValueError(f"a phase is a policy name or {{policy: ..., ...}}, not {spec!r}")
    spec = dict(spec)
    name = str(spec.pop("policy", "gradient"))
    name = "model" if name == "llm" else name
    if ":" in name:                                   # `module:Class`, a policy of your own
        from .document import TaskError, resolve

        try:
            cls = resolve(name, "policy")
        except TaskError as exc:
            raise ValueError(str(exc)) from exc
        if not (isinstance(cls, type) and issubclass(cls, Policy)):
            raise ValueError(f"policy {name!r} is not a flux_loop.dse.Policy subclass")
        pol = cls(**_config(cls, spec))
        pol.label = pol.label or default_label or name
        return pol
    cls = _POLICIES.get(name)
    if cls is None or cls is Phases:
        raise ValueError(f"phase policy {name!r} is not one of {', '.join(n for n in _POLICIES if n != 'phases')}")
    pol = cls(**_config(cls, spec))
    if not pol.label:
        pol.label = default_label
    return pol


def validate_phase(spec: Any) -> None:
    make_phase(spec)


@dataclass
class CommandSearch(Policy):
    """A search a command runs (D799): the document's own chain of tries -- a solver, a proof,
    a model it asks itself -- as a script beside it. Each round the loop writes `{history}`, a
    JSON file of everything measured and refused so far (name, knobs, stage, metrics, why), and
    keeps `{state}`, a JSON file the command may read and write between rounds; the command
    prints one JSON object (its last line that is one):

        {"candidates": [{"name", "artifact", "knobs"?, "why"?, "by"?}],
         "lessons": [...], "not_established": [...], "conclusion": {...}?, "done": false}

    The candidates go through the gate and the stages like any; `done` (or no candidates) ends
    the search; a `conclusion` is recorded as the run's answer when nothing was decided (a
    partial answer, a proof that none exists)."""

    name: str = "command"
    run: Any = ""
    timeout_s: float = 600.0

    def search(self, problem: Any, state: Any) -> Iterator[list[Candidate]] | None:
        if not self.run:
            state.say("  command search: no `run` command")
            return None
        return self._rounds(problem, state)

    def _history(self, state: Any) -> dict[str, Any]:
        measured = [{"name": s.candidate.name, "knobs": dict(s.candidate.knobs or {}), "stage": s.stage,
                     "metrics": dict(s.metrics), "by": s.candidate.meta.get("strategy", "")} for s in state.scored]
        refused = [{"name": n, "why": why} for n, why in state.refused]
        records = getattr(state, "records", None)             # a resumed record's refusals too
        if records is not None and getattr(records, "resumed", False):
            have = {r["name"] for r in refused}
            for cand, why in records.refusals(limit=500):
                name = str(cand.get("name") or "")
                if name and name not in have:
                    have.add(name)
                    refused.insert(0, {"name": name, "why": why})
        drain = getattr(state, "drain", None)                 # the operator's notes, for a model the command asks
        guidance = drain() if callable(drain) else None
        return {"measured": measured, "refused": refused, "guidance": guidance or ""}

    def _rounds(self, problem: Any, state: Any) -> Iterator[list[Candidate]]:
        import json
        from pathlib import Path

        from .document import _command

        task = getattr(problem, "task", None)
        out_dir = task.out_dir() if task is not None and callable(getattr(task, "out_dir", None)) else Path(state.workdir or ".")
        out_dir.mkdir(parents=True, exist_ok=True)
        keep = out_dir / f"{getattr(task, 'record', '') or 'search'}.search-state.json"
        history = Path(state.workdir or out_dir) / "search-history.json"
        cmd = tuple(_command(self.run, "flow.orchestrate.command"))
        rounds = 0
        while True:
            rounds += 1
            history.parent.mkdir(parents=True, exist_ok=True)
            history.write_text(json.dumps({**self._history(state), "round": rounds}, indent=1, default=str))
            subs = {"history": str(history), "state": str(keep), "workdir": str(history.parent),
                    "home": getattr(task, "home", "") or ".", "python": __import__("sys").executable,
                    "artifact": "", "name": "search", "part": "", "point": ""}
            got = problem._run(cmd, subs, self.timeout_s, "search")
            said = _last_json((got.stdout or ""))
            if not got.ok or said is None:
                tail = ((got.stdout or "") + "\n" + (got.stderr or "")).strip()[-300:]
                state.not_established.append(f"the search command stopped (exit {got.returncode}): {tail}")
                return
            for line in said.get("lessons") or []:
                state.lessons.append(str(line))
                state.say(f"  {line}")
            state.not_established.extend(str(x) for x in said.get("not_established") or [])
            if said.get("conclusion") and getattr(state, "records", None) is not None:
                state.records.conclude(dict(said["conclusion"]))
            cands = [Candidate(name=str(c["name"]), artifact=str(c.get("artifact", "")), knobs=dict(c.get("knobs") or {}),
                               meta={"strategy": str(c.get("by") or "command"), **({"idea": str(c["why"])} if c.get("why") else {})})
                     for c in said.get("candidates") or [] if isinstance(c, dict) and c.get("name")]
            state.say(f"  command search, round {rounds}: {len(cands)} candidate(s)"
                      + (f" -- {said['say']}" if said.get("say") else ""))
            if not cands:
                if hasattr(state, "search_done"):
                    state.search_done = True
                return
            if said.get("done") and hasattr(state, "search_done"):
                state.search_done = True
            yield cands
            if said.get("done"):
                return


def _last_json(text: str) -> dict[str, Any] | None:
    """The last line of `text` that is a JSON object (a command may print other lines first)."""
    import json

    for line in reversed(text.strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                got = json.loads(line)
            except ValueError:
                continue
            if isinstance(got, dict):
                return got
    try:
        got = json.loads(text)
        return got if isinstance(got, dict) else None
    except ValueError:
        return None


def _factory(cls):
    def make(config: dict[str, Any]):
        return cls(**_config(cls, config))

    return make


for _cls in (Sweep, MonteCarlo, Anneal, Gradient, Genetic, ModelSearch, Pareto, Control, Phases, CommandSearch):
    _POLICIES[_cls.name] = _cls
    register("orchestrator", _cls.name, _factory(_cls))
