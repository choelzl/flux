"""The NLU toolkit (docs/decisions.md D481): verified blocks a prototype COMPOSES.

Cedric: "lets have the LLM use tools to make the NLU instead of fully inventing RTL from
scratch". Two days of measurement said the same thing: every model got the SHAPE of a function
unit wrong before it got the numerics wrong -- classifying the input, normalising a subnormal,
converting to fixed point, rounding to nearest-even with the carry, denormalising the result --
and those parts are the same for every operator. They are not a design; they are the toolkit
a designer reaches for, the way an HLS math library or FloPoCo's operators are built from a
shared set of primitives. Here each block is:

- a plain function in the D468 integer subset, so the transpiler inlines it and every width it
  uses is measured on the way to RTL (D478);
- contract-tested on its own (`tests/unit/test_nlu_blocks.py`), exhaustively where the domain
  allows, so a prototype that composes them starts from parts that are right;
- documented in one line the prompt carries (`tool_docs`), like a tool list.

What stays the model's: WHICH blocks, in WHAT order, with WHAT parameters -- the algorithm --
and the knobs it leaves to the family search (D479). What stays the flow's: verification of the
whole against the reference, the transpilation, the selection.

This file is prepended to every prototype as text (`PRELUDE`), so it must stay numpy-only and
standalone: no imports from flux, nothing outside the sandbox's allowance (numpy, math).
"""

from __future__ import annotations

import math

import numpy as np

# ------------------------------------------------------------------------- FP16 fields, classes
BIAS = 15
QNAN, PINF, NINF, PZERO, NZERO = 0x7E00, 0x7C00, 0xFC00, 0x0000, 0x8000


def bits(x):
    """the FP16 patterns as int64 (the harness hands design() a uint16 array; every block
    works in int64 so signed arithmetic and `-BIAS` never meet a uint16). Call it on x when
    you compute on x directly; the field and class blocks call it themselves."""
    return np.asarray(x).astype(np.int64)


def fp16_sign(x):
    """sign bit of each FP16 pattern (0/1)."""
    return (bits(x) >> 15) & 1


def fp16_exp(x):
    """biased 5-bit exponent field (0..31)."""
    return (bits(x) >> 10) & 0x1F


def fp16_frac(x):
    """10-bit fraction field."""
    return bits(x) & 0x3FF


def is_zero(x):
    """+-0."""
    return (fp16_exp(x) == 0) & (fp16_frac(x) == 0)


def is_subnormal(x):
    """exponent field 0 with a nonzero fraction."""
    return (fp16_exp(x) == 0) & (fp16_frac(x) != 0)


def is_inf(x):
    """+-Inf."""
    return (fp16_exp(x) == 31) & (fp16_frac(x) == 0)


def is_nan(x):
    """any NaN."""
    return (fp16_exp(x) == 31) & (fp16_frac(x) != 0)


def is_finite_nonzero(x):
    """a normal or subnormal, not zero, not Inf/NaN."""
    return ~is_zero(x) & (fp16_exp(x) != 31)


def leading_zeros10(frac):
    """leading zeros of a 10-bit fraction (0..9); 9 for frac == 0 too (caller handles zero).
    A binary search with CONSTANT shifts and selects (D496: the nine-step chain of variable
    shifts was 72 logic levels on the critical path of four of the seven operators)."""
    v = bits(frac) & 0x3FF
    lz = np.zeros_like(v)
    hi = (v >> 5) == 0                              # the top 5 bits empty: 5 leading zeros
    lz = lz + 5 * hi
    v = np.where(hi, v << 5, v)
    hi = (v >> 7) == 0                              # then 3
    lz = lz + 3 * hi
    v = np.where(hi, v << 3, v)
    hi = (v >> 8) == 0                              # then 2
    lz = lz + 2 * hi
    v = np.where(hi, v << 2, v)
    hi = (v >> 9) == 0                              # then 1
    lz = lz + hi
    return np.minimum(lz, 9)


def mantissa11(x):
    """the 11-bit significand 1.ffffffffff with the hidden one, normalised for subnormals too
    (a subnormal's leading one shifted up to bit 10)."""
    fr = fp16_frac(x)
    ex = fp16_exp(x)
    lz = leading_zeros10(fr)
    return np.where(ex == 0, (fr << (lz + 1)) & 0x7FF, fr | 0x400)


def exponent_unbiased(x):
    """the unbiased exponent so that |x| = mantissa11(x)/1024 * 2^exponent_unbiased(x), for
    normals and (normalised) subnormals alike; signed."""
    fr = fp16_frac(x)
    ex = fp16_exp(x)
    return np.where(ex == 0, -BIAS - leading_zeros10(fr), ex - BIAS)


# ----------------------------------------------------------------------------------- fixed point
def to_fixed(x, frac_bits, max_abs_pow2):
    """|x| as an unsigned fixed-point integer with `frac_bits` fractional bits, truncated;
    saturated at 2^max_abs_pow2 (so the result has max_abs_pow2 + frac_bits + 1 bits).
    Subnormals included."""
    mant = mantissa11(x)
    e = exponent_unbiased(x)
    sh = e + (frac_bits - 10)
    v = np.where(sh >= 0, mant << np.clip(sh, 0, 62), mant >> np.clip(-sh, 0, 62))
    return np.minimum(v, np.int64(1) << (max_abs_pow2 + frac_bits))


def signed_fixed(x, frac_bits, max_abs_pow2):
    """x as a SIGNED fixed-point integer (two's complement), from `to_fixed`."""
    mag = to_fixed(x, frac_bits, max_abs_pow2)
    return np.where(fp16_sign(x) == 1, -mag, mag)


def const_fixed(value, bits):
    """a real constant as an integer with `bits` fractional bits (rounded), for `mul_const`."""
    return int(round(value * (1 << bits)))


def floor_shift(v, n):
    """v // 2^n with floor semantics for negative v (an arithmetic shift)."""
    return v >> n


def low_bits(v, n):
    """the low n bits of v."""
    return v & ((np.int64(1) << n) - 1)


def field(v, hi_bit, lo_bit):
    """bits hi_bit..lo_bit of v, inclusive, as an integer (hi_bit >= lo_bit)."""
    return (v >> lo_bit) & ((np.int64(1) << (hi_bit - lo_bit + 1)) - 1)


def select(cond, a, b):
    """a where cond else b."""
    return np.where(cond, a, b)


# ------------------------------------------------------------------------------------- tables
_FUNCS = {
    "exp2": np.exp2, "exp": np.exp, "ln": np.log, "log2": np.log2,
    "recip": lambda t: 1.0 / t, "rsqrt": lambda t: 1.0 / np.sqrt(t), "sqrt": np.sqrt,
    "sigmoid": lambda t: 1.0 / (1.0 + np.exp(-t)), "tanh": np.tanh,
    # D491: the table oracle had gelu and erf, the prototype's rom did not -- the model
    # vectorised math.erf itself
    "erf": np.vectorize(math.erf, otypes=[np.float64]),
    "gelu": lambda t: 0.5 * t * (1.0 + np.vectorize(math.erf, otypes=[np.float64])(t / math.sqrt(2.0))),
}


# the same names bare (D482: apex wrote `rom(recip, ...)` -- the doc's 'recip' without the
# quotes; either spelling is the same table)
exp2, exp, ln, log2, recip, rsqrt, sqrt, sigmoid, tanh, erf, gelu = (
    _FUNCS[k] for k in ("exp2", "exp", "ln", "log2", "recip", "rsqrt", "sqrt", "sigmoid", "tanh", "erf", "gelu"))


def _func(func):
    """`func` as a callable: a name from _FUNCS ('exp2', 'exp', 'ln', 'log2', 'recip',
    'rsqrt', 'sqrt', 'sigmoid', 'tanh'), quoted or bare, or any callable of a float array
    (`lambda t: 2 / t`)."""
    if callable(func):
        def elementwise_if_needed(t, f=func):
            # D494: a lambda written with math.erf works on one float and dies on the sample
            # array ("only 0-dimensional arrays can be converted to Python scalars") -- a
            # table builder is evaluated once, so apply it per element when it must be
            try:
                return f(t)
            except TypeError:
                return np.vectorize(f, otypes=[np.float64])(t)
        return elementwise_if_needed
    if func not in _FUNCS:
        raise ValueError(f"rom: unknown function name {func!r}; use one of "
                         f"{sorted(_FUNCS)} or pass a callable such as `lambda t: 2 / t`")
    return _FUNCS[func]


def rom(func, lo, hi, entries, frac_bits, sample="left"):
    """a ROM of `func` over [lo, hi) -- `func` one of exp2, exp, ln, log2, recip, rsqrt, sqrt,
    sigmoid, tanh, erf, gelu (bare or as a string) or any callable of a float array (`lambda t: 2 / t`):
    `entries` values (a power of two, <= 1024), entry i sampled at lo + (i + off)*(hi-lo)/entries
    (off 0 left, 0.5 mid, 1 right), rounded to `frac_bits` fractional bits as integers (negative
    values as negative integers). Built once from constants -- it is what the table oracle
    computes -- and indexed with `lookup`."""
    if not isinstance(entries, (int, np.integer)) or not isinstance(frac_bits, (int, np.integer)):
        raise ValueError("rom: `entries` and `frac_bits` must be integer constants (knobs such as "
                         f"2 ** T and M), got {str(type(entries))} and {str(type(frac_bits))}")
    off = {"left": 0.0, "mid": 0.5, "right": 1.0}[sample]
    t = lo + (np.arange(entries) + off) * (hi - lo) / entries
    with np.errstate(all="ignore"):
        v = _func(func)(t.astype(np.float64))
    return np.rint(v * (1 << frac_bits)).astype(np.int64)


def slope_rom(func, lo, hi, entries, frac_bits, sample="left"):
    """per-interval first differences of `func` (a name or a callable, as for `rom`) over
    [lo, hi): f(right edge) - f(left edge), scaled by 2^frac_bits and rounded; for a first-order
    correction with `interp1`. `sample` is accepted for symmetry with `rom` and IGNORED: a
    segment's secant slope does not depend on where the table samples it -- and `interp1`
    counts rem from the segment's LEFT edge, so its table must be rom(..., sample='left')."""
    f = _func(func)
    left = lo + np.arange(entries) * (hi - lo) / entries
    right = left + (hi - lo) / entries
    with np.errstate(all="ignore"):
        d = f(right.astype(np.float64)) - f(left.astype(np.float64))
    return np.rint(d * (1 << frac_bits)).astype(np.int64)


def lookup(table, idx):
    """table[idx]."""
    return table[idx]


def interp1(table, slopes, idx, rem, rem_bits):
    """the COMPLETE interpolated value table[idx] + slopes[idx] * rem / 2^rem_bits -- table[idx]
    is already in it, do not add lookup(table, idx) again; `rem` is the next rem_bits bits of
    the argument below the index (D483: a model added the table value twice)."""
    return table[idx] + ((slopes[idx] * rem) >> rem_bits)


def approx_on_mantissa(func, m, T, M):
    """func(m / 1024) as a Q(M) integer (M fractional bits) for the normalised significand m in
    1024..2047 (from mantissa11): a 2^T-entry ROM of func over [1, 2) indexed by m's top T
    fraction bits, linearly interpolated on the remaining 10 - T bits. All the index
    bookkeeping in one call; the argument reduction and what the exponent becomes stay yours:
    when your operator is g(m) * 2^k(e), compute g here and derive k; if g(m) can leave [1, 2),
    normalize_significand it (or scale it by a power of two) and adjust the exponent by the
    same amount before pack_fp16. T and M are knobs to declare in a SPACE."""
    if not isinstance(T, (int, np.integer)) or not isinstance(M, (int, np.integer)):
        raise ValueError("approx_on_mantissa: T (index bits) and M (fraction bits of the result) "
                         f"must be integer knobs, got {str(type(T))} and {str(type(M))} -- "
                         "declare `T = 6` and `M = 12` at module level, not a table")
    tab = rom(func, 1.0, 2.0, 1 << T, M)
    slp = slope_rom(func, 1.0, 2.0, 1 << T, M)
    frac = bits(m) & 0x3FF
    idx = frac >> (10 - T)
    rem = frac & ((1 << (10 - T)) - 1)
    return interp1(tab, slp, idx, rem, 10 - T)


def recip_fixed(v, frac_bits, out_frac_bits, T=6, M=14):
    """1 / v for a POSITIVE fixed-point v (frac_bits fractional bits) as a fixed-point
    integer with out_frac_bits fractional bits, WITHOUT leaving fixed point (D488: a
    composition that rounds to FP16 between its steps has a precision floor; this is the
    division that keeps the intermediate wide). v is normalised at its leading one, 1/m read
    from a 2^T-entry interpolated table (M fraction bits) and shifted back. v == 0 gives the
    largest representable value. T and M are knobs."""
    v = bits(v)
    pos = leading_one(v)
    K = 24                                                    # the significand kept: 1.xxx in Q23
    sig = np.where(pos >= K - 1, v >> np.clip(pos - (K - 1), 0, 62),
                   v << np.clip((K - 1) - pos, 0, 62)) & ((1 << K) - 1)
    frac = sig & ((1 << (K - 1)) - 1)                         # the 23 bits below the leading one
    tab = rom(recip, 1.0, 2.0, 1 << T, M)
    slp = slope_rom(recip, 1.0, 2.0, 1 << T, M)
    idx = frac >> (K - 1 - T)
    rem = frac & ((1 << (K - 1 - T)) - 1)
    r = interp1(tab, slp, idx, rem, K - 1 - T)               # 1/m in Q(M), m in [1, 2)
    # v = m * 2^pos as an integer, so its value is m * 2^(pos - frac_bits) and
    # 1/value = (1/m) * 2^(frac_bits - pos); to Q(out_frac_bits) that is r shifted by
    sh = out_frac_bits + frac_bits - pos - M
    out = np.where(sh >= 0, r << np.clip(sh, 0, 62), r >> np.clip(-sh, 0, 62))
    return np.where(v == 0, (np.int64(1) << 62) - 1, out)


# ------------------------------------------------------------------------- rounding, packing
def round_rne(v, drop_bits):
    """v / 2^drop_bits rounded to nearest, ties to even (drop_bits may vary per element)."""
    sh = np.clip(np.asarray(drop_bits, dtype=np.int64), 0, 62)
    q = v >> sh
    rem = v & ((np.int64(1) << sh) - 1)
    half = np.where(sh > 0, np.int64(1) << np.maximum(sh - 1, 0), np.int64(0))
    up = np.where(sh > 0, (rem > half) | ((rem == half) & ((q & 1) == 1)), False)
    return q + up.astype(np.int64)


def pack_fp16(sign, e_unb, v, frac_bits):
    """the FP16 pattern of (-1)^sign * v/2^frac_bits * 2^e_unb where v is in [2^frac_bits,
    2^(frac_bits+1)) -- a normalised significand 1.xxx in Q1.frac_bits -- and e_unb is the
    UNBIASED exponent (0 for a value in [1, 2); do not add BIAS, clip or special-case it):
    round-to-nearest-even to 10 fraction bits, the carry into the exponent, overflow to Inf
    and the subnormal path (denormalised with rounding, underflow to 0) all happen inside.
    The one function every operator ends with; wrap only `specials` around it."""
    fb = np.asarray(frac_bits, dtype=np.int64)
    # a significand with FEWER than 11 bits (frac_bits < 10: from_fixed packing a small fixed-
    # point value at its leading one) is shifted LEFT into the field, never rounded (D489: the
    # old round_rne(v, negative) clipped to a no-op and from_fixed(8, 16) gave 0x808 -- every
    # fixed-point sigmoid route died in the small-output band on a toolkit slip)
    mant_q = np.where(fb >= 10, round_rne(v, np.maximum(fb - 10, 0)), bits(v) << np.clip(10 - fb, 0, 62))
    carry = mant_q >= 2048
    mant_n = np.where(carry, 1024, mant_q)
    e_out = e_unb + BIAS + carry.astype(np.int64)
    normal = (e_out << 10) | (mant_n & 0x3FF)
    # subnormal: field = round(v * 2^(e_unb + 24 - frac_bits)), from the unrounded significand;
    # a non-positive shift is a LEFT shift (D485: from_fixed packs at the leading one, where
    # the field can be v itself -- the old "nothing to drop means zero" lost every subnormal)
    sub_sh = frac_bits - 24 - e_unb
    fld = np.where(sub_sh >= 0, round_rne(v, np.clip(sub_sh, 0, 62)),
                   v << np.clip(-sub_sh, 0, 62))
    subn = np.minimum(fld, 0x7FF)                                   # 0x400 = the smallest normal
    mag = np.where(e_out >= 31, PINF, np.where(e_out >= 1, normal, subn))
    return (mag | (sign << 15)).astype(np.int64)


def leading_one(v):
    """the bit position of the leading one of a non-negative integer v (floor(log2 v); 0 for
    v == 0), found in six halving steps -- a leading-zero counter."""
    v = bits(v)
    pos = np.zeros_like(v)
    for i in range(6):                                  # k = 32, 16, 8, 4, 2, 1
        # CONSTANT shifts and a select per step (D496: `v >> (pos + k)` was a barrel shifter
        # per step, 102 logic levels on sigmoid's critical path)
        k = 32 >> i
        step = (v >> k) != 0
        pos = pos + k * step
        v = np.where(step, v >> k, v)
    return pos


def from_fixed(v, frac_bits, sign=0):
    """the FP16 pattern of (-1)^sign * v / 2^frac_bits for a NON-NEGATIVE fixed-point v of
    ANY magnitude (v == 0 gives a signed zero): the leading one is located and the value packed
    at that position, so pack_fp16's rounding is the only rounding. The general "a fixed-point
    result to FP16" step; pack_fp16 is its special case for a value already in [1, 2)."""
    v = bits(v)
    pos = leading_one(v)
    y = pack_fp16(sign, pos - frac_bits, v, pos)
    return np.where(v == 0, bits(sign) << 15, y)


def normalize_significand(v, frac_bits):
    """(v', shift): v shifted left until it is in [2^frac_bits, 2^(frac_bits+1)) for v in
    (0, 2^(frac_bits+1)); the shift to subtract from the exponent. Handles up to 3 leading
    zero positions (v >= 2^(frac_bits-3)); use before `pack_fp16` when a result may be < 1."""
    s1 = v < (np.int64(1) << frac_bits)
    v1 = np.where(s1, v << 1, v)
    s2 = v1 < (np.int64(1) << frac_bits)
    v2 = np.where(s2, v1 << 1, v1)
    s3 = v2 < (np.int64(1) << frac_bits)
    v3 = np.where(s3, v2 << 1, v2)
    return v3, s1.astype(np.int64) + s2.astype(np.int64) + s3.astype(np.int64)


def specials(x, nan_out, pinf_out, ninf_out, pzero_out, nzero_out, y):
    """y for finite nonzero x; the given patterns for NaN, +Inf, -Inf, +0, -0 inputs."""
    out = np.where(is_nan(x), nan_out, y)
    out = np.where(is_inf(x) & (fp16_sign(x) == 0), pinf_out, out)
    out = np.where(is_inf(x) & (fp16_sign(x) == 1), ninf_out, out)
    out = np.where(is_zero(x) & (fp16_sign(x) == 0), pzero_out, out)
    out = np.where(is_zero(x) & (fp16_sign(x) == 1), nzero_out, out)
    return out


# ------------------------------------------------------------------ FP16 arithmetic (D487)
# IEEE-754 half precision, round to nearest even, every special handled -- the operators a
# composition needs: sigmoid = recip(1 + exp(-x)), tanh = 2 sigmoid(2x) - 1, gelu = x Phi(x).
# Each is checked exhaustively on one operand and against numpy on random pairs.
def fp16_neg(x):
    """-x: the sign bit flipped (NaN stays NaN)."""
    return bits(x) ^ 0x8000


def fp16_abs(x):
    """|x|: the sign bit cleared."""
    return bits(x) & 0x7FFF


def _fixed24(x):
    """(sign, significand as an integer, exponent) so that |x| = sig * 2^(e - 10) with sig in
    1024..2047 for normals and the subnormal field for subnormals (e = -14 then), 0 for zero."""
    x = bits(x)
    ex = fp16_exp(x)
    fr = fp16_frac(x)
    sig = np.where(ex == 0, fr, fr | 0x400)
    e = np.where(ex == 0, -14, ex - 15)
    return fp16_sign(x), sig, e


def fp16_add(a, b):
    """a + b, correctly rounded (nearest even), with NaN/Inf/zero as IEEE says (Inf - Inf is
    NaN; -0 + -0 is -0; a NaN operand gives QNAN)."""
    a, b = bits(a), bits(b)
    sa, ma, ea = _fixed24(a)
    sb, mb, eb = _fixed24(b)
    # align to the larger exponent with 3 extra bits (guard, round) and a sticky bit
    swap = (ea < eb) | ((ea == eb) & (ma < mb))
    s1, m1, e1 = np.where(swap, sb, sa), np.where(swap, mb, ma), np.where(swap, eb, ea)
    s2, m2, e2 = np.where(swap, sa, sb), np.where(swap, ma, mb), np.where(swap, ea, eb)
    d = np.minimum(e1 - e2, 40)
    m1x = m1 << 13
    m2x = m2 << 13
    shifted = m2x >> d
    sticky = ((m2x & ((np.int64(1) << d) - 1)) != 0).astype(np.int64)
    m2s = shifted | sticky
    same = s1 == s2
    v = np.where(same, m1x + m2s, m1x - m2s)          # v >= 0: the larger magnitude leads
    sign = s1
    # v is in Q13 of the significand scale (value = v / 2^23 * 2^(e1 + 10) ... ): normalise
    pos = leading_one(v)
    e_unb = e1 + (pos - 23)                          # significand 1.xxx at bit 23 <-> exponent e1
    y = pack_fp16(sign, e_unb, v, pos)
    y = np.where(v == 0, np.where(same & (s1 == 1), NZERO, PZERO), y)
    # specials
    an, bn, ai, bi = is_nan(a), is_nan(b), is_inf(a), is_inf(b)
    y = np.where(ai & bi & (sa != sb), QNAN, y)
    y = np.where(ai & ~(bi & (sa != sb)), a, y)
    y = np.where(bi & ~ai, b, y)
    y = np.where(an | bn, QNAN, y)
    return y


def fp16_sub(a, b):
    """a - b."""
    return fp16_add(a, fp16_neg(b))


def fp16_mul(a, b):
    """a * b, correctly rounded (nearest even); 0 * Inf is NaN, signs as IEEE says."""
    a, b = bits(a), bits(b)
    sa, ma, ea = _fixed24(a)
    sb, mb, eb = _fixed24(b)
    sign = sa ^ sb
    prod = ma * mb                                   # <= 22 bits, value = prod * 2^(ea + eb - 20)
    pos = leading_one(prod)
    y = pack_fp16(sign, ea + eb - 20 + pos, prod, pos)
    y = np.where(prod == 0, sign << 15, y)
    an, bn, ai, bi = is_nan(a), is_nan(b), is_inf(a), is_inf(b)
    az, bz = is_zero(a), is_zero(b)
    y = np.where((ai | bi), (sign << 15) | PINF, y)
    y = np.where((ai & bz) | (bi & az) | an | bn, QNAN, y)
    return y


def fp16_from_int(n, frac_bits):
    """the FP16 pattern of a SIGNED fixed-point integer n with frac_bits fractional bits
    (from_fixed for a value that may be negative)."""
    n = bits(n)
    return from_fixed(np.abs(n), frac_bits, (n < 0).astype(np.int64))


ONE, HALF, TWO = 0x3C00, 0x3800, 0x4000


BLOCKS = [bits, fp16_sign, fp16_exp, fp16_frac, is_zero, is_subnormal, is_inf, is_nan,
          is_finite_nonzero, leading_zeros10, mantissa11, exponent_unbiased, to_fixed,
          signed_fixed, const_fixed, floor_shift, low_bits, field, select, rom, slope_rom,
          lookup, interp1, approx_on_mantissa, recip_fixed, round_rne, pack_fp16, leading_one, from_fixed,
          normalize_significand, specials, fp16_neg, fp16_abs, fp16_add, fp16_sub, fp16_mul,
          fp16_from_int]


# ------------------------------------------------------------------- the prelude and its docs
_MARK = "# --- flux_nlu toolkit prelude ---"


def prelude() -> str:
    """This module's source as text to prepend to a prototype: the blocks are then plain
    functions the prototype calls, the sandbox runs and the transpiler inlines."""
    import inspect

    src = inspect.getsource(inspect.getmodule(prelude))
    body = src.split('"""', 2)[2] if src.lstrip().startswith('"""') else src   # drop the docstring
    body = body.split("\n\n# ------------------------------------------------------------------- the prelude and its docs")[0]
    body = body.replace("from __future__ import annotations\n", "")   # not first in a prototype; not allowed by the screen
    return _MARK + "\n" + body.strip() + "\n# --- end of the toolkit prelude ---\n"


def with_prelude(code: str, operators: dict[str, str] | None = None) -> str:
    """`code` with the toolkit in front, once -- and, when `operators` is given, the
    campaign's VERIFIED operators as blocks after it (D487: sigmoid = recip(1 + exp(-x)) once
    `exp_fp16` and `recip_fp16` exist)."""
    if _MARK in code:
        return code
    return prelude() + "\n" + operator_prelude(operators or {}) + code


_AUDITED = ("pack_fp16", "interp1", "lookup", "specials", "from_fixed")


def operator_prelude(operators: dict[str, str]) -> str:
    """The verified prototypes of `operators` ({op: array-form text}) as blocks: each one's
    module-level names and functions prefixed `_<op>_`, its `design` exposed as
    `<op>_fp16(x)`, and its calls of the audited blocks routed to the un-audited originals
    so a composition's report speaks about the composition, not about a verified part."""
    import ast

    if not operators:
        return ""
    import inspect
    import textwrap

    out = ["# --- verified operators of this campaign (0 over on every input) ---",
           "import math", "import struct"]
    # the audited blocks again under their un-audited names, as real definitions (the
    # transpiler inlines FunctionDefs; an alias is a name it cannot follow)
    for n in _AUDITED:
        src = textwrap.dedent(inspect.getsource(globals()[n]))
        out.append(src.replace(f"def {n}(", f"def _{n}_block(", 1))
    for op, code in sorted(operators.items()):
        try:
            tree = ast.parse(code)
        except SyntaxError:
            continue
        names = {t.id for n in tree.body if isinstance(n, ast.Assign) for t in n.targets if isinstance(t, ast.Name)}
        names |= {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
        prefix = f"_{op}_"

        class R(ast.NodeTransformer):
            def visit_Name(self, node):
                if node.id in names:
                    node.id = prefix + node.id
                elif node.id in _AUDITED and isinstance(node.ctx, ast.Load):
                    node.id = f"_{node.id}_block"
                return node

            def visit_FunctionDef(self, node):
                if node.name in names:
                    node.name = prefix + node.name
                self.generic_visit(node)
                return node

            def visit_ClassDef(self, node):
                if node.name in names:
                    node.name = prefix + node.name
                self.generic_visit(node)
                return node

        body = [n for n in tree.body if not isinstance(n, (ast.Import, ast.ImportFrom))]
        tree.body = body
        R().visit(tree)
        out.append(f"# {op}: the verified prototype, namespaced")
        out.append(ast.unparse(ast.fix_missing_locations(tree)))
        out.append(f"def {op}_fp16(x):\n    return bits(_{op}_design(bits(x))) & 0xFFFF")
    out.append("# --- end of the verified operators ---")
    return "\n".join(out) + "\n"


def operator_docs(operators: dict[str, str]) -> str:
    """One line per verified operator for the prompt."""
    ref = {"exp": "e^x", "log": "ln(x)", "sigmoid": "1/(1+e^-x)", "tanh": "tanh(x)",
           "gelu": "x*Phi(x)", "recip": "1/x", "rsqrt": "1/sqrt(x)"}
    if not operators:
        return ""
    return "\n".join(f"  {op}_fp16(x): {ref.get(op, op)} of the FP16 pattern x, as an FP16 pattern -- "
                     "verified 0 over on all 65536 inputs; compose freely"
                     for op in sorted(operators))


def tool_docs() -> str:
    """One line per block -- signature and contract -- for the prompt."""
    import inspect

    lines = []
    for fn in BLOCKS:
        sig = str(inspect.signature(fn))
        doc = (inspect.getdoc(fn) or "").split("\n\n")[0].replace("\n", " ")
        lines.append(f"  {fn.__name__}{sig}: {doc}")
    return "\n".join(lines)


def prelude_lines(operators: dict[str, str] | None = None) -> int:
    """How many lines precede the model's text in `with_prelude(code, operators)` -- the
    number to subtract from a line of the whole to get the model's line."""
    return (prelude() + "\n" + operator_prelude(operators or {})).count("\n")


AUDIT_HARNESS = '''
_DIAG = []
_pack_fp16_block = pack_fp16
def pack_fp16(sign, e_unb, v, frac_bits):
    try:
        _v = np.asarray(v); _e = np.asarray(e_unb); _fba = np.asarray(frac_bits).astype(np.int64)
        _fin = np.isfinite(_v.astype(np.float64)) & (_v > 0)
        if _v.size > 1 and (_v == 0).sum() > _v.size // 2:
            # D494: gelu's Phi packed as `abs_val >> (l - M)` -- a NEGATIVE shift count when
            # the value has fewer than M bits, which numpy turns into 0 -- and the output
            # was 0 on 34,689 inputs with nothing said
            _DIAG.append(f"pack_fp16: v is ZERO on {int((_v == 0).sum())} of {_v.size} inputs -- the significand was shifted out: a right shift by a NEGATIVE count (numpy gives 0 or garbage; `v >> (pos - M)` when pos < M), a mask, or a table of zeros; to pack a fixed-point value at its leading one call from_fixed(v, frac_bits) instead of normalising by hand")
        _lo, _hi = np.int64(1) << _fba, np.int64(1) << (_fba + 1)
        _out = _fin & ((_v < _lo) | (_v >= _hi))
        _fb = int(_fba.max()); _lo = int(np.asarray(_lo).max()); _hi = int(np.asarray(_hi).max())
        if _fba.size > 1 and np.unique(_fba).size > 1:
            _out = _out & False                          # a per-element Q (from_fixed): packed at the leading one
        if _out.sum() > _fin.sum() // 8 and _fin.any():
            _vmin, _vmax = int(_v[_fin].min()), int(_v[_fin].max())
            _med = float(np.median(_v[_fin]))
            _guess = int(np.floor(np.log2(max(_med, 1.0))))
            _head = f"pack_fp16: v is outside [2^{_fb}, 2^{_fb + 1}) = [{_lo}, {_hi}) on {int(_out.sum())} of {int(_fin.sum())} finite inputs (v spans {_vmin}..{_vmax}, median near 2^{_guess}); "
            if _med < _lo:
                _DIAG.append(_head + f"v is BELOW 2^frac_bits: pack_fp16 wants the whole significand 1.xxx WITH its leading one (2^{_fb} <= v < 2^{_fb + 1}), not the fraction bits alone -- do not mask the leading one off; if the value can genuinely be below 1.0, normalize_significand(v, {_fb}) first and subtract its shift from the exponent")
            else:
                _DIAG.append(_head + f"with frac_bits={_fb} that is not a normalised 1.xxx significand -- either pass frac_bits={_guess} (the Q format v really has) or normalize_significand(v, {_fb}) first")
        if _e.size > 1 and (np.abs(_e[_fin]) > 40).sum() > _fin.sum() // 8:
            _DIAG.append(f"pack_fp16: e_unb spans {int(_e[_fin].min())}..{int(_e[_fin].max())} -- an UNBIASED FP16 exponent lies in -24..15; a biased one (BIAS added) or a shift count was passed")
        elif _e.size > 1 and _fin.sum() > 1000 and np.unique(_e[_fin]).size <= 4:
            _DIAG.append(f"pack_fp16: e_unb takes only {np.unique(_e[_fin]).size} distinct value(s) ({sorted(int(u) for u in np.unique(_e[_fin]))}) over {int(_fin.sum())} finite inputs -- a result that spans many binades needs an exponent derived from exponent_unbiased(x) plus your adjustment, not a shift count or the second value normalize_significand returns (that one is SUBTRACTED from the exponent, not the exponent)")
    except Exception:  # noqa: BLE001 -- an audit is help, never a gate (D491)
        pass
    return _pack_fp16_block(sign, e_unb, v, frac_bits)
def _index_audit(name, table, idx):
    _i = np.asarray(idx); _n = len(table)
    if _i.size <= 1:
        return
    if ((_i < 0) | (_i >= _n)).any():
        _DIAG.append(f"{name}: idx spans {int(_i.min())}..{int(_i.max())} for a table of {_n} entries -- mask/shift the index to its {max(1, _n - 1).bit_length()} bits")
        return
    _counts = np.bincount(_i.astype(np.int64).ravel(), minlength=_n)
    _busy = np.flatnonzero(_counts > max(1, _i.size // (8 * _n)))   # >= 1/8 of an entry's fair share
    # the signature of a hidden bit is ONE HALF of the table busy; of a constant offset, a
    # narrow block. A table indexed by a fixed-point x is busiest at the centre because FP16
    # inputs crowd around 0 -- that is the format, not a bug (D494: gelu's Phi table)
    _half = (_busy.size and _busy.min() == _n // 2 and _busy.max() == _n - 1) or \
            (_busy.size and _busy.min() == 0 and _busy.max() == _n // 2 - 1)
    if _n >= 4 and 0 < _busy.size and (_half or _busy.size <= _n // 8):
        _DIAG.append(f"{name}: only entries {int(_busy.min())}..{int(_busy.max())} ({_busy.size} of {_n}) of the table are indexed by more than a handful of inputs -- the index still carries the hidden bit or a constant offset (mantissa11(x) is 1024..2047: index with its fraction bits, (m & 0x3FF) >> (10 - T), which is right for subnormals too; fp16_frac(x) is not)")
_specials_block = specials
def specials(x, nan_out, pinf_out, ninf_out, pzero_out, nzero_out, y):
    try:
        _names = ("nan_out", "pinf_out", "ninf_out", "pzero_out", "nzero_out")
        _bad = [n for n, a in zip(_names, (nan_out, pinf_out, ninf_out, pzero_out, nzero_out))
                if (np.asarray(a).dtype == bool and np.asarray(a).size > 1)
                or (np.asarray(a).size > 1 and np.unique(np.asarray(a)).size <= 2 and int(np.asarray(a).max()) <= 1)]
        if _bad:
            _DIAG.append(f"specials: {', '.join(_bad)} look like boolean MASKS -- specials(x, nan_out, pinf_out, ninf_out, pzero_out, nzero_out, y) takes the OUTPUT PATTERNS for NaN/+Inf/-Inf/+0/-0 INPUTS (e.g. QNAN, PINF, PZERO, 0x3C00) and replaces y only on those input classes; to override y on a condition of your own, write select(cond, PATTERN, y)")
    except Exception:  # noqa: BLE001 -- an audit is help, never a gate (D491)
        pass
    return _specials_block(x, nan_out, pinf_out, ninf_out, pzero_out, nzero_out, y)
_from_fixed_block = from_fixed
def from_fixed(v, frac_bits, sign=0):
    try:
        _v = bits(v); _fb = np.asarray(frac_bits).astype(np.int64)
        if _v.size > 1:
            _pos = _v > 0
            _thin = _pos & (leading_one(_v) < 11)              # fewer than 12 significant bits
            if _thin.sum() > max(8, _pos.sum() // 64):
                _small = int(leading_one(_v[_thin]).min()); _k = int(_fb.max()) - _small
                _DIAG.append(f"from_fixed: v carries fewer than 12 significant bits on {int(_thin.sum())} of {int(_pos.sum())} nonzero results (down to {int(_v[_thin].min())} with frac_bits={int(_fb.max())}, a value near 2^-{_k}) -- FP16 keeps 11 bits at EVERY magnitude, so a fixed-point result must hold >= 12 significant bits at its SMALLEST value: for results down to 2^-{_k} give the value >= {_k + 12} fraction bits (out_frac_bits of recip_fixed, the shift before from_fixed) and pass that count as frac_bits; results that came out as 0 or 1 here are smaller still, so size the count from the smallest output the function reaches before your saturation threshold, not from this v")
    except Exception:  # noqa: BLE001 -- an audit is help, never a gate (D491)
        pass
    # from_fixed packs EVERY element, zeros included (its own np.where picks the signed
    # zero after), so the pack audit must not see that inner call (D494: "v is ZERO on
    # 37120 of 65536" fired on a correct from_fixed(prod, 28, s))
    global pack_fp16
    _w = pack_fp16
    pack_fp16 = _pack_fp16_block
    try:
        return _from_fixed_block(v, frac_bits, sign)
    finally:
        pack_fp16 = _w
_lookup_block = lookup
def lookup(table, idx):
    try:
        _index_audit("lookup", table, idx)
    except Exception:  # noqa: BLE001 -- an audit is help, never a gate (D491)
        pass
    return _lookup_block(table, idx)
_interp1_block = interp1
def interp1(table, slopes, idx, rem, rem_bits):
    try:
        _index_audit("interp1", table, idx)
        _r = np.asarray(rem)
        if _r.size > 1 and (_r >= (1 << int(rem_bits))).any():
            _DIAG.append(f"interp1: rem spans {int(_r.min())}..{int(_r.max())} but rem_bits={int(rem_bits)} says rem < {1 << int(rem_bits)} -- the fraction bits below the index do not match rem_bits")
        _t = np.asarray(table, dtype=np.float64); _s = np.asarray(slopes, dtype=np.float64)
        if _t.size >= 4 and _s.size == _t.size:
            # D494: gelu's SLOPES were the derivative per unit x, 8x the segment's difference
            # (the segment is 1/8 wide); interp1 adds slopes[idx] * rem >> rem_bits, so a slope
            # is f(right) - f(left) OF THE SEGMENT, scaled like the table
            _d = np.diff(_t); _m = np.abs(_d) > 2
            if _m.sum() >= 4:
                _ratio = np.median(_s[:-1][_m] / _d[_m])
                if not (0.7 <= _ratio <= 1.4):
                    _DIAG.append(f"interp1: the slopes are not the table's first differences (slopes[i] is ~{_ratio:.2g}x table[i+1]-table[i]) -- interp1 adds slopes[idx]*rem >> rem_bits, so a slope is f(right)-f(left) of ONE SEGMENT in the table's Q format: slope_rom(func, lo, hi, entries, frac_bits) builds exactly that; a derivative per unit x must be multiplied by the segment width (hi-lo)/entries")
    except Exception:  # noqa: BLE001 -- an audit is help, never a gate (D491)
        pass
    return _interp1_block(table, slopes, idx, rem, rem_bits)
'''


def audit_harness() -> str:
    """Text appended AFTER a prototype in the harness (D482): wrappers that check the two
    blocks every operator ends with against their own contracts on the real data, and leave
    findings in `_DIAG` for the harness to print. A library that says "you passed frac_bits=10
    for a Q13 value" ends the attempt the model spent five turns not finding; design() looks the
    names up at call time, so the wrappers take effect without touching its text."""
    return AUDIT_HARNESS


def misuse(code: str, extra_names: set[str] | None = None) -> list[str]:
    """Every way a prototype fights the toolkit instead of composing it, as "line N: what --
    what to do", on the MODEL's text alone. Two things the first live apex pass did in every
    attempt (D482): re-derived the blocks by hand (`def pack_fp16` with scalar `if`s -- the
    exact shape errors the toolkit exists to remove, and a definition that shadows the
    verified one), and tested itself at module level (`np.arange(65536)`, `design(...)`,
    `print`) -- the harness runs design() on every input; a self-test in the prototype is
    dead weight the transpiler must refuse."""
    import ast

    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []                                       # the subset rules report the parse error
    names = {fn.__name__ for fn in BLOCKS} | set(extra_names or ())
    redefined: list[tuple[str, int, int]] = []
    tests: list[tuple[int, str]] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
            redefined.append((node.name, node.lineno, node.end_lineno or node.lineno))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Import,
                               ast.ImportFrom, ast.Assign, ast.AnnAssign, ast.AugAssign, ast.For)):
            continue                                    # definitions, constants, table building
        elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            continue                                    # a docstring
        else:
            called = {n.func.id if isinstance(n.func, ast.Name) else getattr(n.func, "attr", "")
                      for n in ast.walk(node) if isinstance(n, ast.Call)}
            what = ("a call of design()" if "design" in called else "a print" if "print" in called
                    else f"a module-level `{type(node).__name__.lower()}`")
            tests.append((node.lineno, what))
    shadowed: list[tuple[str, int]] = []                 # `is_zero = is_zero(x)` (D484)
    for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
        called = {n.func.id for n in ast.walk(fn) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        bound: list[tuple[str, int]] = [(a.arg, fn.lineno) for a in fn.args.args]
        for node in ast.walk(fn):
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for t in targets:
                    bound += [(m.id, node.lineno) for m in ast.walk(t) if isinstance(m, ast.Name)]
        # a local named like a block is a bug only where the function also calls that name:
        # Python makes the name local for the whole body, so the call finds the array
        shadowed += [(n, ln) for n, ln in bound if n in names and n in called]
    out: list[str] = []
    if not any(isinstance(n, ast.FunctionDef) and n.name == "design" for n in tree.body):
        # D484: the rules' own "no design(x)" lands on line 1, inside the prelude once relocated,
        # and was filtered as a toolkit line -- so a prototype without design() ran and crashed
        out.append("line 1: no `design(x)` function -- the harness calls design() on all 65536 "
                   "inputs; a prototype is design() plus its module-level constants and tables "
                   "(scratch computations belong in the compute tool)")
    if shadowed:
        out.append(f"line {shadowed[0][1]}: `{shadowed[0][0]}` is assigned "
                   + (f"(and {len(shadowed) - 1} more block name(s): "
                      f"{', '.join(dict.fromkeys(n for n, _l in shadowed[1:]))}) " if len(shadowed) > 1 else "")
                   + "-- a variable or parameter with a block's name shadows the block, and the "
                   "next call of it is \"'numpy.ndarray' object is not callable\"; name it "
                   "differently (zero_in, nan_in, ...)")
    if redefined:
        spans = ", ".join(f"{a}-{b}" if b > a else str(a) for _n, a, b in redefined)
        out.append(f"line {redefined[0][1]}: {', '.join(n for n, _a, _b in redefined)} "
                   f"{'is a' if len(redefined) == 1 else 'are'} toolkit block"
                   f"{'' if len(redefined) == 1 else 's'}, already defined and verified -- "
                   f"delete your definition{'' if len(redefined) == 1 else 's'} (lines {spans}) "
                   "and call the block; a variant needs another name")
    if tests:
        out.append(f"line {tests[0][0]}: {tests[0][1]}"
                   + (f" and {len(tests) - 1} more module-level statement(s)" if len(tests) > 1 else "")
                   + " -- the prototype is definitions and constants; the harness runs design() "
                   "on all 65536 inputs itself. Test pieces with the compute tool")
    return out


def relocate(text: str, n: int | None = None) -> str:
    """Line numbers in a message about `with_prelude(code)` made relative to the model's own
    text (the prelude's `n` lines subtracted); a line inside the prelude is named as such."""
    import re

    if n is None:
        n = prelude_lines()

    def fix(m: re.Match) -> str:
        ln = int(m.group(1))
        return f"line {ln - n}" if ln > n else f"toolkit line {ln}"

    return re.sub(r"\bline (\d+)", fix, text)
