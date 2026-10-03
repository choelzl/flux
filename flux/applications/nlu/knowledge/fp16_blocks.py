"""VERIFIED FP16 BLOCKS for a prototype's design(x) -- plain integer Python that transcribes to
hardware, each checked by flux/tests/unit/test_nlu_fp16_blocks.py: the decode and the rounding
on all 65,536 patterns, the rounding on a hundred thousand wide values and every tie against
numpy's. COPY the ones you use into your prototype as they are (same names, same bodies) and
build design(x) between them. They are pieces, not a design: what to compute is yours.

    s, e, f = fp16_fields(x)       # sign, biased exponent 0..31, fraction 0..1023
    s, m, k = fp16_unpack(x)       # |x| = m * 2^k exactly, 1024 <= m < 2048 (x finite, non-zero)
    y = fp16_round(s, q, k, W)     # the FP16 pattern nearest (-1)^s * q * 2^k, ties to even
    p = horner(C, t, F)            # C[0] + C[1]*t + C[2]*t^2 + ..., every value Q.F
    C0, C1, C2 = segment_table(fn, lo, hi, N, 2, F)    # MODULE LEVEL only: per-segment coefficients

THE PATTERN every operator takes: (1) the classes and the saturated ranges first, by comparisons
on the fields (NaN, Inf, zero, the thresholds of the methods sheet -- split on the sign bit
first); (2) fp16_unpack, and the range reduction the exponent gives (k, its parity, n + f);
(3) the reduced function in fixed point on the significand: a segment index from the top bits
of t = m - 1024 (or of the reduced argument), u = the rest as Q.F, horner((C0[j], C1[j], C2[j]),
u, F); (4) ONE fp16_round at the end, handed the scale as k -- it does the subnormals, the carry
into the next binade, overflow to Inf and underflow to 0, so never pack bits by hand. A signed
fixed-point result v goes in as s = (v < 0), q = abs(v). Keep 3-6 guard bits beyond the 11 the
result needs (F = 16 is a good start; W = the width of q).

RECIPES, in these blocks' terms (the methods sheet has the why):
* recip: |x| = (m/1024) * 2^(k+10); r ~ 2^F * 1024/m on m in [1024, 2048) (segments of
  t = m - 1024, or a seed and one Newton step); y = fp16_round(s, r, -F - k - 10, W).
* rsqrt: make k even (k odd: m = 2*m, k = k - 1), so m/1024 is in [1, 4); r ~ 2^F/sqrt(m/1024)
  ([1,2) and [2,4) are two segment sets); y = fp16_round(0, r, -F - (k + 10) // 2, W).
* exp: z = x * log2(e) as a signed Q.F integer (m times log2(e) as Q.G, shifted by k + F - G);
  n = z >> F, f = z & (2^F - 1) (both right for a negative z: >> floors); p ~ 2^F * 2^(f/2^F)
  by segments; y = fp16_round(0, p, n - F, W).
* log: ln|x| = ln(m/1024) + (k+10)*ln2; for x near 1 the two cancel -- there compute ln(1+d),
  d = x - 1 exact in fixed point, as d * (1 - d/2 + d^2/3 ...) so the result keeps its own
  exponent; signed result: s = (v < 0), q = abs(v).
* tanh, sigmoid, gelu: odd/complement symmetry on |x| (s restored); tiny |x| is x (tanh) or
  0.5 + x/4 (sigmoid) or x * Phi(0) (gelu) to 1 ULP -- keep x's exponent there instead of a
  fixed point that flushes it; the mid range by segments; results that span many binades
  (sigmoid and gelu of a large negative x) by a per-segment exponent, as the sheet says.
"""


def fp16_fields(x):
    """x: a 16-bit pattern -> (s, e, f): its sign bit, biased exponent field 0..31, fraction 0..1023.
    e == 31: Inf (f == 0) or NaN; e == 0: zero (f == 0) or subnormal."""
    return (x >> 15) & 1, (x >> 10) & 31, x & 1023


def fp16_unpack(x):
    """A finite, non-zero x as (s, m, k): |x| = m * 2^k exactly, with 1024 <= m < 2048 -- a
    subnormal normalised by a leading-one shift (a priority encoder in hardware)."""
    s, e, f = fp16_fields(x)
    if e == 0:
        m = f
        k = -24
        for _ in range(10):
            if m < 1024:
                m = m << 1
                k = k - 1
    else:
        m = f | 1024
        k = e - 25
    return s, m, k


def fp16_round(s, q, k, W):
    """The FP16 pattern nearest (-1)^s * q * 2^k, ties to even: q an unsigned integer below 2^W
    (W a constant, at least 12), k any integer. Subnormals keep fewer bits, a round-up that
    carries into the next binade moves the exponent, a value past the largest finite is Inf
    (0x7c00) and one below half the least subnormal a zero of sign s."""
    if q == 0:
        return s << 15
    for _ in range(W):                     # the leading one to bit W-1
        if q < (1 << (W - 1)):
            q = q << 1
            k = k - 1
    e = k + W - 1 + 15                     # the biased exponent of the leading bit
    drop = W - 11                          # the bits below the 11 a normal keeps
    if e < 1:
        drop = drop + 1 - e                # a subnormal keeps fewer
    if drop > W + 1:
        drop = W + 1                       # all of q is below half the least subnormal: zero
    keep = q >> drop
    rest = q & ((1 << drop) - 1)
    half = 1 << (drop - 1)
    if rest > half or (rest == half and (keep & 1) == 1):
        keep = keep + 1
    if e < 1:
        return (s << 15) | keep            # keep == 1024: rounded up to the least normal, as it should
    if keep == 2048:                       # the carry into the next binade
        keep = 1024
        e = e + 1
    if e > 30:
        return (s << 15) | 0x7C00
    return (s << 15) | (e << 10) | (keep - 1024)


def horner(C, t, F):
    """C[0] + C[1]*t + C[2]*t^2 + ... in fixed point: t and every C[i] are Q.F integers (signed
    ones fine), each product rounded back to Q.F. C is a tuple of constants or table reads."""
    acc = C[len(C) - 1]
    for i in range(len(C) - 2, -1, -1):
        acc = ((acc * t + (1 << (F - 1))) >> F) + C[i]
    return acc


def segment_table(fn, lo, hi, N, degree, F):
    """MODULE LEVEL, once (float math is allowed there, as a ROM is generated): fn on [lo, hi)
    in N equal segments, each a polynomial of `degree` in u, the position inside its segment
    (u in [0, 1) as Q.F), fitted at Chebyshev nodes. Returns degree+1 tuples of N integers,
    the Q.F coefficients by power: C0, C1, C2 = segment_table(...); in design(),
    horner((C0[j], C1[j], C2[j]), u, F). N a power of two <= 64, so j is a bit-field."""
    import numpy as np

    h = (hi - lo) / N
    nodes = 0.5 - 0.5 * np.cos((2 * np.arange(4 * (degree + 1)) + 1) * np.pi / (8 * (degree + 1)))
    out = [[] for _ in range(degree + 1)]
    for j in range(N):
        ys = [fn(lo + (j + u) * h) for u in nodes]
        c = np.polyfit(nodes, ys, degree)[::-1]
        for i in range(degree + 1):
            out[i].append(int(round(c[i] * (1 << F))))
    return tuple(tuple(c) for c in out)
