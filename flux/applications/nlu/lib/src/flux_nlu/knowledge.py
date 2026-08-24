"""Curated method knowledge for the NLU designer (D408) -- the mentor's seed.

The loop designs; this file only TEACHES. It is the "populate some knowledge to help
the loop" half of the contract: a compact, provenance-free-because-textbook cheat
sheet of the computation methods the study names, each with the two facts a designer
actually needs at FP16 -- the core identity, and where it breaks. Handed to the
proposer prompt through `fit_to_budget`, so a small context window gets the top of
this list and is told it was narrowed.

Ordered by how often each method wins at half precision: at 11 significand bits,
small tables and low-degree piecewise polynomials usually beat iterative methods on
both area and latency, and the model should hear that prior -- as a direction, not an
instruction (it is free to prove otherwise; the evaluator judges).
"""

from __future__ import annotations

METHODS = """\
METHODS, most promising first at FP16 (11 significand bits; ~3.3 decimal digits):

* piecewise-poly: split the reduced domain into 2^k segments (address by top mantissa
  bits), degree-1 or degree-2 polynomial per segment from a coefficient ROM. At FP16,
  16-64 segments with degree-1 usually reach <=1 ULP on smooth functions; degree-2
  DIVIDES the table by ~10 for one more multiply (16-32 segments where degree-1 wants
  hundreds). The workhorse. The rules the published units follow, and this campaign's
  first designs did not: (1) make the tabled RANGE a power of two ([0,4), [0,8), [-8,8))
  so the segment index is a BIT-FIELD of the fixed-point argument -- a range like
  [0, 4.508] with 1024 uniform segments needs a multiply (or a divider) just to address
  the table; (2) segment NON-UNIFORMLY -- dense where the function bends (tanh near 0,
  Phi around -2), sparse where it is flat or linear -- or table the function on a
  log-spaced argument; (3) where the output spans many binades (Phi(x) for x < -2, exp of
  a negative argument) store a per-segment EXPONENT with a short mantissa (block floating
  point) instead of a 32-bit fixed value and a 64-bit product; 16-20-bit multipliers are
  the norm, 48-64-bit ones a sign that the representation is wrong; (4) fold symmetry
  into ONE datapath (odd functions on |x| with the sign restored, Phi(-x) = 1 - Phi(x)
  only where the cancellation leaves the bits you need).
* lut: direct table on the mantissa (plus exponent handling in logic). A full 2^10
  table per function is ~16Kb ROM; exact by construction where the table IS the
  rounded reference. Wins on latency (1 cycle), loses on area if not shared.
* interpolation: lut on top mantissa bits + linear blend on the rest; a lut/poly
  hybrid -- table of 2^k entries plus one multiply. Often the knee point.
* poly: single minimax polynomial on the reduced range (Horner form). Needs degree
  4-6 for <=1 ULP on [1,2) at FP16 -- more multipliers than piecewise, no ROM.
* newton-raphson: recip: y' = y*(2 - x*y); rsqrt: y' = 0.5*y*(3 - x*y*y). One
  iteration from an 8-bit seed table reaches FP16 precision; the multiplies dominate
  area. Natural for recip/rsqrt only.
* cordic: shift-add iterations, hyperbolic mode gives exp/tanh/log via sinh/cosh.
  ~13 iterations for FP16 -- small area, long latency or deep pipeline; needs the
  scale-factor correction and argument range extension (|z| <= ~1.118).
* bit-product: weighted sum of bit products (a truncated multiplier-like array
  evaluating the function's boolean expansion); competitive only for very low
  precision -- at 11 bits usually dominated by piecewise-poly.
* parabolic-synthesis: recursive product of second-order factors; 2-3 stages at
  FP16, multiplier-heavy but shallow. An alternative to minimax poly.

RANGE REDUCTIONS the exponent gives you for free (use them; the polynomial then only
covers a unit interval):
* exp(x)  = 2^(x*log2(e)); split x*log2(e) = n + f, f in [0,1): result exponent is n,
  evaluate 2^f on one interval. Overflow to +Inf above ~11.09; underflow below ~-17.3.
* log(x)  = log(m) + e*ln(2) for x = m*2^e, m in [1,2): evaluate log(m) only.
  log of negative is NaN, log(0) is -Inf: handle as classes, not values.
* recip(x)  = 2^-e * recip(m): reduce to m in [1,2), negate exponent, watch subnormals.
* rsqrt(x)  = 2^(-e/2) * rsqrt(m), m in [1,4) by exponent parity: one bit selects
  between two sub-ranges.
* sigmoid(x) = 0.5*(1 + tanh(x/2)); or 1/(1+exp(-x)) reusing exp + recip.
  Saturates to 1.0 above ~+8.3 and to 0 below ~-13; exploit early-out.
* tanh(x) = (exp(2x)-1)/(exp(2x)+1); saturates to +/-1 beyond |x| ~ 4.2 at FP16 --
  most of the domain is the constant, only [0, 4.2) needs computing.
* gelu(x) = x*Phi(x), Phi(x) = 0.5*(1+erf(x/sqrt(2))): TABLE Phi -- smooth, in (0,1),
  ~0.5+0.4x near 0 -- on [-5.8, 3.5] and MULTIPLY by x in fixed point, so the RELATIVE
  error stays bounded as x -> 0 (a table of gelu itself holds a value near 0 with 11 bits
  to keep and loses them all below |x| ~ 0.25, where 24k of the inputs live). For
  NEGATIVE x, Phi is small (Phi(-2) = 0.023, Phi(-4) = 3e-5) and a table's absolute error
  is a large relative one there: give Phi 24+ fraction bits and enough segments where it
  is small. It is x itself from +3.447 on and -0 only from -5.727 on (the exact boundaries
  are in the saturation list below). Composing from tanh/sigmoid approximations STACKS
  the ULP errors.

SHARING: exp/sigmoid/tanh/gelu are one family (each expressible through exp or tanh);
recip/rsqrt are another (Newton iterations share the multiplier). A shared
piecewise-poly engine with per-(op,segment) coefficient ROMs serves all seven with
one datapath -- the area win of sharing against the fmax cost of the op mux is
exactly the trade the frontier exists to measure.

SATURATION THRESHOLDS, measured against the exact FP16 reference (these are the
spec, not advice -- an output that is finite where the reference says Inf, or
nonzero where it says 0, is a class error no polynomial can fix; handle these
ranges with EXPLICIT comparisons on the input bits BEFORE any approximation).
THE COMPARISON TRAP: FP16 bit patterns do NOT order as unsigned integers once the
sign bit is set -- every negative pattern is >= 0x8000, so a raw `x >= 0x498c`
saturates ALL negatives to +Inf (a measured failure in this campaign). Split on
x[15] FIRST: compare positive thresholds only when the sign bit is 0, negative
thresholds (against the magnitude, or the full pattern) only when it is 1:
* exp:     x >= 0x498c (+11.094)  -> +Inf (0x7c00);  x >= 0xcc56 as a negative
           (<= -17.344) -> +0. Between: subnormal outputs are real and required.
* sigmoid: x >= 0x4829 (+8.320)   -> exactly 1.0 (0x3c00);  negative x >= 0xcc56
           (<= -17.344) -> +0.
* tanh:    x >= 0x4482 (+4.508)   -> exactly 1.0;  0xc482 and beyond -> exactly -1.0.
* gelu:    x >= 0x42e5 (+3.447) -> x itself (gelu(x) rounds to x from there on);
           negative x: gelu is a real FP16 value all the way down to 0xc5b9 (-5.723),
           gelu(-4) = -1.27e-4 and gelu(-5) = -1.4e-6 included; -0 only from 0xc5ba
           (<= -5.727). A "-0 below -4" shortcut fails 392 inputs.
* recip/rsqrt: 0 -> +/-Inf and Inf -> 0 per IEEE; rsqrt of any negative is NaN.

STYLE, learned from this campaign's own refusals: long `else if (x == 16'hXXXX)`
chains breed syntax errors -- prefer `casez` on the exponent bits x[14:10] (or on
the whole x) with ranges, and keep every sized literal fully written out.

FP16 FACTS: bias 15; subnormals below 2^-14 (flushing them is a VISIBLE error the
exhaustive check will count); max normal 65504; 1 ULP at [1,2) is 2^-10. The
reference rounds to nearest-even from float64 -- match its specials by class:
NaN in -> NaN out (any payload); the overflow threshold is where the reference says
Inf, not where your accumulator saturates.
"""


def knowledge_text(budget_chars: int | None = None) -> str:
    """The cheat sheet, fitted to a prompt budget with the narrowing announced."""
    if budget_chars is None:
        return METHODS
    from flux_llm import fit_to_budget

    return fit_to_budget(METHODS, budget_chars)
