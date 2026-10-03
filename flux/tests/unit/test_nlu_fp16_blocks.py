"""The nlu's verified FP16 blocks (D804): `applications/nlu/knowledge/fp16_blocks.py` is what a
prototype copies, so it is exact where its docstring says it is -- the decode and the rounding
on all 65,536 patterns, the rounding of wide values and of every kind of tie against numpy's
(float64 -> float16 rounds once, to nearest even) -- and stays inside the generic prototype's
rules (no float work reachable from design())."""

from __future__ import annotations

import importlib.util
import random
from fractions import Fraction
from pathlib import Path

import numpy as np

BLOCKS = Path(__file__).resolve().parents[2] / "applications" / "nlu" / "knowledge" / "fp16_blocks.py"


def _blocks():
    spec = importlib.util.spec_from_file_location("fp16_blocks", BLOCKS)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _f16(v: float) -> int:
    with np.errstate(all="ignore"):
        return int(np.float16(v).view(np.uint16))


def test_unpack_is_exact_and_round_packs_back_on_every_pattern():
    b = _blocks()
    for x in range(1 << 16):
        s, e, f = b.fp16_fields(x)
        assert (s << 15 | e << 10 | f) == x
        if e == 31:
            continue                                   # Inf and NaN: the classes, decided first
        if e == 0 and f == 0:
            assert b.fp16_round(s, 0, 0, 12) == x      # a zero keeps its sign
            continue
        s, m, k = b.fp16_unpack(x)
        assert 1024 <= m < 2048
        assert m * 2.0 ** k == abs(float(np.uint16(x).view(np.float16)))
        for W in (12, 16, 24):
            assert b.fp16_round(s, m, k, W) == x, (hex(x), W)


def test_round_matches_numpy_on_wide_values_and_ties():
    b = _blocks()
    rng = random.Random(804)
    for _ in range(100_000):
        W = rng.choice((12, 14, 16, 20, 24, 32, 40))
        q = rng.getrandbits(rng.randint(1, W))
        k = rng.randint(-24 - W - 4, 16)               # below the least subnormal to past the overflow
        s = rng.getrandbits(1)
        want = _f16((-1) ** s * q * 2.0 ** k) if q else s << 15
        assert b.fp16_round(s, q, k, W) == want, (s, q, k, W)
    # a single 1 just below the 11 kept bits (an exact tie for a normal), in every regime
    for keep in (1024, 1025, 1026, 2047, 1, 2, 3, 1023):
        for k in (-30, -27, -25, -20, 0, 5, 4):
            W = 16
            q = (keep << 5) | (1 << 4)
            want = _f16(q * 2.0 ** k)
            assert b.fp16_round(0, q, k, W) == want, (keep, k)


def test_horner_and_a_segment_table_reach_their_precision():
    b = _blocks()
    F = 16
    assert b.horner((7 << F, 5 << F, 3 << F), 0, F) == 7 << F          # at t = 0, the constant term
    rng = random.Random(1)
    for _ in range(2000):                              # within a unit per product of the exact value
        cs = tuple(rng.randint(-(1 << 18), 1 << 18) for _ in range(3))
        t = rng.randint(0, (1 << F) - 1)
        exact = sum(Fraction(c, 1 << F) * Fraction(t, 1 << F) ** i for i, c in enumerate(cs))
        assert abs(Fraction(b.horner(cs, t, F), 1 << F) - exact) <= Fraction(3, 1 << F)
    # 1/m on [1, 2): 32 segments of degree 2 hold it far below FP16's 2^-11
    C0, C1, C2 = b.segment_table(lambda v: 1.0 / v, 1.0, 2.0, 32, 2, F)
    assert len(C0) == len(C1) == len(C2) == 32 and all(isinstance(c, int) for c in C0 + C1 + C2)
    worst = 0.0
    for t in range(1024):                              # t = m - 1024, 10 bits: 5 index, 5 position
        j, u = t >> 5, (t & 31) << (F - 5)
        got = b.horner((C0[j], C1[j], C2[j]), u, F) / (1 << F)
        worst = max(worst, abs(got - 1024 / (1024 + t)))
    assert worst < 2.0 ** -14


def test_the_blocks_stay_inside_the_prototype_rules():
    from flux_loop.golden_proto import float_work

    design = ("\n\ndef design(x):\n"
              "    s, m, k = fp16_unpack(x)\n"
              "    return {'y': fp16_round(s, horner((m, 0), 0, 16), k, 16)}\n")
    assert float_work(BLOCKS.read_text() + design) == []
