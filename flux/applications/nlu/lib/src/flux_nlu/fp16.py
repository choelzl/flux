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

__all__ = ["OPS", "OPCODES", "describe_failures", "reference", "ulp_distance", "ulp_report", "all_inputs"]

#: Operator name -> opcode on the shared unit's 3-bit `op` port. The framework owns
#: this table; every prompt, harness and report uses these names and numbers.
OPCODES: dict[str, int] = {
    "exp": 0, "log": 1, "sigmoid": 2, "tanh": 3, "gelu": 4,
    "recip": 5, "rsqrt": 6,
}

_ERF = np.vectorize(math.erf, otypes=[np.float64])


def _gelu(x: np.ndarray) -> np.ndarray:
    # x * Phi(x) at -Inf is -Inf * 0 in float64 (NaN); the function's limit there is -0, which
    # is what the sheet promises and what hardware returns (D491: the reference asked the
    # model for NaN at x = -Inf, the one input where the formula and the function differ)
    with np.errstate(invalid="ignore"):
        y = 0.5 * x * (1.0 + _ERF(x / math.sqrt(2.0)))
    return np.where(np.isneginf(x), -0.0, y)


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
        # the cast to FP16 overflows to Inf by design (exp of a large x); the warning it
        # raised on every call filled the run's log tab with itself
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


def _f16(bits: Any) -> str:
    """A bit pattern as the number a reader can reason about (D419): 0x4984 says
    nothing, +11.03 says 'this is exp of a large input'."""
    v = float(np.array([int(bits)], dtype=np.uint16).view(np.float16)[0])
    if np.isnan(v):
        return "NaN"
    if np.isinf(v):
        return "+Inf" if v > 0 else "-Inf"
    return f"{v:+.4g}"


_REGION_EDGES = ((0.0, "zero/subnormal"), (2.0 ** -14, "|x|<0.25"), (0.25, "0.25<=|x|<1"),
                 (1.0, "1<=|x|<4"), (4.0, "4<=|x|<16"), (16.0, "16<=|x|<256"),
                 (256.0, "|x|>=256"))


def _regions(xs: np.ndarray, failing: np.ndarray, got: np.ndarray | None = None,
             want: np.ndarray | None = None) -> list[dict[str, Any]]:
    """WHERE the failures are (D419): failing/total per magnitude band and sign, so
    a model reading 'every band fails' learns the core is absent, rather than
    chasing four edge points. Specials (Inf/NaN inputs) are their own band."""
    vals = xs.astype(np.uint16).view(np.float16).astype(np.float64)
    mag = np.abs(vals)
    out: list[dict[str, Any]] = []
    for sign_name, sign_mask in (("x>=0", vals >= 0), ("x<0", vals < 0)):
        for k, (lo, label) in enumerate(_REGION_EDGES):
            hi = _REGION_EDGES[k + 1][0] if k + 1 < len(_REGION_EDGES) else np.inf
            band = sign_mask & np.isfinite(vals) & (mag >= lo) & (mag < hi)
            if lo == 0.0:
                band = sign_mask & np.isfinite(vals) & (mag < hi)
            n = int(band.sum())
            if n:
                row: dict[str, Any] = {"region": f"{sign_name} {label}",
                                       "failing": int((band & failing).sum()), "total": n}
                if want is not None:
                    wv = want[band].astype(np.uint16)
                    row["nan_only"] = bool((((wv >> 10) & 0x1F) == 0x1F).all()
                                           and ((wv & 0x3FF) != 0).all())
                bad = np.flatnonzero(band & failing)
                if bad.size and got is not None and want is not None:
                    i = int(bad[bad.size // 2])       # a typical one, not the extreme
                    row["example"] = {"xf": _f16(xs[i]), "gotf": _f16(got[i]),
                                      "wantf": _f16(want[i])}
                    gz = (np.asarray(got).astype(np.uint16)[bad] & 0x7FFF) == 0
                    wz = (np.asarray(want).astype(np.uint16)[bad] & 0x7FFF) == 0
                    row["zeros"] = int((gz & ~wz).sum())     # got 0 where the reference is not
                out.append(row)
    special = ~np.isfinite(vals)
    if special.any():
        out.append({"region": "Inf/NaN inputs", "failing": int((special & failing).sum()),
                    "total": int(special.sum())})
    return out


#: The reference each operator is judged against, as the reports spell it.
FORMULAS = {"exp": "e^x", "log": "ln(x)", "sigmoid": "1/(1+e^-x)", "tanh": "tanh(x)",
            "gelu": "x*Phi(x)", "recip": "1/x", "rsqrt": "1/sqrt(x)"}


def describe_failures(op: str, rep: dict[str, Any], *, worst_n: int = 4,
                      previous: dict[str, Any] | None = None) -> str:
    """The failure text a repair/patch prompt carries (D419): decoded numbers, the
    reference named, and the per-region breakdown. Built to be un-misreadable --
    the model once concluded the tests wanted y = x from four hex triples."""
    ref = FORMULAS.get(op, op)
    lines = [f"reference is y = {ref} rounded to FP16; {rep['over_budget']} of {rep['n']} "
             f"inputs are beyond the ULP budget (max {rep['max_ulp']} ULP)."]
    bands = [r for r in rep.get("regions", []) if r["total"]]
    prev = {r["region"]: r["failing"] for r in (previous or {}).get("regions", [])}
    # Bands where the reference is NaN for every input (log/rsqrt of a negative) are
    # matched by CLASS: an `ok` there says nothing about the algorithm (D478 -- Cedric read
    # log's x<0 rows as the half that worked; the whole function lives in x>0).
    nan_only = {r["region"] for r in bands if r.get("nan_only")}
    if bands:
        lines.append("failures by input region (failing/total"
                     + (", change since your last attempt" if prev else "") + "):")
        for r in bands:
            frac = r["failing"] / r["total"]
            tag = "ALL FAIL" if r["failing"] == r["total"] else (
                "ok" if r["failing"] == 0 else f"{frac:.0%}")
            if r["region"] in nan_only and r["failing"] == 0:
                tag = "ok (NaN by class -- not evidence of the algorithm)"
            delta = ""
            if r["region"] in prev and prev[r["region"]] != r["failing"]:
                d = r["failing"] - prev[r["region"]]
                delta = f"   {'better' if d < 0 else 'WORSE'} by {abs(d)}"
            ex = r.get("example")
            exs = f"   e.g. x={ex['xf']} got {ex['gotf']} wanted {ex['wantf']}" if ex else ""
            lines.append(f"  {r['region']:<24} {r['failing']:>6}/{r['total']:<6} {tag}{delta}{exs}")
    if rep.get("pattern"):
        lines.append(rep["pattern"])
    n_over, n_all = int(rep.get("over_budget", 0) or 0), int(rep.get("n", 0) or 0)
    if 0 < n_over <= 0.05 * n_all and not str(rep.get("pattern", "")).startswith("pattern: 106 failures"):
        # D494: gelu at 2,859 was rewritten from scratch three attempts running (parse
        # error, erf on the data path, a table left undefined) -- a design that is 96% right
        # is edited, not replaced
        lines.append(f"THIS DESIGN IS {100 * (n_all - n_over) / n_all:.1f}% RIGHT ({n_all - n_over} of {n_all} "
                     "inputs pass): EDIT it -- change the one thing the pattern names and keep everything "
                     "else; a new prototype starts over from tens of thousands of failures")
    if rep.get("audit"):
        lines.append("the blocks checked their contracts on your data:")
        lines.extend("  ! " + d for d in rep["audit"][:4])
    if rep.get("tables"):
        # the model's own tables as numbers (D482): apex pre-scaled a table by 2^13 that
        # `rom` had already scaled -- a look at TABLE's range would have said so
        lines.append("your tables (entries, integer value range): "
                     + rep["tables"].replace(";", "; "))
    if rep.get("worst"):
        lines.append("worst cases (x -> got, wanted):")
        for w in rep["worst"][:worst_n]:
            lines.append(f"  x={w['xf']} ({w['x']}): got {w['gotf']} ({w['got']}), "
                         f"wanted {w['wantf']} ({w['want']}), {w['ulp']} ULP off")
    return "\n".join(lines)


def failure_pattern(got: np.ndarray, want: np.ndarray, failing: np.ndarray,
                    xs: np.ndarray | None = None) -> str:
    """One sentence on what the failures have in COMMON, computed on the values (D482): apex
    sat at 62,978 over for four attempts editing subnormal handling while every region was
    exactly 2x the reference -- the worst cases (class mismatches sort first) never said so.
    A power of two shared by most failures is an exponent offset; a shared sign flip is a
    sign bug; a small relative error is precision (table, interpolation, rounding) -- three
    different edits, and the counts say which. Empty when nothing is shared."""
    g = np.asarray(got).astype(np.uint16).view(np.float16).astype(np.float64)
    w = np.asarray(want).astype(np.uint16).view(np.float16).astype(np.float64)
    both = failing & np.isfinite(g) & np.isfinite(w) & (w != 0)
    n_fail = int(failing.sum())
    n = int(both.sum())
    if n_fail == 0:
        return ""
    limits = failing & ~both
    if limits.sum() > 0.5 * n_fail:
        # D484: "1692 of 42468 failures are exactly 2^-1" -- true of the 4% with a finite,
        # nonzero reference; the other 96% were where the reference is 0 or Inf
        nan_ref = failing & np.isnan(w)
        if nan_ref.sum() >= 0.4 * n_fail:                # half the domain, typically
            # D485: log's negative half -- the reference is NaN by class, not a limit
            return (f"pattern: {int(nan_ref.sum())} of {n_fail} failures are inputs whose reference "
                    "is NaN (the function is undefined there, e.g. a negative x) -- return QNAN for "
                    "that whole input class before any arithmetic; any NaN pattern passes")
        return (f"pattern: {int(limits.sum())} of {n_fail} failures are where the reference is 0 "
                "or Inf (or the result is NaN/Inf) -- the overflow and underflow LIMITS: the inputs "
                "beyond which the result saturates must be caught before the arithmetic (their "
                "fixed-point form wraps), and the finite tail below them rounded to 0 or Inf")
    if n < max(8, n_fail // 4):
        return ""
    g, w = g[both], w[both]
    n_fail_txt = f"{n_fail}" if n == n_fail else f"{n} finite-reference failures ({n_fail} in all)"
    if (g == 0).sum() > 0.5 * n:
        return (f"pattern: got is ZERO on {int((g == 0).sum())} of {n_fail_txt} where the "
                "reference is not -- the value is shifted or masked away, or an underflow/zero "
                "test is true for everything")
    if xs is not None:                                     # D491: tanh's `y = x` shortcut to 0.375
        same = (np.asarray(got).astype(np.uint16)[failing] == np.asarray(xs).astype(np.uint16)[failing])
        if same.sum() > 0.9 * n_fail:
            gf = np.abs(np.asarray(got).astype(np.uint16)[failing].view(np.float16).astype(np.float64))
            lo, hi = gf.min(), gf.max()
            return (f"pattern: got is the INPUT itself on {int(same.sum())} of {n_fail} failures "
                    f"(|x| from {lo:.4g} to {hi:.4g}) -- a pass-through (`y = x` for small x, an "
                    "identity branch) fires where it is no longer within 1 ULP: shrink that "
                    "threshold to where the function's first neglected term is below 2^-12 "
                    "relative, and let the computed path take the rest")
    if np.unique(g).size == 1:
        return (f"pattern: got is the same value ({_f16(int(np.asarray(got)[failing][0]))}) on every "
                f"one of {n_fail} failures -- the result does not depend on the input: a constant "
                "path, a table indexed by a constant, or an early return that always fires")
    sign_flip = (np.sign(g) == -np.sign(w)) & (g != 0)
    if sign_flip.sum() > 0.5 * n:
        return (f"pattern: {int(sign_flip.sum())} of {n_fail_txt} have the SIGN of the "
                "reference flipped -- a sign bug, not a numerics one")
    r = np.abs(g) / np.abs(w)
    with np.errstate(all="ignore"):
        k = np.rint(np.log2(np.where(r > 0, r, 1.0)))
    exact = (r > 0) & (np.abs(r / np.exp2(k) - 1.0) < 2e-2) & (k != 0)
    if exact.sum() > 0.5 * n:
        ks, cnt = np.unique(k[exact], return_counts=True)
        kk = int(ks[cnt.argmax()])
        return (f"pattern: {int(cnt.max())} of {n_fail_txt} are exactly 2^{kk} times the "
                f"reference -- an exponent off by {kk}, not a table or rounding problem")
    rel = np.abs(g - w) / np.abs(w)
    ga, wa = np.abs(g), np.abs(w)
    if np.median(rel) >= 0.25:
        lo, hi = np.quantile(r, [0.1, 0.9])
        nz = r[r > 0]
        lo_nz, hi_nz = np.quantile(nz, [0.1, 0.9]) if nz.size >= 8 else (1.0, 1.0)
        if hi_nz > 100 * lo_nz:                # a spread of two orders among the NONZERO ratios
            # D492: gelu's ratios ran from 2.5 to 35,000 four attempts running, each reported
            # as "a scaling error" -- a factor that is not a constant is a missing TERM
            return (f"pattern: |got| / |reference| runs from {lo:.3g} to {hi:.3g} over {n} of "
                    f"{n_fail} failures -- the output does not TRACK the reference: not one "
                    "scaling but a factor that varies with x -- a term left out (the x in "
                    "x*Phi(x), a multiply by the mantissa after the table), a table of a "
                    "different function than the one the output needs, or a value packed at "
                    "the wrong Q per binade. Check ONE input by hand in a compute snippet "
                    "(print every intermediate as a float) before editing")
        return (f"pattern: |got| / |reference| runs from {lo:.3g} to {hi:.3g} (median "
                f"{np.median(r):.3g}) over {n} of {n_fail} failures -- a SCALING error: the "
                "exponent, a shift, the Q format or the table's argument range is wrong; "
                "precision is not the question yet")
    if (ga > wa).sum() > 0.9 * n or (ga < wa).sum() > 0.9 * n:
        side = "above" if (ga > wa).sum() > (ga < wa).sum() else "below"
        count = max(int((ga > wa).sum()), int((ga < wa).sum()))
        med = float(np.median(rel))
        if med >= 0.02:
            return (f"pattern: |got| is {side} |reference| on {count} of {n_fail} failures, "
                    f"typically by {med:.1%} (|got|/|reference| median {np.median(r):.3g}) -- a "
                    "systematic one-sided error, far too large for rounding: a Q position, a shift, "
                    "a missing term or the table's argument is off")
        return (f"pattern: |got| is {side} |reference| on {count} of {n_fail} failures (median "
                f"relative error {med:.2e} ~ 2^{np.log2(max(med, 1e-12)):.1f}) -- a one-sided "
                "small error: a rounding direction, a truncated term, a table sampled on the "
                "wrong side")
    med = float(np.median(rel))
    if med >= 0.015:
        return (f"pattern: no shared factor or sign, but a median relative error of {med:.1%} -- far "
                "beyond precision: the value computed is a different function of the input than "
                "intended (the argument the table was built on vs the index/rem derived from the "
                "input, a term with the wrong sign or scale, an interpolation formula)")
    return (f"pattern: no shared factor or sign; median relative error {med:.2e} ~ "
            f"2^{np.log2(max(med, 1e-12)):.1f} -- precision (table size, interpolation "
            "order, correction bits, rounding), the knobs' business")


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
         "xf": _f16(xs[i]), "gotf": _f16(got[i]), "wantf": _f16(want[i]),
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
        "regions": (regions := _regions(xs, dist > budget, got, want)),
        "pattern": _localised(regions, int(over.sum()), _rel_median(got, want, over))
                   or failure_pattern(got, want, over, xs),
    }


def _rel_median(got: np.ndarray, want: np.ndarray, failing: np.ndarray) -> float | None:
    """The median relative error over the failures with a finite, nonzero reference (None
    when fewer than a handful are), for the tiers that need to tell precision from a path."""
    g = np.asarray(got).astype(np.uint16).view(np.float16).astype(np.float64)
    w = np.asarray(want).astype(np.uint16).view(np.float16).astype(np.float64)
    m = failing & np.isfinite(g) & np.isfinite(w) & (w != 0)
    if m.sum() < 8:
        return None
    return float(np.median(np.abs(g[m] - w[m]) / np.abs(w[m])))


def _localised(regions: list[dict[str, Any]], n_fail: int, rel_med: float | None = None) -> str:
    """When one input region holds nearly every failure, that is the pattern (D482: a corrected
    recip failed on subnormal inputs only and the value-based pattern said "precision")."""
    if n_fail < 8:
        return ""
    bands: dict[str, int] = {}                      # the same band for both signs counts once
    for r in regions:
        band = r["region"].replace("x>=0 ", "").replace("x<0 ", "")
        bands[band] = bands.get(band, 0) + int(r["failing"])
    for band, failing in bands.items():
        if failing >= 0.9 * n_fail:
            hint = (" (for subnormal inputs: index and reduce from mantissa11(x), never from "
                    "fp16_frac(x))" if "subnormal" in band else
                    " (a range-reduction step, a saturation threshold or a fixed-point width "
                    "that runs out exactly there -- the rest of the design is right, keep it)")
            return (f"pattern: {failing} of {n_fail} failures are in one input band, {band} "
                    f"(both signs) -- a path for those inputs is wrong or missing{hint}, not the "
                    "arithmetic elsewhere")
    for sign in ("x<0", "x>=0"):
        rows = [r for r in regions if r["region"].startswith(sign + " ") and r["failing"]]
        failing = sum(int(r["failing"]) for r in rows)
        if failing >= 0.9 * n_fail and rows:
            worst = next((r for r in sorted(rows, key=lambda r: -int(r["failing"])) if r.get("example")), rows[0])
            ex = worst.get("example") or {}
            head = (f"pattern: {failing} of {n_fail} failures are {'negative' if sign == 'x<0' else 'non-negative'} "
                    f"inputs, in {', '.join(r['region'].replace(sign + ' ', '') for r in rows)} -- ")
            zeros = sum(int(r.get("zeros", 0)) for r in rows)
            if rel_med is not None and rel_med < 0.05:
                # D494: gelu's 2,859 were all negative x with errors under 1% -- not a missing
                # path but a table whose ABSOLUTE error is a large RELATIVE one where the
                # function is small (Phi(-2) = 0.02); "a path is wrong or missing" sent the
                # model to its saturation thresholds three times
                return (head + f"the errors there are SMALL (median {rel_med:.2g} relative, e.g. x={ex.get('xf', '?')} "
                        f"got {ex.get('gotf', '?')} wanted {ex.get('wantf', '?')}): on that half the result is much "
                        "smaller than on the other, so a table's or a fixed-point value's absolute error is a large "
                        "relative one -- precision where the function is small: more segments there (a finer "
                        "table or a second table for that half), a second-order term, or the small quantity "
                        "tabled in its own scaled Q; not a saturation or a missing branch"
                        + (f" -- except {zeros} of them, which are 0 where the reference is not: a saturation "
                           "threshold fires too early there" if zeros else ""))
            return (head + f"one half of the domain takes a path the other does not (e.g. x={ex.get('xf', '?')} "
                    f"got {ex.get('gotf', '?')} wanted {ex.get('wantf', '?')}): a saturation, an "
                    "underflow/overflow limit, or a sign-dependent step is wrong or missing")
    return ""


_FAMILY_REF = {
    "exp": "np.exp(v)", "log": "np.log(v)", "sigmoid": "1.0 / (1.0 + np.exp(-v))",
    "tanh": "np.tanh(v)", "gelu": "0.5 * v * (1.0 + _erf(v / 1.4142135623730951))",
    "recip": "1.0 / v", "rsqrt": "1.0 / np.sqrt(v)",
}


def family_judge(op: str, budget: int) -> str:
    """The FP16 gate as SOURCE for the family harness (D515: `flux_loop.pyint.family_harness`
    takes the judge from the problem): every FP16 pattern as the input, the reference rounded
    to FP16 as `_want`, a member's output shaped to 16-bit patterns, and `_over` counting the
    inputs beyond `budget` ULP with the gate's own class rules (NaN in -> any NaN; Inf and
    the class mismatches as HUGE)."""
    return f'''
_erf = np.vectorize(_math.erf, otypes=[np.float64])
_BUDGET = {int(budget)}
_xs = np.arange(65536, dtype=np.uint16)
_v = _xs.view(np.float16).astype(np.float64)
with np.errstate(all="ignore"):
    v = _v
    _want = np.asarray({_FAMILY_REF[op]}, dtype=np.float64).astype(np.float16).view(np.uint16).astype(np.int64)

def _shape(y):
    return (np.asarray(y, dtype=np.int64) & 0xFFFF).astype("<u2")

def _key(b):
    b = b.astype(np.int64)
    neg = (b & 0x8000) != 0
    return np.where(neg, 0x8000 - (b & 0x7FFF), b + 0x8000)

def _over(got):
    got = got.astype(np.int64); want = _want
    g_exp = got & 0x7C00; w_exp = want & 0x7C00; g_man = got & 0x03FF; w_man = want & 0x03FF
    g_nan = (g_exp == 0x7C00) & (g_man != 0); w_nan = (w_exp == 0x7C00) & (w_man != 0)
    g_inf = (g_exp == 0x7C00) & (g_man == 0); w_inf = (w_exp == 0x7C00) & (w_man == 0)
    HUGE = 1 << 17
    dist = np.abs(_key(got) - _key(want))
    dist = np.where(w_nan, np.where(g_nan, 0, HUGE), dist)
    dist = np.where(w_inf, np.where(got == want, 0, HUGE), dist)
    dist = np.where(g_nan & ~w_nan, HUGE, dist)
    dist = np.where(g_inf & ~w_inf & ~w_nan, HUGE, dist)
    return int((dist > _BUDGET).sum())
'''
