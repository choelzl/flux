# Computing an FP16 function as a formula: method notes

These notes give the method and facts measured on this problem's golden model. They are not a
design.

## The format
- binary16: sign (bit 15), exponent (bits 14:10, bias 15), mantissa (bits 9:0).
  - A normal value is (-1)^s * 2^(e-15) * (1 + m/1024).
  - When e = 0 the value is subnormal: (-1)^s * 2^-14 * m/1024.
  - e = 31 means inf (m = 0) or NaN.
- One ULP is one step of the 16-bit pattern within a binade. This problem allows 1 ULP of
  error, so aim for under 1/2 ULP before rounding.
- Round to nearest, ties to even. Keep at least three extra bits (guard, round, sticky) until
  the final rounding.

## The one mistake to avoid
Do not convert x into a single fixed-point format. FP16 spans 2^-24 to 65504; a binary point
that suits 1.0 flushes every small value to zero. That exact mistake made half the inputs fail
in an earlier attempt. Keep the exponent, and compute on the mantissa.

## Factor the function
GELU(x) = x * h(x), where h(x) = 0.5 * (1 + tanh(sqrt(2/pi) * (x + 0.044715 x^3))).
- h is smooth and lies in (0, 1): h(-4) = 1.76e-5, h(-2) = 0.0227, h(0) = 0.5, h(1) = 0.841,
  h(3) = 0.99879.
- y = x * h(x) keeps relative precision. Multiply x's mantissa by h's mantissa and add the
  exponents. Only h needs approximating.
- On the negative side h is small, down to 1e-5 and below, so y is small too and has its own
  small ULP. Approximate h with RELATIVE accuracy there. Either give h its own exponent and
  normalise it with `bit_length`, or approximate something with a flat range, such as
  log2 h(x) or h scaled per segment.

## Regions measured on this golden (decide them with comparisons on the bit pattern)
- x >= 3.380859375 (and +inf): y = x exactly; the correction is below half an ULP.
- x < -5.28515625 (and -inf): y = -0 (0x8000).
- Negative x from -5.285 upwards gives a SUBNORMAL result until about -4.75. Shift into the
  subnormal range before rounding.
- |x| < 0.0278: y = x/2 + x^2 / sqrt(2*pi), rounded once, matches the golden exactly. Compute
  it on the mantissa with the exponent kept; x^2 scales as 2^(2e).
- NaN in gives the input back (quiet); 0 in gives 0 of the same sign.
- In between (0.0278 <= |x| < 3.38, and -5.29 < x <= -0.0278), h needs an approximation.

## Approximating h: a polynomial per segment
- At module level (floats are allowed there, once), split the middle range into segments. Use
  16 to 64 uniform segments in x, or segments by the exponent of |x|. Fit a degree-2 or
  degree-3 polynomial per segment with `numpy.polyfit` on dense samples. Then quantise the
  coefficients to integers at a scale you choose, and store them in small tables of at most
  64 entries each.
- In design(), take the segment index from the top bits of |x|. In this middle range a fixed
  point with about 14 fractional bits holds x well, because |x| is between 2^-6 and 2^3.
  Evaluate with Horner's rule in integers, shifting after each multiply.
- Size the widths so the error of h is under about 1/4 ULP of the result. Check the error
  numerically at module level before you trust it.
- Fewer segments and a lower degree mean less hardware. Get it passing first, then shrink.

## Rounding the result
- Form the product x_mant * h_mant with its exponent.
- Normalise: if the product carried into a new bit, shift and add 1 to the exponent.
- Round to nearest even at 10 mantissa bits. The rounding may carry into the exponent.
- If the exponent falls below the normal range, shift right into a subnormal, keeping the
  sticky bit, then round.

## Work loop
Write the prototype and run the check command. Read the "WHERE they fail" table, which groups
failures by sign and exponent, and fix the range that fails most first. Keep every range that
already passes.
