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
from typing import Any, Callable, Iterable, Mapping

__all__ = ["Duel", "Law", "READ_BACK_FRAMING", "controlled_pairs", "duels_text", "head_to_head",
           "laws_text", "numeric_knobs", "record_read_back",
           "pairwise_laws", "read_back"]


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


def numeric_knobs(candidate: Mapping[str, Any]) -> dict[str, float]:
    """The flat numeric view of a candidate's parameters (assignment sub-dicts flattened to
    `outer.inner`): the keys a controlled pair may differ on. Non-numeric entries are ignored,
    never coerced; booleans are not numbers here (D444)."""
    out: dict[str, float] = {}
    for k, v in candidate.items():
        if k == "arch":
            continue
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            out[k] = float(v)
        elif isinstance(v, dict):
            for kk, vv in v.items():
                if isinstance(vv, (int, float)) and not isinstance(vv, bool):
                    out[f"{k}.{kk}"] = float(vv)
    return out


def controlled_pairs(known: list[tuple[Mapping[str, Any], Any]], *,
                     coupled: Iterable[frozenset[str]] = (), numeric: bool = False
                     ) -> list[tuple[str, tuple[Mapping[str, Any], Any], tuple[Mapping[str, Any], Any]]]:
    """Every pair of `known` entries whose knob dicts differ in exactly ONE knob (or in
    exactly one `coupled` set, reported under the set's first name), as `(knob, a, b)` in
    input order (D444) -- the paid-for experiment a law, a duel and a mined ratio all read.
    `numeric` compares values as floats (a missing knob reads as 0), else by equality."""
    coupled = [frozenset(c) for c in coupled]
    out = []
    for i, (a, ga) in enumerate(known):
        for b, gb in known[i + 1:]:
            keys = set(a) | set(b)
            if numeric:
                changed = [k for k in keys if float(a.get(k, 0)) != float(b.get(k, 0))]
            else:
                changed = [k for k in keys if a.get(k) != b.get(k)]
            knob = None
            if len(changed) == 1:
                knob = changed[0]
            else:
                for grp in coupled:
                    if set(changed) == grp:
                        knob = sorted(grp)[0]
                        break
            if knob is not None:
                out.append((knob, (a, ga), (b, gb)))
    return out


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
    deltas: dict[str, list[float]] = {}
    for knob, (a, ga), (b, gb) in controlled_pairs(known, coupled=coupled, numeric=True):
        probe = knob if knob in a and knob in b and float(a.get(knob, 0)) != float(b.get(knob, 0)) \
            else next(k for k in set(a) | set(b) if float(a.get(k, 0)) != float(b.get(k, 0)))
        lo, hi = ((a, ga), (b, gb)) if float(a.get(probe, 0)) < float(b.get(probe, 0)) else ((b, gb), (a, ga))
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
                 min_pairs: int = 2, top: int = 6, metric: str = "metric",
                 higher_is_better: bool = True) -> list[Duel]:
    """Head-to-head verdicts from every pair of configs differing in ONE knob.

    Values are compared by equality, so categorical knobs work; each (knob, value,
    value) matchup is accumulated in a canonical order so A-vs-B and B-vs-A pairs
    pool their evidence. Matchups with fewer than `min_pairs` pairs are withheld --
    one pair is an anecdote -- and the result is the `top` strongest by |mean
    effect| weighted by evidence, weakest last.

    `higher_is_better` is which way the metric runs (D449). Every caller until then
    read a metric where more is better (megahertz, throughput, speedup) and the
    winner was simply the larger mean -- so the first cost-shaped read-back (XOR
    gates, area, storage) would have announced the EXPENSIVE value as the winner."""
    deltas: dict[tuple[str, object, object], list[float]] = {}
    for k, (a, ga), (b, gb) in controlled_pairs(known):
        va, vb = a.get(k), b.get(k)
        if str(va) <= str(vb):
            deltas.setdefault((k, va, vb), []).append(ga - gb)
        else:
            deltas.setdefault((k, vb, va), []).append(gb - ga)
    out: list[Duel] = []
    sign = 1.0 if higher_is_better else -1.0
    for (k, v1, v2), ds in deltas.items():
        if len(ds) < min_pairs:
            continue
        mean = sum(ds) / len(ds)
        winner, loser = (v1, v2) if sign * mean >= 0 else (v2, v1)
        out.append(Duel(knob=k, winner=winner, loser=loser, mean_delta=abs(mean),
                        pairs=len(ds), metric=metric))
    out.sort(key=lambda d: -d.mean_delta * (1 + min(d.pairs, 8) / 8))
    return out[:top]


READ_BACK_FRAMING = "this campaign's earlier runs; directions, not instructions"


def read_back(lines: Iterable[str], *, framing: str = READ_BACK_FRAMING) -> str:
    """The one prompt block the record is read back through (D429): a fixed header
    naming what the lines are and what they are not, then one bullet per line; no
    lines, no block. Every application's `_record_context` and the extractor's own
    duels and laws render through here, so the vocabulary cannot drift."""
    rows = [f"  * {ln}" for ln in lines if ln]
    if not rows:
        return ""
    return f"WHAT THE RECORD SHOWS ({framing}):\n" + "\n".join(rows)


def duels_text(duels: list[Duel]) -> str:
    """The prompt block. Verdicts and magnitudes, never prescriptions: the model decides."""
    return read_back((d.describe() for d in duels),
                     framing="head-to-head, from controlled one-knob pairs in\n"
                             "earlier measurements; larger |effect| first -- verdicts, not "
                             "instructions")


def laws_text(laws: list[Law]) -> str:
    """The prompt block. Directions and magnitudes, never prescriptions: the model decides."""
    return read_back((k.describe() for k in laws),
                     framing="controlled one-knob pairs from earlier measurements;\n"
                             "larger |effect| first -- directions, not instructions")


def record_read_back(records: Any, *, stage: str, metric: str, knobs: Iterable[str] | None = None,
                     metric_label: str | None = None, top: int = 5, higher_is_better: bool = True,
                     conclusion: "Callable[[dict[str, Any]], str | None] | None" = None,
                     extra: "Callable[[Any], list[str]] | None" = None,
                     framing: str = READ_BACK_FRAMING) -> str:
    """The whole read-back of a resumed campaign for a prompt (D445): earlier conclusions
    (through `conclusion`, the study's own wording), the head-to-head verdicts over `knobs`
    (every candidate key when None) on `metric` at `stage`, then the study's `extra` lines --
    as one `read_back` block, "" for a fresh or record-less run. Three studies had written
    this skeleton; each now supplies only what is its own."""
    if records is None or not getattr(records, "resumed", False):
        return ""
    lines: list[str] = []
    if conclusion is not None:
        for c in records.conclusions(limit=2):
            said = conclusion(c)
            if said:
                lines.append(said)
    known = records.known(stage=stage, metric=metric, higher_is_better=higher_is_better)
    keys = list(knobs) if knobs is not None else None
    pairs = [({k: c.get(k) for k in (keys or c.keys())}, v) for c, v in known]
    lines += [d.describe() for d in head_to_head(pairs, metric=metric_label or metric, top=top,
                                                 higher_is_better=higher_is_better)]
    if extra is not None:
        lines += list(extra(records))
    return read_back(lines, framing=framing)
