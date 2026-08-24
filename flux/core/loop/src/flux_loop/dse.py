"""The DSE box (D465 promised it, D553 delivers it): search policies over a declared space,
as orchestrators anyone can select.

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

The objective a policy reads is the document's first (D511): its metric and direction. A
policy is also the rules orchestrator for a document with parts, so the two can coexist.
"""

from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass, fields
from typing import Any, Iterator

from .roles import Rules, register
from .types import Candidate, Scored

__all__ = ["Anneal", "Genetic", "Gradient", "ModelSearch", "MonteCarlo", "Policy", "Sweep", "neighbour", "points"]


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


def _key(point: dict[str, Any]) -> tuple:
    return tuple((k, repr(v)) for k, v in point.items())


@dataclass
class Policy(Rules):
    """What every policy shares: the space from the problem, the points measured already,
    the objective it reads (the document's first), the batch it hands to the gate."""

    name: str = "policy"

    def search(self, problem: Any, state: Any) -> Iterator[list[Candidate]] | None:
        space = {k: list(v) for k, v in dict(problem.space(state) or {}).items() if v}
        if not space:
            state.say(f"  {self.name}: no `space:` is declared and the world names none; nothing to search")
            return None
        seen = {_key(s.candidate.knobs) for s in state.scored if s.candidate.knobs}
        return self.walk(problem, state, space, seen)

    def walk(self, problem: Any, state: Any, space: dict[str, list], seen: set) -> Iterator[list[Candidate]]:
        raise NotImplementedError

    # ---- what the subclasses use
    def batch(self, problem: Any, state: Any, pts: list[dict[str, Any]], seen: set) -> list[Candidate]:
        fresh = []
        for p in pts:
            k = _key(p)
            if k in seen:
                continue
            seen.add(k)
            fresh.append(p)
        return list(problem.instantiate(fresh, state)) if fresh else []

    def objective(self, problem: Any, state: Any) -> tuple[str, float] | None:
        """(metric, sign): the value times sign is what a policy MINIMISES."""
        objs = list(problem.objectives() or [])
        if not objs:
            state.say(f"  {self.name}: the document declares no objective; the policy cannot rank")
            return None
        return objs[0].metric, (1.0 if objs[0].direction == "minimize" else -1.0)

    @staticmethod
    def values(scored: list[Scored] | None, metric: str, sign: float) -> list[tuple[float, dict[str, Any]]]:
        return [(sign * float(v), dict(s.candidate.knobs))
                for s in (scored or []) if s.candidate.knobs and (v := s.metrics.get(metric)) is not None]

    def incumbent(self, state: Any, metric: str, sign: float) -> tuple[float, dict[str, Any]] | None:
        best = self.values(list(state.scored), metric, sign)
        return min(best, key=lambda t: t[0]) if best else None


@dataclass
class Sweep(Policy):
    name: str = "sweep"
    batch_size: int = 0          # 0 = every point at once

    def walk(self, problem, state, space, seen):
        todo = [p for p in points(space) if _key(p) not in seen]
        size = int(self.batch_size) or max(1, len(todo))
        state.say(f"  sweep: {len(todo)} point(s) of {len(points(space))}, {size} a batch")
        for i in range(0, len(todo), size):
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
            got = yield self.batch(problem, state, [points(space)[0]], seen)
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
    name: str = "gradient"
    steps: int = 16

    def walk(self, problem, state, space, seen):
        obj = self.objective(problem, state)
        if obj is None:
            return
        metric, sign = obj
        here = self.incumbent(state, metric, sign)
        if here is None:
            got = yield self.batch(problem, state, [points(space)[0]], seen)
            vals = self.values(got, metric, sign)
            if not vals:
                return
            here = min(vals, key=lambda t: t[0])
        for _ in range(int(self.steps)):
            around = [n for n in neighbours(space, here[1]) if _key(n) not in seen]
            if not around:
                return
            got = yield self.batch(problem, state, around, seen)
            vals = self.values(got, metric, sign)
            if not vals:
                return
            best = min(vals, key=lambda t: t[0])
            if best[0] >= here[0]:
                state.say(f"  gradient: no neighbour of {here[1]} improves {metric}; the walk ends")
                return
            here = best


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
        grid = points(space)
        first = [p for p in grid if _key(p) not in seen]
        rng.shuffle(first)
        got = yield self.batch(problem, state, first[:size], seen)
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

    def walk(self, problem, state, space, seen):
        from .model import _ask, _json

        if state.proposer is None:
            state.say("  dse: llm asks a model for the next points and this run has none")
            return
        obj = self.objective(problem, state)
        metric, sign = obj if obj else ("", 1.0)
        objs = list(problem.objectives() or [])
        goal = objs[0] if objs else None
        schema = {"type": "object",
                  "properties": {"points": {"type": "array", "items": {"type": "object"}},
                                 "why": {"type": "string"}},
                  "required": ["points"]}
        complaint = ""
        for round_ in range(int(self.rounds)):
            measured = self.values(list(state.scored), metric, sign) if metric else []
            measured.sort(key=lambda t: t[0])
            rows = [f"  {json.dumps(p)} -> {metric} {sign * v:g}" for v, p in measured[:int(self.shown)]]
            lines = [
                f"DESIGN-SPACE EXPLORATION, round {round_ + 1} of {self.rounds}. The space (each knob and its choices, in order):",
                *(f"  {k}: {json.dumps(v)}" for k, v in space.items()),
                ("OBJECTIVE: " + f"{goal.direction} {goal.metric}" + (f", goal {goal.goal:g}" if goal.goal is not None else "")) if goal else
                "OBJECTIVE: none declared; propose points that cover the space",
                (f"MEASURED SO FAR ({len(measured)} point(s), best first):\n" + "\n".join(rows)) if rows else "MEASURED SO FAR: nothing",
                f"{len(points(space)) - len(seen)} point(s) of {len(points(space))} are not measured yet.",
                f"Propose the {int(self.batch_size)} NEW points most worth measuring next -- near the best when the trend is clear, "
                "away from it when the measured points do not tell. Every point names EVERY knob with one of its choices, exactly as written. "
                "Reply as JSON: {\"points\": [{knob: choice, ...}, ...], \"why\": \"one line\"}.",
            ]
            if complaint:
                lines.append("LAST ROUND: " + complaint)
            try:
                doc = _json(_ask(state, "\n".join(lines), schema).text)
            except Exception as exc:  # noqa: BLE001 -- the model's turn failed; the walk ends
                state.say(f"  dse: the model's round did not run ({exc!s:.100})")
                return
            raw = (doc or {}).get("points") if isinstance(doc, dict) else None
            good, bad = [], []
            for p in raw or []:
                q = _coerce(space, p)
                if q is None:
                    bad.append(f"{json.dumps(p)[:80]} is not a point of the space")
                elif _key(q) in seen or any(_key(q) == _key(g) for g in good):
                    bad.append(f"{json.dumps(q)} is measured already")
                else:
                    good.append(q)
            why = str((doc or {}).get("why") or "")[:160] if isinstance(doc, dict) else ""
            state.say(f"  dse: the model proposes {len(good)} point(s)" + (f" -- {why}" if why else "")
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


def _factory(cls):
    names = {f.name for f in fields(cls)} - {"name"}

    def make(config: dict[str, Any]):
        cfg = {k: v for k, v in (config or {}).items() if k in names}
        if "batch" in (config or {}) and "batch_size" in names:
            cfg["batch_size"] = config["batch"]
        return cls(**cfg)

    return make


for _cls in (Sweep, MonteCarlo, Anneal, Gradient, Genetic, ModelSearch):
    register("orchestrator", _cls.name, _factory(_cls))
