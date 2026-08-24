"""Walking the design space: one local-search primitive, and the moves it walks with.

`flow.py` used to carry four hand-written loops -- climb Bingo's knobs, compose partners in, tune
the partners' knobs, shrink storage -- each with its own wave, its own patience counter and its
own stopping rule, written months apart and agreeing with each other by accident. They are one
loop. What differs between them is only:

    * what MOVES are reachable from the current best (a different neighbourhood), and
    * what BETTER means (higher speedup; smaller storage that still clears a floor).

`climb` is that loop. Each phase is a neighbourhood function plus an ordering, and the bugs that
each bespoke loop had found separately -- marking candidates seen before measuring them, taking
the head of an enumeration instead of a spread, giving up on the first flat round -- are fixed in
one place and cannot drift apart again.

A `Design` is the unit throughout: Bingo's knobs, the L2 stack, and the partners' knobs. Two
designs that differ in any of the three are different, and `seen` is keyed on all three, so a
configuration measured with one stack is not mistaken for the same configuration in another.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable

from .config import BingoConfig, storage_bytes
from .partners import defaults_for_stack, knob_moves
from .space import diverse_neighbours, partner_stacks, shrink_moves
from .study import ScoredConfig

#: Bingo's knobs, the L2 stack, and the partners' knobs. What gets measured.
Design = tuple[BingoConfig, tuple[str, ...], dict[str, Any]]

#: Something that measures a wave of designs and scores each one.
Measure = Callable[[list[Design], str], list[ScoredConfig]]


def identity(cfg: BingoConfig, types: tuple[str, ...], knobs: dict[str, Any] | Iterable) -> tuple:
    """What makes two designs the same. All three parts, always."""
    items = knobs.items() if isinstance(knobs, dict) else knobs
    return (cfg, tuple(types), tuple(sorted(items)))


def identity_of(scored: ScoredConfig) -> tuple:
    return identity(scored.config, scored.types, scored.partner_knobs)


def design_of(scored: ScoredConfig) -> Design:
    return (scored.config, scored.types, dict(scored.partner_knobs))


# ---- neighbourhoods -----------------------------------------------------------
def bingo_moves(best: ScoredConfig) -> list[Design]:
    """Change one of Bingo's knobs, round-robin across knobs. Stack and partners unchanged."""
    return [(cfg, best.types, dict(best.partner_knobs)) for cfg in diverse_neighbours(best.config)]


def compose_moves(best: ScoredConfig) -> list[Design]:
    """Add one partner prefetcher to the stack, at that partner's shipped defaults."""
    return [(best.config, stack, {**dict(best.partner_knobs), **defaults_for_stack(stack)})
            for stack in partner_stacks(best.types)]


def partner_moves(best: ScoredConfig) -> list[Design]:
    """Change one of the PARTNERS' knobs, round-robin. Bingo and the stack unchanged."""
    current = dict(best.partner_knobs) or defaults_for_stack(best.types)
    return [(best.config, best.types, move) for move in knob_moves(best.types, current)]


def shrink_spread(best: ScoredConfig, k: int) -> list[Design]:
    """`k` designs that are smaller than `best`, spread across the storage range.

    A spread, not the head of the list: `shrink_moves` is sorted smallest-first, so the first k
    are the most aggressive cuts, which mostly fail the floor and end the descent in one round.
    Evenly spaced samples bracket the axis.
    """
    candidates = shrink_moves(best.config)
    if len(candidates) > k > 1:
        candidates = [candidates[round(i * (len(candidates) - 1) / (k - 1))] for i in range(k)]
    picked = list(dict.fromkeys(candidates[:k]))
    return [(cfg, best.types, dict(best.partner_knobs)) for cfg in picked]


# ---- the primitive -------------------------------------------------------------
@dataclass
class Climb:
    """What one local search did."""

    best: ScoredConfig
    scored: list[ScoredConfig]          # everything measured, including the non-improving
    spent: int
    stopped: str                        # why it ended, for the log


def climb(start: ScoredConfig, *, moves: Callable[[ScoredConfig], list[Design]],
          measure: Measure, better: Callable[[ScoredConfig, ScoredConfig], bool],
          seen: set, budget: int, wave_size: int, patience: int, provenance: str,
          admit: Callable[[ScoredConfig], str | None] | None = None,
          refuse: Callable[[ScoredConfig, str], None] | None = None,
          log: Callable[[str], None] = lambda _m: None, name: str = "climb") -> Climb:
    """Local search from `start`: measure a wave of moves, keep the best, repeat.

    `better(candidate, best)` decides what the search is FOR -- higher speedup, or smaller storage.
    `admit(candidate)` returns why a measured design may not be kept at all (below a floor), which
    is a refusal recorded via `refuse`, not a low rank: a constraint is not a preference.

    Candidates are marked in `seen` only when they are actually measured. Marking everything
    `moves` returned, and then measuring a slice, is how an earlier version burned 125 neighbours
    to measure five and reported "no unexplored neighbours" after touching 4% of the space.
    """
    best, scored, spent, flat = start, [], 0, 0
    stopped = "budget spent"
    while spent < budget and flat < patience:
        fresh = [d for d in moves(best) if identity(*d) not in seen]
        wave = fresh[: min(wave_size, budget - spent)]
        if not wave:
            stopped = "no unexplored move left"
            log(f"  {name}: {stopped}")
            break
        seen.update(identity(*d) for d in wave)
        got = measure(wave, provenance)
        spent += len(wave)

        kept = []
        for candidate in got:
            why = admit(candidate) if admit else None
            if why is None:
                kept.append(candidate)
            elif refuse:
                refuse(candidate, why)
        scored.extend(kept)

        winner = max(kept, key=lambda s: s.geomean_speedup) if kept else None
        improved = [c for c in kept if better(c, best)]
        if improved:
            best = max(improved, key=lambda s: s.geomean_speedup)
            flat = 0
            log(f"  {name}: {len(wave)} measured, best now {best.geomean_speedup:.4f} "
                f"({best.storage_bytes} B)")
        else:
            flat += 1
            log(f"  {name}: {len(wave)} measured, none improved "
                f"({flat}/{patience} flat rounds)"
                + (f"; best of wave {winner.geomean_speedup:.4f}" if winner else ""))
    else:
        if flat >= patience:
            stopped = f"{patience} flat round(s)"
    return Climb(best=best, scored=scored, spent=spent, stopped=stopped)


# ---- what "better" means, per phase --------------------------------------------
def faster(candidate: ScoredConfig, best: ScoredConfig) -> bool:
    return candidate.geomean_speedup > best.geomean_speedup


def faster_by(margin: float) -> Callable[[ScoredConfig, ScoredConfig], bool]:
    """Better only if the gain clears `margin` -- for moves whose cost is a whole prefetcher."""
    return lambda candidate, best: candidate.geomean_speedup - best.geomean_speedup >= margin


def smaller(candidate: ScoredConfig, best: ScoredConfig) -> bool:
    return candidate.storage_bytes < best.storage_bytes


def holds_floor(floor: float) -> Callable[[ScoredConfig], str | None]:
    """The admission rule for shrinking: below the floor is refused, never ranked."""
    def admit(candidate: ScoredConfig) -> str | None:
        if candidate.geomean_speedup < floor:
            return (f"below the retention floor: geomean {candidate.geomean_speedup:.4f} "
                    f"< {floor:.4f}")
        return None
    return admit


__all__ = [
    "Climb", "Design", "Measure", "bingo_moves", "climb", "compose_moves", "design_of",
    "faster", "faster_by", "holds_floor", "identity", "identity_of", "partner_moves",
    "shrink_spread", "smaller", "storage_bytes",
]
