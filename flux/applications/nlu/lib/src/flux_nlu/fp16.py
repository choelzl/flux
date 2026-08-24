"""FP16 ground truth for the NLU study (D408): references, ULP distance, verdicts.

The domain is IEEE half precision, and its size is the study's superpower: 65536
possible inputs means every unary operator is checkable EXHAUSTIVELY in simulation --
correctness here is a proof by enumeration, not a sample (the bankmap posture). The
reference is numpy: compute in float64 from the float16 input, round back to float16.
That rounding is the declared truth; the double-rounding cases where float64->float16
differs from a correctly-rounded direct evaluation are vanishingly rare at half
precision and the reference is stated, versioned and identical for every candidate,
which is what a judge needs to be.

ULP distance uses the standard monotone key: reinterpreting the ordered halves onto a
line where adjacent representable numbers differ by 1, so "off by one ULP in the
mantissa" is literally |key(got) - key(want)| == 1, across exponent boundaries too.
Specials are judged by CLASS, not distance: where the reference is NaN any NaN
passes (payloads are nobody's contract); where it is +/-Inf only that infinity
passes -- a design that saturates to 65504 instead of overflowing to Inf is wrong,
and hiding that in an ULP number would be the lie.
"""

from __future__ import annotations

import math
from typing import Any, Callable

import numpy as np

__all__ = ["OPS", "OPCODES", "reference", "ulp_distance", "ulp_report", "all_inputs"]

#: Operator name -> opcode on the shared unit's 3-bit `op` port. The framework owns
#: this table; every prompt, harness and report uses these names and numbers.
OPCODES: dict[str, int] = {
    "exp": 0, "log": 1, "sigmoid": 2, "tanh": 3, "gelu": 4,
    "recip": 5, "rsqrt": 6,
}

_ERF = np.vectorize(math.erf, otypes=[np.float64])


def _gelu(x: np.ndarray) -> np.ndarray:
    return 0.5 * x * (1.0 + _ERF(x / math.sqrt(2.0)))


#: name -> float64 elementwise function. Kept beside OPCODES so adding an operator
#: is one row in each, and the conformance test that zips them cannot drift.
OPS: dict[str, Callable[[np.ndarray], np.ndarray]] = {
    "exp": np.exp,
    "log": np.log,
    "sigmoid": lambda x: 1.0 / (1.0 + np.exp(-x)),
    "tanh": np.tanh,
    "gelu": _gelu,
    "recip": lambda x: 1.0 / x,
    "rsqrt": lambda x: 1.0 / np.sqrt(x),
}


def all_inputs() -> np.ndarray:
    """Every FP16 bit pattern once: the exhaustive domain."""
    return np.arange(0x10000, dtype=np.uint16)


def reference(op: str, xs: np.ndarray) -> np.ndarray:
    """Golden outputs as uint16 bit patterns for uint16 input patterns."""
    x64 = xs.astype(np.uint16).view(np.float16).astype(np.float64)
    with np.errstate(all="ignore"):
        y64 = OPS[op](x64)
    return np.asarray(y64, dtype=np.float64).astype(np.float16).view(np.uint16)


def _order_key(bits: np.ndarray) -> np.ndarray:
    """Monotone int key over FP16: adjacent representables differ by 1; +0 == -0."""
    b = bits.astype(np.int64)
    neg = (b & 0x8000) != 0
    return np.where(neg, 0x8000 - (b & 0x7FFF), b + 0x8000)


_HUGE = 1 << 17   # class mismatch sentinel: beyond any real FP16 ULP distance


def ulp_distance(got: np.ndarray, want: np.ndarray) -> np.ndarray:
    """Elementwise ULP distance with class rules for specials (module docstring)."""
    got = got.astype(np.uint16)
    want = want.astype(np.uint16)
    g_exp = got & 0x7C00
    w_exp = want & 0x7C00
    g_man = got & 0x03FF
    w_man = want & 0x03FF
    g_nan = (g_exp == 0x7C00) & (g_man != 0)
    w_nan = (w_exp == 0x7C00) & (w_man != 0)
    g_inf = (g_exp == 0x7C00) & (g_man == 0)
    w_inf = (w_exp == 0x7C00) & (w_man == 0)
    dist = np.abs(_order_key(got) - _order_key(want))
    dist = np.where(w_nan, np.where(g_nan, 0, _HUGE), dist)
    dist = np.where(w_inf, np.where(got == want, 0, _HUGE), dist)
    dist = np.where(g_nan & ~w_nan, _HUGE, dist)
    dist = np.where(g_inf & ~w_inf & ~w_nan, _HUGE, dist)
    return dist


def ulp_report(op: str, xs: np.ndarray, got: np.ndarray, *,
               budget: int = 1, worst_n: int = 8) -> dict[str, Any]:
    """One operator's verdict over one input set. `max_ulp`/`over_budget` drive the
    gate; `worst` carries the counterexamples the repair prompt feeds on."""
    want = reference(op, xs)
    dist = ulp_distance(got, want)
    over = dist > budget
    order = np.argsort(-dist)
    worst = [
        {"x": f"0x{int(xs[i]):04x}", "got": f"0x{int(got[i]):04x}",
         "want": f"0x{int(want[i]):04x}",
         "ulp": ("class-mismatch" if int(dist[i]) >= _HUGE else int(dist[i]))}
        for i in order[:worst_n] if dist[i] > 0
    ]
    real = dist[dist < _HUGE]
    return {
        "op": op, "n": int(xs.size),
        "max_ulp": ("class-mismatch" if bool((dist >= _HUGE).any())
                    else int(dist.max()) if dist.size else 0),
        "mean_ulp": float(real.mean()) if real.size else 0.0,
        "pct_exact": float((dist == 0).mean()) if dist.size else 1.0,
        "error_rate": float((dist > 0).mean()) if dist.size else 0.0,
        "over_budget": int(over.sum()),
        "ok": not bool(over.any()),
        "worst": worst,
    }
