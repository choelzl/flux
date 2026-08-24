"""Every configuration the search can reach must be one the simulator accepts.

This is not a style check. `prefetcher/bingo.cc` opens with

    assert((knob::bingo_region_size >> LOG2_BLOCK_SIZE) == knob::bingo_pattern_len);

so an illegal configuration does not score badly -- it aborts, six minutes into a six-minute run,
and a search that generates them burns its budget discovering its own generator is broken. The
interconnect study hit the same class of bug twice (D313, D319) with proposals that named a shape
the fabric could not have.
"""

from __future__ import annotations

import random
from pathlib import Path

FLUX_ROOT = Path(__file__).resolve().parents[2]

from flux_prefetcher.config import (  # noqa: E402
    BLOCK_SIZE, DEFAULT, BingoConfig, InvalidConfig, is_valid, invalid_reason, storage_bytes,
    validate,
)
from flux_prefetcher.space import (  # noqa: E402
    REGION_SIZES, TABLE_SIZES, neighbours, random_config, shrink_moves, with_region,
)


def test_every_sampled_configuration_is_legal():
    rng = random.Random(20260827)
    sampled = [random_config(rng) for _ in range(500)]
    illegal = [(c, invalid_reason(c)) for c in sampled if not is_valid(c)]
    assert not illegal, f"{len(illegal)} of 500 sampled configurations are illegal: {illegal[:3]}"


def test_every_neighbour_is_legal_from_several_starting_points():
    rng = random.Random(11)
    for start in [DEFAULT] + [random_config(rng) for _ in range(12)]:
        for candidate in neighbours(start):
            assert is_valid(candidate), f"{start} -> {candidate}: {invalid_reason(candidate)}"


def test_a_region_move_carries_pattern_len_with_it():
    """The knob the simulator asserts on. They are one axis, not two."""
    for region in REGION_SIZES:
        moved = with_region(DEFAULT, region)
        assert moved.pattern_len == region // BLOCK_SIZE
        assert is_valid(moved)


def test_changing_region_size_alone_is_rejected():
    """The failure this space exists to prevent, stated as a test rather than trusted."""
    broken = DEFAULT.replace(region_size=1024)          # pattern_len left at 32
    assert not is_valid(broken)
    assert "pattern_len" in (invalid_reason(broken) or "")
    assert "ABORT" in (invalid_reason(broken) or "")


def test_shrink_moves_only_shrink():
    """Stage 2 descends on storage; a move that grows would make the descent non-monotone."""
    rng = random.Random(3)
    for start in [DEFAULT] + [random_config(rng) for _ in range(8)]:
        budget = storage_bytes(start)
        for candidate in shrink_moves(start):
            assert storage_bytes(candidate) < budget
            assert is_valid(candidate)


def test_table_sizes_are_all_sixteen_way_legal():
    """FT, AT and the streamer are hard-coded 16-way in bingo.h; sets must stay a power of two."""
    for size in TABLE_SIZES:
        assert size % 16 == 0
        sets = size // 16
        assert sets & (sets - 1) == 0


def test_illegal_configurations_explain_themselves():
    """A refusal a proposer cannot read is a refusal it cannot learn from."""
    cases = [
        (BingoConfig(region_size=2048, pattern_len=16), "pattern_len"),
        (BingoConfig(min_addr_width=20, max_addr_width=4), "max_addr_width"),
        (BingoConfig(pc_width=0, min_addr_width=0), "must exceed 0"),
        (BingoConfig(ft_size=17), "do not divide into 16 ways"),
    ]
    for cfg, expected in cases:
        reason = invalid_reason(cfg)
        assert reason is not None, f"{cfg} should be illegal"
        assert expected in reason, f"reason for {cfg} was {reason!r}, wanted {expected!r}"


def test_validate_raises_and_is_valid_does_not():
    try:
        validate(DEFAULT.replace(pattern_len=1))
    except InvalidConfig:
        pass
    else:
        raise AssertionError("validate should have raised")
    assert is_valid(DEFAULT)


def test_shrink_moves_are_ordered_by_what_they_save():
    """The ordering stage 2 depends on, and the bug it was introduced to fix.

    `neighbours()` yields by knob, region size first. Stage 2 can only afford a handful of
    measurements per round, so taking the head of that list spent every round on region-size
    variants. For a PHT-dominated configuration -- and the PHT is typically ~94% of Bingo's
    storage -- the 94%-saving move sat ninth in the enumeration and was never measured, while
    0.1%-saving moves were.
    """
    from flux_prefetcher.config import storage_bytes as sb

    heavy = BingoConfig(region_size=2048, pattern_len=32, pc_width=20, min_addr_width=4,
                        max_addr_width=18, ft_size=32, at_size=64, pht_size=16384,
                        pht_ways=32, pf_streamer_size=512, l2c_thresh=0.3)
    moves = shrink_moves(heavy)
    assert moves, "a large configuration must have smaller neighbours"
    sizes = [sb(m) for m in moves]
    assert sizes == sorted(sizes), "shrink_moves must be ordered smallest-first"
    assert all(s < sb(heavy) for s in sizes)


def test_the_dominant_knob_is_reachable_within_one_wave():
    """Stage 2 samples a spread across `shrink_moves`; that spread must reach the big lever.

    Checked as a property of the ORDERING rather than by importing the flow: the first element is
    the largest available saving, so any spread that includes index 0 sees it.
    """
    from flux_prefetcher.config import storage_bytes as sb

    heavy = BingoConfig(region_size=2048, pattern_len=32, pc_width=20, min_addr_width=4,
                        max_addr_width=18, ft_size=32, at_size=64, pht_size=16384,
                        pht_ways=32, pf_streamer_size=512, l2c_thresh=0.3)
    moves = shrink_moves(heavy)
    best = moves[0]
    assert sb(best) == min(sb(m) for m in moves)
    # For this configuration the PHT is ~94% of storage, so the biggest saving must touch it.
    changed = [f for f in heavy.__dataclass_fields__ if getattr(best, f) != getattr(heavy, f)]
    assert "pht_size" in changed, f"the largest saving changed {changed}, not the dominant table"
    assert sb(best) < 0.2 * sb(heavy), "the largest single saving here should exceed 80%"


def test_diverse_neighbours_covers_knobs_before_repeating_one():
    """The hill-climb measures ~6 neighbours per round out of 125.

    Taking the head of `neighbours()` gave six region-size variants every round, so ten of the
    eleven knobs were never tried and the climb stopped on a "local optimum" it had only tested
    one axis of. Round-robin means a six-wide wave touches six different knobs.
    """
    from flux_prefetcher.space import diverse_neighbours

    ordered = diverse_neighbours(DEFAULT)
    assert len(ordered) == len(list(neighbours(DEFAULT))), "reordering must not drop candidates"

    def knob_of(candidate):
        changed = [f for f in DEFAULT.__dataclass_fields__
                   if getattr(candidate, f) != getattr(DEFAULT, f)]
        return "region_size" if "region_size" in changed else changed[0]

    first_six = [knob_of(c) for c in ordered[:6]]
    assert len(set(first_six)) == 6, f"a six-wide wave still repeats a knob: {first_six}"
    assert all(is_valid(c) for c in ordered)


def test_the_l1d_threshold_stays_above_one():
    """Not a tuning choice — a crash workaround.

    The three Bingo thresholds are a cascade (bingo.cc:323): confidence above `l1d_thresh` fills
    into L1D, else L2, else LLC. Confidence never exceeds 1.0, so the shipped 1.01 makes the L1D
    branch unreachable. Lowering it makes an L2 prefetcher issue L1 fills, which this ChampSim
    fork aborts on — `[L1D_MSHR] cannot find a matching entry`, `cache.cc:1627 Assertion 0
    failed` — on both the L1D-disabled and L1D-enabled builds.

    If a future search ever varies this knob, it must keep it above 1.0 or handle the abort.
    """
    from flux_prefetcher.config import FIXED_INI

    assert float(FIXED_INI["bingo_l1d_thresh"]) > 1.0
    assert FIXED_INI["bingo_pc_address_fill_level"] == "L2"
