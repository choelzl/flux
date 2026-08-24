"""The drawing's DSE box, as orchestrators anyone can select (D465).

Montecarlo, a grid sweep and simulated annealing are search POLICIES over a declared space, and
nothing about them is specific to a study: given the knobs and their choices, each one knows what
to propose next. They existed in this repository as study code (the interconnect's annealing
chain, the prefetcher's climbs, the exhaustive sweep over flat mappings) and so could not be
switched on for anything else.

A problem declares its space -- `Problem.space() -> {"width": [8, 16, 32], "banks": [4, 8]}` --
and any of these fills the orchestration role:

    rig(orchestrator="sweep")                                  # every point, in batches
    rig(orchestrator={"montecarlo": {"samples": 20, "seed": 3}})
    rig(orchestrator={"anneal": {"metric": "served", "minimize": False}})

Each one is a generator of batches (D446), so the loop gates and measures each batch and hands
the results back -- which is what makes annealing possible at all: it reads the numbers of the
batch it just proposed before choosing the next move.

What a component proposes is a POINT: `Candidate(knobs=point)` with no artifact. Turning a point
into something buildable is the problem's `build`, exactly as in every study whose candidates are
points rather than text (a bank mapping, a prefetcher configuration).

Deliberately NOT here: a generic Pareto-UCT. `flux_frontier.ParetoUCT` needs a reference point
and a scale per axis to mean anything, and those are the problem's own knowledge -- a wrapper
that invented them would produce a tree whose numbers look fine and rank nothing (the prefetcher
supplies them and uses it directly).
"""

from __future__ import annotations

import itertools
import math
import random
from dataclasses import dataclass, field
from typing import Any, Iterator

from .types import Candidate

__all__ = ["Anneal", "MonteCarlo", "Sweep", "neighbour", "points", "space_of"]


def space_of(problem: Any) -> dict[str, list[Any]]:
    """A problem's declared space, checked: knob -> its choices, every list non-empty."""
    space = dict(getattr(problem, "space", lambda: {})() or {})
    if not space:
        raise ValueError(
            f"{getattr(problem, 'name', problem)} declares no space, so there is nothing for a "
            f"DSE orchestrator to search: implement `space()` as {{knob: [choices, ...]}}")
    for knob, choices in space.items():
        if not isinstance(choices, (list, tuple)) or not len(choices):
            raise ValueError(f"the space's {knob!r} needs a non-empty list of choices, "
                             f"not {choices!r}")
    return {k: list(v) for k, v in space.items()}


def points(space: dict[str, list[Any]]) -> list[dict[str, Any]]:
    """Every point of the space, knobs in declared order -- the grid."""
    keys = list(space)
    return [dict(zip(keys, combo)) for combo in itertools.product(*(space[k] for k in keys))]


def neighbour(space: dict[str, list[Any]], point: dict[str, Any],
              rng: random.Random) -> dict[str, Any]:
    """One knob moved to an ADJACENT choice: the move an annealer makes. Adjacency is the
    declared order, so listing choices in a meaningful order (widths low to high) is what makes
    a neighbour mean "a little different" rather than "anything else"."""
    knob = rng.choice(list(space))
    choices = space[knob]
    here = choices.index(point[knob]) if point.get(knob) in choices else 0
    step = rng.choice([-1, 1]) if len(choices) > 1 else 0
    return {**point, knob: choices[max(0, min(len(choices) - 1, here + step))]}


def _key(point: dict[str, Any]) -> str:
    return repr(sorted((k, str(v)) for k, v in point.items()))


def _candidate(point: dict[str, Any], strategy: str, n: int) -> Candidate:
    return Candidate(name=f"{strategy}#{n}", artifact="", knobs=dict(point),
                     meta={"strategy": strategy})


@dataclass
class Sweep:
    """Every point in the space, in batches -- the exhaustive answer, and the one that proves
    what the others only approach. `batch` is how many go through the gate and the first stage
    together."""

    batch: int = 8
    name: str = "sweep"

    def search(self, problem: Any, state: Any) -> Iterator[list[Candidate]]:
        space = space_of(problem)
        grid = points(space)
        state.say(f"  sweep: {len(grid)} point(s) of {len(space)} knob(s), "
                  f"{self.batch} at a time")
        for i in range(0, len(grid), max(1, self.batch)):
            chunk = grid[i:i + max(1, self.batch)]
            yield [_candidate(p, self.name, i + n) for n, p in enumerate(chunk)]


@dataclass
class MonteCarlo:
    """Uniform random points, never the same one twice. `samples` bounds the whole search --
    the loop's `steps` bounds the batches -- and `seed` makes it reproducible, which is what
    separates a sample from an anecdote."""

    samples: int = 24
    batch: int = 4
    seed: int = 0
    name: str = "montecarlo"

    def search(self, problem: Any, state: Any) -> Iterator[list[Candidate]]:
        space = space_of(problem)
        rng = random.Random(self.seed)
        grid = points(space)
        want = min(max(1, self.samples), len(grid))
        state.say(f"  montecarlo: {want} of {len(grid)} point(s), seed {self.seed}")
        seen: set[str] = set()
        drawn = 0
        while drawn < want:
            batch: list[Candidate] = []
            while len(batch) < max(1, self.batch) and drawn < want:
                point = {k: rng.choice(v) for k, v in space.items()}
                if _key(point) in seen:
                    if len(seen) >= len(grid):
                        break
                    continue
                seen.add(_key(point))
                batch.append(_candidate(point, self.name, drawn))
                drawn += 1
            if not batch:
                return
            yield batch


@dataclass
class Anneal:
    """Simulated annealing over the declared space: one neighbour of the incumbent per step,
    accepted when it is better or when the temperature says to take it anyway.

    `metric` is what it anneals on and `minimize` which way is better -- a policy cannot guess
    that. The temperature falls geometrically (`cooling`) from `temperature`, and a move is
    accepted with probability exp(-worse/T), the classical rule; `seed` makes the walk
    reproducible. It reads the numbers of the batch it proposed before choosing the next move,
    which is what the loop handing results back to a generator is for (D446).
    """

    metric: str = ""
    minimize: bool = False
    temperature: float = 1.0
    cooling: float = 0.85
    seed: int = 0
    name: str = "anneal"
    _accepted: int = field(default=0, repr=False)
    _rejected: int = field(default=0, repr=False)

    def search(self, problem: Any, state: Any) -> Iterator[list[Candidate]]:
        if not self.metric:
            raise ValueError('an `anneal` orchestrator needs the metric it anneals on, e.g. '
                             '{"anneal": {"metric": "fmax_mhz", "minimize": false}}')
        space = space_of(problem)
        rng = random.Random(self.seed)
        here = {k: rng.choice(v) for k, v in space.items()}
        temperature = max(1e-9, self.temperature)
        state.say(f"  anneal: {self.metric} "
                  f"({'lower' if self.minimize else 'higher'} is better), T={temperature:g}, "
                  f"cooling {self.cooling:g}, seed {self.seed}")
        seen: set[str] = {_key(here)}
        best: float | None = None
        n = 0
        got = yield [_candidate(here, self.name, n)]
        while True:
            value = self._value(got)
            if value is not None and (best is None or self._better(value, best)):
                best = value
            n += 1
            move = neighbour(space, here, rng)
            tries = 0
            while _key(move) in seen and tries < 8:
                move = neighbour(space, here, rng)
                tries += 1
            if _key(move) in seen:
                state.say(f"  anneal: every neighbour of {here} has been measured")
                return
            seen.add(_key(move))
            got = yield [_candidate(move, self.name, n)]
            new = self._value(got)
            if new is None:
                continue                      # nothing measured: the walk stays where it is
            if value is None or self._accept(new, value, temperature, rng):
                here = move
                self._accepted += 1
            else:
                self._rejected += 1
            temperature *= max(1e-9, min(1.0, self.cooling))

    def _value(self, got: Any) -> float | None:
        """The metric this walk anneals on, out of the batch it just proposed -- or None when
        the batch measured nothing, which leaves the walk where it is."""
        for s in got or ():
            value = s.metrics.get(self.metric)
            if value is not None:
                return float(value)
        return None

    def _better(self, a: float, b: float) -> bool:
        return a < b if self.minimize else a > b

    def _accept(self, new: float, old: float, temperature: float, rng: random.Random) -> bool:
        if self._better(new, old):
            return True
        worse = (new - old) if self.minimize else (old - new)
        scale = max(abs(old), 1e-9)
        return rng.random() < math.exp(-worse / scale / max(temperature, 1e-9))
