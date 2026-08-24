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
  halves the table at the cost of one multiply. The workhorse.
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
* gelu(x) = 0.5*x*(1+erf(x/sqrt(2))): either a direct piecewise fit on [-4,4] (it is
  ~x for x>3 and ~0 for x<-3 at FP16), or compose from tanh/sigmoid approximations --
  but composing STACKS the ULP errors; a direct fit of the whole function is usually
  the only way to hold <=1 ULP.

SHARING: exp/sigmoid/tanh/gelu are one family (each expressible through exp or tanh);
recip/rsqrt are another (Newton iterations share the multiplier). A shared
piecewise-poly engine with per-(op,segment) coefficient ROMs serves all seven with
one datapath -- the area win of sharing against the fmax cost of the op mux is
exactly the trade the frontier exists to measure.

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
