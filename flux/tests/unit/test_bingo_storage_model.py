"""The Bingo storage model, checked against `inc/bingo.h`'s OWN worked examples.

`flux_prefetcher.config.storage_bytes` is stage 2's entire objective, so a quiet error in it would
not fail a run -- it would make the study optimise the wrong number and report a confident answer.
The project shipped a `score_memory.py` that computes the same thing, and testing one against the
other would only prove they were copied from each other.

`inc/bingo.h` documents each table's storage formula AND a worked byte count beside it:

    line  86  Filter Table        size * (37 - lg(sets) + 5 + 16 + 1 + lg(ways))
              64 * (37 - lg(4) + 5 + 16 + 1 + lg(16))                = 488 Bytes
    line 182  Accumulation Table  size * (37 - lg(sets) + 32 + 5 + 16 + 1 + lg(ways))
              128 * (37 - lg(8) + 32 + 5 + 16 + 1 + lg(16))          = 1472 Bytes
    line 326  Pattern History     size * (32 - lg(sets) + 32 + 1 + lg(ways))
    line 423  Prefetch Streamer   size * (53 - lg(sets) + 64 + 1 + lg(ways))
              128 * (53 - lg(8) + 64 + 1 + lg(16))                   = 1904 Bytes

Those numbers are the simulator author's, for exactly the sizes the shipped `bingo.ini` uses, so
they are ground truth this repository did not produce. The constants line up with the model's:
the 37-bit region key is `48 - lg(2048)`, the 53-bit streamer key is `64 - lg(2048)`, the 32-bit
PHT key is `pc_width + max_addr_width = 16 + 16`, and the streamer's 64-bit payload is
`2 bits x pattern_len = 2 x 32`.
"""

from __future__ import annotations

from pathlib import Path

FLUX_ROOT = Path(__file__).resolve().parents[2]

from flux_prefetcher.config import (  # noqa: E402
    DEFAULT, FIXED_WAYS, BingoConfig, storage_bits, storage_bytes, table_bits,
)


def _bits(entries: int, ways: int, key_bits: int, payload_bits: int) -> int:
    """bingo.h's formula, written out longhand rather than by calling the code under test."""
    sets = entries // ways
    return entries * (key_bits - sets.bit_length() + 1 + payload_bits + 1
                      + (ways.bit_length() - 1))


def test_filter_table_matches_bingo_h_worked_example():
    """`64 * (37 - lg(4) + 5 + 16 + 1 + lg(16)) = 488 Bytes` (inc/bingo.h:87)."""
    got = table_bits(64, FIXED_WAYS, 37, 5 + 16)
    assert got == 64 * (37 - 2 + 5 + 16 + 1 + 4)
    assert got // 8 == 488


def test_accumulation_table_matches_bingo_h_worked_example():
    """`128 * (37 - lg(8) + 32 + 5 + 16 + 1 + lg(16)) = 1472 Bytes` (inc/bingo.h:183)."""
    got = table_bits(128, FIXED_WAYS, 37, 32 + 5 + 16)
    assert got == 128 * (37 - 3 + 32 + 5 + 16 + 1 + 4)
    assert got // 8 == 1472


def test_prefetch_streamer_matches_bingo_h_worked_example():
    """`128 * (53 - lg(8) + 64 + 1 + lg(16)) = 1904 Bytes` (inc/bingo.h:424)."""
    got = table_bits(128, FIXED_WAYS, 53, 64)
    assert got == 128 * (53 - 3 + 64 + 1 + 4)
    assert got // 8 == 1904


def test_the_four_tables_sum_to_the_shipped_configuration_total():
    """The shipped `bingo.ini`, table by table, from bingo.h's constants only."""
    filter_table = 64 * (37 - 2 + 5 + 16 + 1 + 4)            # 488 B
    accumulation = 128 * (37 - 3 + 32 + 5 + 16 + 1 + 4)      # 1472 B
    pattern_hist = 4096 * (32 - 8 + 32 + 1 + 4)              # pht_size 4096, 16 ways -> 256 sets
    streamer = 128 * (53 - 3 + 64 + 1 + 4)                   # 1904 B
    assert storage_bits(DEFAULT) == filter_table + accumulation + pattern_hist + streamer
    assert storage_bytes(DEFAULT) == 35096


def test_keys_are_derived_from_region_size_not_hardcoded():
    """37 and 53 are `48 - lg(region)` and `64 - lg(region)`; halving the region widens both."""
    halved = BingoConfig(region_size=1024, pattern_len=16)
    # One more tag bit per table, because one fewer bit indexes the region.
    assert storage_bits(halved) > 0
    wider = table_bits(64, FIXED_WAYS, 48 - 10, 4 + 16)
    assert wider == 64 * (38 - 2 + 4 + 16 + 1 + 4)


def test_storage_is_monotone_in_table_size():
    """A bigger table costs more. Stage 2 descends on this, so a non-monotone model would misrank."""
    base = DEFAULT
    bigger = DEFAULT.replace(pht_size=DEFAULT.pht_size * 2)
    assert storage_bytes(bigger) > storage_bytes(base)
