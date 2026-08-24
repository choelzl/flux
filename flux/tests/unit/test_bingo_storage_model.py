"""The Bingo storage model in `applications/prefetcher/bingo.py`, checked against `inc/bingo.h`'s
own worked examples.

`storage_bytes` is the shrink phase's whole objective, so a quiet error would make the study optimise the
wrong number. Testing against the shipped `score_memory.py` would only prove one was copied from
the other; bingo.h's worked byte counts are independent ground truth:

    line  86  Filter Table        size * (37 - lg(sets) + 5 + 16 + 1 + lg(ways))
              64 * (37 - lg(4) + 5 + 16 + 1 + lg(16))                = 488 Bytes
    line 182  Accumulation Table  size * (37 - lg(sets) + 32 + 5 + 16 + 1 + lg(ways))
              128 * (37 - lg(8) + 32 + 5 + 16 + 1 + lg(16))          = 1472 Bytes
    line 326  Pattern History     size * (32 - lg(sets) + 32 + 1 + lg(ways))
    line 423  Prefetch Streamer   size * (53 - lg(sets) + 64 + 1 + lg(ways))
              128 * (53 - lg(8) + 64 + 1 + lg(16))                   = 1904 Bytes

37 = `48 - lg(2048)`, 53 = `64 - lg(2048)`, the PHT key 32 = `pc_width + max_addr_width`, and the
streamer payload 64 = `2 bits x pattern_len`.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

BINGO = Path(__file__).resolve().parents[2] / "applications" / "prefetcher" / "bingo.py"
_spec = importlib.util.spec_from_file_location("bingo_model", BINGO)
bingo = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bingo)
FIXED_WAYS, storage_bits, storage_bytes, table_bits = bingo.FIXED_WAYS, bingo.storage_bits, bingo.storage_bytes, bingo.table_bits

#: the shipped `bingo.ini`, as ChampSim reads it (pattern_len and pht_size derived)
DEFAULT = {"bingo_region_size": 2048, "bingo_pattern_len": 32, "bingo_pc_width": 16, "bingo_min_addr_width": 5,
           "bingo_max_addr_width": 16, "bingo_ft_size": 64, "bingo_at_size": 128, "bingo_pf_streamer_size": 128,
           "bingo_pht_size": 4096, "bingo_pht_ways": 16}


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
    halved = {**DEFAULT, "bingo_region_size": 1024, "bingo_pattern_len": 16}
    assert storage_bits(halved) > 0
    wider = table_bits(64, FIXED_WAYS, 48 - 10, 4 + 16)
    assert wider == 64 * (38 - 2 + 4 + 16 + 1 + 4)


def test_storage_is_monotone_in_table_size():
    """A bigger table costs more. Stage 2 descends on this, so a non-monotone model would misrank."""
    bigger = {**DEFAULT, "bingo_pht_size": DEFAULT["bingo_pht_size"] * 2}
    assert storage_bytes(bigger) > storage_bytes(DEFAULT)


def test_an_illegal_configuration_is_refused_before_the_simulator():
    """bingo.cc aborts on these; `bingo.py check` must refuse them with the reason."""
    legal = {**DEFAULT, "bingo_l2c_thresh": 0.8}
    assert bingo.invalid_reason(legal) is None
    for patch, why in [({"bingo_pattern_len": 16}, "bingo.cc aborts"),
                       ({"bingo_min_addr_width": 20}, "max_addr_width < min_addr_width"),
                       ({"bingo_pc_width": 0, "bingo_min_addr_width": 0}, "must exceed 0"),
                       ({"bingo_pht_ways": 3}, "do not divide into 3 ways"),
                       ({"bingo_l2c_thresh": 1.5}, "outside 0..1")]:
        assert why in (bingo.invalid_reason({**legal, **patch}) or ""), patch
