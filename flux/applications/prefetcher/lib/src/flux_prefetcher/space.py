"""The moves a search may make in Bingo's configuration space.

Every generator here produces LEGAL configurations by construction wherever the constraint is
structural, and filters with `is_valid` only where it is not. That split matters. Bingo's knobs
are not independent axes:

  * `pattern_len` is `region_size / 64` and nothing else -- `bingo.cc` asserts it. They are one
    knob wearing two names, so a move that changes one always changes the other.
  * The filter, accumulation and streamer tables are hard-coded 16-way, so their sizes are
    `16 * 2^k` and nothing between.
  * The PHT's size must be `ways * 2^i`, and its tag must survive the index: `pc_width +
    max_addr_width - i >= 0`.
  * `max_addr_width >= min_addr_width`, and `pc_width + min_addr_width > 0`.

A proposer that treats these as eleven free integers produces mostly-illegal candidates, which is
exactly what happened on the interconnect study before its space was written down (D313, D319).
Writing the legal moves once, here, is what keeps a model's suggestions inside the space instead
of teaching it the rules through rejection.
"""

from __future__ import annotations

import random
from typing import Iterator

from .config import BLOCK_SIZE, BingoConfig, MAX_WIDTH, is_valid

#: Legal region sizes: powers of two from one cache block to one page. A region cannot exceed a
#: page because Bingo never prefetches across a page boundary.
REGION_SIZES = tuple(1 << k for k in range(6, 13))          # 64 .. 4096

#: Legal sizes for the three 16-way tables: 16 ways x a power-of-two set count.
TABLE_SIZES = tuple(16 * (1 << k) for k in range(0, 8))     # 16 .. 2048

#: Legal PHT associativities, and set counts. Size is the product.
PHT_WAYS = tuple(1 << k for k in range(0, 6))               # 1 .. 32
PHT_SETS = tuple(1 << k for k in range(0, 13))              # 1 .. 4096

#: `bingo_l2c_thresh` is continuous; a search over floats needs a grid to be reproducible.
L2C_THRESHOLDS = (0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0)


def with_region(cfg: BingoConfig, region_size: int) -> BingoConfig:
    """Change the region size, carrying `pattern_len` with it as the simulator requires."""
    return cfg.replace(region_size=region_size, pattern_len=region_size // BLOCK_SIZE)


def random_config(rng: random.Random) -> BingoConfig:
    """A uniformly-sampled legal configuration.

    Rejection-sampled on the two constraints that are cheaper to test than to construct
    (`max >= min`, and the PHT tag surviving its index); everything else is legal by construction,
    so the loop terminates quickly rather than hunting for a rare valid point.
    """
    for _ in range(1000):
        region = rng.choice(REGION_SIZES)
        min_w = rng.randint(0, 12)
        cfg = BingoConfig(
            region_size=region, pattern_len=region // BLOCK_SIZE,
            pc_width=rng.randint(0, 20),
            min_addr_width=min_w,
            max_addr_width=rng.randint(min_w, min(MAX_WIDTH, min_w + 20)),
            ft_size=rng.choice(TABLE_SIZES),
            at_size=rng.choice(TABLE_SIZES),
            pht_ways=(ways := rng.choice(PHT_WAYS)),
            pht_size=ways * rng.choice(PHT_SETS),
            pf_streamer_size=rng.choice(TABLE_SIZES),
            l2c_thresh=rng.choice(L2C_THRESHOLDS),
        )
        if is_valid(cfg):
            return cfg
    raise RuntimeError("could not sample a legal configuration in 1000 attempts")


def neighbours(cfg: BingoConfig) -> Iterator[BingoConfig]:
    """Every legal one-move change from `cfg`, for a local search.

    "One move" is deliberately not "one field": stepping `region_size` also steps `pattern_len`,
    because a configuration where they disagree is not a neighbour, it is a crash.
    """
    seen = {cfg}

    def offer(candidate: BingoConfig) -> Iterator[BingoConfig]:
        if candidate not in seen and is_valid(candidate):
            seen.add(candidate)
            yield candidate

    for region in REGION_SIZES:
        yield from offer(with_region(cfg, region))
    for size in TABLE_SIZES:
        yield from offer(cfg.replace(ft_size=size))
        yield from offer(cfg.replace(at_size=size))
        yield from offer(cfg.replace(pf_streamer_size=size))
    for ways in PHT_WAYS:
        yield from offer(cfg.replace(pht_ways=ways, pht_size=max(cfg.pht_size, ways)))
    for sets in PHT_SETS:
        yield from offer(cfg.replace(pht_size=cfg.pht_ways * sets))
    for width in range(0, MAX_WIDTH + 1):
        yield from offer(cfg.replace(pc_width=width))
        yield from offer(cfg.replace(min_addr_width=width))
        yield from offer(cfg.replace(max_addr_width=width))
    for thresh in L2C_THRESHOLDS:
        yield from offer(cfg.replace(l2c_thresh=thresh))


def shrink_moves(cfg: BingoConfig) -> list[BingoConfig]:
    """Neighbours that make the hardware SMALLER, ORDERED BY HOW MUCH THEY SAVE.

    The ordering is the point, and it was learned the expensive way. `neighbours()` yields by knob,
    region size first, and stage 2 can only afford to measure a handful per round -- so it spent
    every round on region-size variants and never reached `pht_size`, which sat ninth in the
    enumeration. For a typical stage-1 winner the pattern history table is 94% of the storage
    (137,216 of 145,744 bytes), so the search was proposing 0.1%-saving moves while a 94% saving
    stood one move away, unmeasured. The reported answer was 24% smaller when it could have been
    far smaller, and nothing in the output said so.

    Sorted smallest-first, so a caller taking a spread across this list brackets the whole storage
    axis instead of sampling one knob. Storage is analytic and free, so ordering 61 candidates
    costs microseconds against the six minutes each measurement costs.
    """
    from .config import storage_bytes

    budget = storage_bytes(cfg)
    smaller = [c for c in neighbours(cfg) if storage_bytes(c) < budget]
    return sorted(smaller, key=storage_bytes)


def diverse_neighbours(cfg: BingoConfig) -> list[BingoConfig]:
    """`neighbours`, reordered so the first k touch k DIFFERENT knobs.

    The hill-climb can afford a handful of measurements per round and was taking the head of
    `neighbours()`, which yields region size first -- so every wave was six region-size variants,
    the other ten knobs were never tried, and the climb reported "no neighbour improved" and
    stopped. Raising the budget did not help, because the extra measurements went to the same one
    axis. Round-robin by which field changed fixes the coverage without changing the space.
    """
    buckets: dict[str, list[BingoConfig]] = {}
    for candidate in neighbours(cfg):
        changed = tuple(f for f in cfg.__dataclass_fields__
                        if getattr(candidate, f) != getattr(cfg, f))
        # region_size and pattern_len always move together; name the pair by its driver
        key = "region_size" if "region_size" in changed else (changed[0] if changed else "?")
        buckets.setdefault(key, []).append(candidate)

    out: list[BingoConfig] = []
    while any(buckets.values()):
        for key in list(buckets):
            if buckets[key]:
                out.append(buckets[key].pop(0))
    return out


#: L2 prefetchers the `multi` slot accepts alongside Bingo, best-measured-first.
#:
#: Measured alone against the no-prefetcher baseline on the screen rung
#: (`experiments/prefetcher_family.py`): bingo 1.0607, next_line 1.0378, scooby 1.0366, sms
#: 1.0355, spp_ppf_dev 1.0270, power7 1.0171, ampm 1.0162, mlop 1.0146, streamer 1.0145, sandbox
#: 1.0088, stride 1.0063, spp_dev2 1.0033, ipcp 1.0000, bop 0.9872, dspatch 0.9571.
#:
#: Ordering matters because the search tries them in turn under a budget: a partner that is bad
#: alone is unlikely to help in combination, and the two that are actively HARMFUL alone (bop,
#: dspatch) are excluded rather than measured -- six minutes each to re-learn something already
#: known is the one cost this study cannot afford.
PARTNERS = ("sms", "ampm", "stride", "streamer", "spp_ppf_dev", "power7", "mlop",
            "next_line", "sandbox", "spp_dev2", "ipcp", "scooby")

#: Pairs measured to crash this ChampSim build. Excluded so a budget is not spent rediscovering
#: them, and named so the exclusion is checkable rather than folklore:
#:   bingo+scooby     -> abort (exit 134)
#:   bingo+mlop       -> did not complete
#:   next_line+sms    -> segfault (exit 139)
#:   bingo+next_line  -> segfault (exit 139)
#: A crash is still handled as a refusal at run time; this list only avoids paying for it twice.
KNOWN_UNSTABLE = frozenset({
    frozenset({"bingo", "scooby"}),
    frozenset({"bingo", "mlop"}),
    frozenset({"bingo", "next_line"}),
    frozenset({"next_line", "sms"}),
})


def partner_stacks(current: tuple[str, ...], limit: int = 6) -> list[tuple[str, ...]]:
    """Stacks reachable by adding ONE partner to `current`, skipping known-unstable pairs."""
    out: list[tuple[str, ...]] = []
    for partner in PARTNERS:
        if partner in current:
            continue
        if any(frozenset({partner, existing}) in KNOWN_UNSTABLE for existing in current):
            continue
        out.append(tuple(current) + (partner,))
        if len(out) >= limit:
            break
    return out
