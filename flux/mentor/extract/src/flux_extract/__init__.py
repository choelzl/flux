"""What a campaign's record can TEACH: rules extracted from measurements (D397).

Records hold results and conclusions; extract builds rules -- laws, directions,
principles -- from them. The founding instance is D369's pairwise analysis, moved here
from the prefetcher because the arithmetic was never prefetcher-specific: two measured
configurations differing in exactly one knob are a controlled experiment somebody
already paid for, and their delta is a fact about the workload. Typed facts computed
fresh from the store on every call (D243): the store IS the memory, there is no prose
memory to drift out of step.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

__all__ = ["Duel", "Law", "duels_text", "head_to_head", "laws_text", "pairwise_laws"]


@dataclass(frozen=True)
class Law:
    """One knob's measured direction, from every one-knob pair the record holds."""

    knob: str
    direction: str                  # "up" or "down"
    mean_delta: float               # mean metric change per pair, in that direction
    pairs: int                      # controlled pairs behind the number
    metric: str = "metric"

    def describe(self) -> str:
        return (f"{self.knob} {self.direction}: {self.mean_delta:+.4f} {self.metric} on "
                f"average over {self.pairs} measured pair(s)")


def pairwise_laws(known: list[tuple[Mapping[str, float], float]], *,
                  min_pairs: int = 2, top: int = 6, metric: str = "metric",
                  coupled: Iterable[frozenset[str]] = ()) -> list[Law]:
    """Knob-direction laws from every pair of measured configs differing in ONE knob.

    `known` maps knob dicts (numeric values) to one measured number each. `coupled`
    names knob sets that move together (one knob wearing two names -- the prefetcher's
    region_size/pattern_len); a pair differing in exactly one coupled set counts as one
    knob, reported under the set's first name (sorted). Directions with fewer than
    `min_pairs` pairs are withheld -- one pair is an anecdote -- and the result is the
    `top` strongest by |mean effect| weighted by evidence, weakest last.
    """
    coupled = [frozenset(c) for c in coupled]
    deltas: dict[str, list[float]] = {}
    for i, (a, ga) in enumerate(known):
        for b, gb in known[i + 1:]:
            keys = set(a) | set(b)
            changed = [k for k in keys if float(a.get(k, 0)) != float(b.get(k, 0))]
            knob = None
            if len(changed) == 1:
                knob = changed[0]
            else:
                for grp in coupled:
                    if set(changed) == grp:
                        knob = sorted(grp)[0]
                        break
            if knob is None:
                continue
            probe = knob if knob in changed else changed[0]
            lo, hi = ((a, ga), (b, gb)) if float(a[probe]) < float(b[probe]) else ((b, gb), (a, ga))
            deltas.setdefault(knob, []).append(hi[1] - lo[1])
    out = []
    for knob, ds in deltas.items():
        if len(ds) < min_pairs:
            continue
        mean = sum(ds) / len(ds)
        out.append(Law(knob=knob, direction="up" if mean >= 0 else "down",
                       mean_delta=abs(mean), pairs=len(ds), metric=metric))
    out.sort(key=lambda k: -k.mean_delta * (1 + min(k.pairs, 8) / 8))
    return out[:top]


@dataclass(frozen=True)
class Duel:
    """One knob's head-to-head verdict between two of its values (D400).

    The categorical companion to Law: where a knob's values are names rather than
    numbers (booth4 vs wallace, tree vs chain), a controlled pair has no direction,
    only a winner -- but it is the same paid-for experiment."""

    knob: str
    winner: object
    loser: object
    mean_delta: float               # winner's mean metric gain over the loser
    pairs: int
    metric: str = "metric"

    def describe(self) -> str:
        return (f"{self.knob}: {self.winner} beats {self.loser} by "
                f"{self.mean_delta:+.4f} {self.metric} on average over "
                f"{self.pairs} controlled pair(s)")


def head_to_head(known: list[tuple[Mapping[str, object], float]], *,
                 min_pairs: int = 2, top: int = 6, metric: str = "metric"
                 ) -> list[Duel]:
    """Head-to-head verdicts from every pair of configs differing in ONE knob.

    Values are compared by equality, so categorical knobs work; each (knob, value,
    value) matchup is accumulated in a canonical order so A-vs-B and B-vs-A pairs
    pool their evidence. Matchups with fewer than `min_pairs` pairs are withheld --
    one pair is an anecdote -- and the result is the `top` strongest by |mean
    effect| weighted by evidence, weakest last."""
    deltas: dict[tuple[str, object, object], list[float]] = {}
    for i, (a, ga) in enumerate(known):
        for b, gb in known[i + 1:]:
            keys = set(a) | set(b)
            changed = [k for k in keys if a.get(k) != b.get(k)]
            if len(changed) != 1:
                continue
            k = changed[0]
            va, vb = a.get(k), b.get(k)
            if str(va) <= str(vb):
                deltas.setdefault((k, va, vb), []).append(ga - gb)
            else:
                deltas.setdefault((k, vb, va), []).append(gb - ga)
    out: list[Duel] = []
    for (k, v1, v2), ds in deltas.items():
        if len(ds) < min_pairs:
            continue
        mean = sum(ds) / len(ds)
        winner, loser = (v1, v2) if mean >= 0 else (v2, v1)
        out.append(Duel(knob=k, winner=winner, loser=loser, mean_delta=abs(mean),
                        pairs=len(ds), metric=metric))
    out.sort(key=lambda d: -d.mean_delta * (1 + min(d.pairs, 8) / 8))
    return out[:top]


def duels_text(duels: list[Duel]) -> str:
    """The prompt block. Verdicts and magnitudes, never prescriptions: the model decides."""
    if not duels:
        return ""
    rows = "\n".join(f"  * {d.describe()}" for d in duels)
    return ("WHAT THE RECORD SHOWS (head-to-head, from controlled one-knob pairs in\n"
            "earlier measurements; larger |effect| first -- verdicts, not instructions):\n"
            + rows)


def laws_text(laws: list[Law]) -> str:
    """The prompt block. Directions and magnitudes, never prescriptions: the model decides."""
    if not laws:
        return ""
    rows = "\n".join(f"  * {k.describe()}" for k in laws)
    return ("WHAT THE RECORD SHOWS (controlled one-knob pairs from earlier measurements;\n"
            "larger |effect| first -- directions, not instructions):\n" + rows)
