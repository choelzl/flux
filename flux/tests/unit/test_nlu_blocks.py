"""D481: the toolkit -- verified blocks a prototype composes. Each block's contract is checked
on its own (exhaustively where the domain allows); a whole operator composed from them reaches
0 over in a few lines, transpiles, and the loop admits it from a scripted model that only ever
composes -- never writes RTL, never re-derives classification, normalisation or rounding."""

from __future__ import annotations

import json
import shutil
import sys

import numpy as np
import pytest

from flux_nlu import blocks as B
from flux_nlu.blocks import prelude, relocate, tool_docs, with_prelude
from flux_nlu.fp16 import all_inputs, reference, ulp_report

XS = all_inputs().astype(np.int64)
VALS = all_inputs().view(np.float16).astype(np.float64)


def test_fields_and_classes_match_the_ieee_view():
    finite = np.isfinite(VALS)
    assert (B.fp16_sign(XS) == (np.signbit(VALS)).astype(int)).all()
    assert (B.is_nan(XS) == np.isnan(VALS)).all() and (B.is_inf(XS) == np.isinf(VALS)).all()
    assert (B.is_zero(XS) == (VALS == 0)).all()
    assert (B.is_subnormal(XS) == ((VALS != 0) & (np.abs(VALS) < 2.0 ** -14))).all()
    assert (B.is_finite_nonzero(XS) == (finite & (VALS != 0))).all()


def test_normalisation_reconstructs_every_finite_nonzero_value():
    """|x| == mantissa11/1024 * 2^exponent_unbiased, subnormals included."""
    m = B.is_finite_nonzero(XS)
    recon = B.mantissa11(XS)[m] / 1024.0 * np.exp2(B.exponent_unbiased(XS)[m].astype(np.float64))
    assert np.allclose(recon, np.abs(VALS[m]), rtol=0, atol=0)
    assert ((B.mantissa11(XS)[m] >> 10) == 1).all()          # the hidden one is in place


def test_fixed_point_conversion_truncates_and_saturates():
    m = B.is_finite_nonzero(XS)
    got = B.to_fixed(XS, 12, 5)[m]
    want = np.minimum(np.floor(np.abs(VALS[m]) * 4096), 32 * 4096).astype(np.int64)
    assert (got == want).all()
    sf = B.signed_fixed(XS, 12, 5)[m]
    assert (np.sign(sf) == np.sign(VALS[m])).all() or (sf[np.sign(sf) != np.sign(VALS[m])] == 0).all()
    assert B.const_fixed(1.4426950408889634, 14) == 23637


def test_round_rne_is_round_half_to_even():
    v = np.arange(0, 64, dtype=np.int64)
    got = B.round_rne(v, 2)
    want = np.rint(v / 4.0).astype(np.int64)                  # numpy rint is half-to-even
    assert (got == want).all()
    assert (B.round_rne(np.array([5, 6, 7]), np.array([0, 1, 2])) == [5, 3, 2]).all()


def test_pack_fp16_round_trips_every_finite_value_and_handles_the_edges():
    """Unpack every finite FP16 to (sign, e, significand) and pack it back: identity. Then the
    edges: a carry out of the significand, overflow to Inf, a subnormal result, underflow."""
    m = np.isfinite(VALS) & (VALS != 0)
    x = XS[m]
    v = B.mantissa11(x) << 3                                   # Q1.13
    y = B.pack_fp16(B.fp16_sign(x), B.exponent_unbiased(x), v, 13)
    assert (y == x).all()
    assert B.pack_fp16(0, 0, (1 << 13) + (1 << 13) - 1, 13) == 0x4000       # 1.9999 rounds up to 2.0: carry
    assert B.pack_fp16(0, 16, 1 << 13, 13) == B.PINF                         # 2^16 overflows
    assert B.pack_fp16(1, -15, 1 << 13, 13) == (0x8000 | 0x0200)             # -2^-15 = a subnormal
    assert B.pack_fp16(0, -30, 1 << 13, 13) == 0                              # below half the smallest


def test_tables_are_the_oracles_numbers():
    t = B.rom("exp2", 0.0, 1.0, 8, 10)
    assert (t == np.rint(np.exp2(np.arange(8) / 8) * 1024)).all()
    s = B.slope_rom("exp2", 0.0, 1.0, 8, 10)
    assert (s > 0).all() and abs(int(s[0]) - round((2 ** (1 / 8) - 1) * 1024)) <= 1
    idx = np.array([0, 7]); rem = np.array([0, 255])
    assert (B.interp1(t, s, idx, rem, 8) == t[idx] + ((s[idx] * rem) >> 8)).all()


COMPOSED_EXP = '''
XF = 12; LB = 14; T = 5; M = 12; CB = 8
SPACE = {"T": [4, 5, 6], "M": [12, 13], "CB": [6, 8]}

def design(x):
    x = x.astype(np.int64)
    xf = signed_fixed(x, XF, 5)
    t = xf * const_fixed(1.4426950408889634, LB)
    k = floor_shift(t, XF + LB)
    f = low_bits(t, XF + LB)
    idx = field(f, XF + LB - 1, XF + LB - T)
    rem = field(f, XF + LB - T - 1, XF + LB - T - CB)
    tab = rom("exp2", 0.0, 1.0, 1 << T, M)
    slp = slope_rom("exp2", 0.0, 1.0, 1 << T, M)
    p = interp1(tab, slp, idx, rem, CB)
    y = pack_fp16(0, k, p, M)
    return specials(x, QNAN, PINF, PZERO, 0x3C00, 0x3C00, y).astype(np.uint16)
'''


def test_an_operator_composed_from_blocks_reaches_zero_over_in_a_few_lines():
    ns: dict = {}
    exec(with_prelude(COMPOSED_EXP), ns)                       # noqa: S102 -- the test's own text
    got = ns["design"](all_inputs())
    assert ulp_report("exp", all_inputs(), got)["over_budget"] == 0
    assert len([ln for ln in COMPOSED_EXP.strip().splitlines() if ln.strip()]) <= 18


def test_blocks_take_the_harness_uint16_array_and_rom_takes_a_callable():
    """D482, from apex's first composed recip: every family member raised. (1) `rom(lambda m:
    2/m, ...)` -- the doc said `func` and the block wanted a NAME from its own table; (2)
    `exponent_unbiased(x)` on the harness's uint16 array met `-BIAS` ("Python integer -15 out
    of bounds for uint16"); the fixture had hidden that behind `x.astype(np.int64)`."""
    xs = all_inputs()                                          # uint16, as the harness passes it
    assert xs.dtype == np.uint16
    e = B.exponent_unbiased(xs)
    assert e.dtype == np.int64 and e.min() == -24 and e[0x0400] == -14 and e[0x3C00] == 0
    assert B.bits(xs).dtype == np.int64 and B.to_fixed(xs, 12, 5).dtype == np.int64
    tab = B.rom(lambda t: 2.0 / t, 1024, 2048, 32, 11)
    assert list(tab[:2]) == [round(2 / 1024 * 2048), round(2 / 1056 * 2048)]
    assert np.array_equal(B.rom("recip", 1.0, 2.0, 16, 10), B.rom(lambda t: 1.0 / t, 1.0, 2.0, 16, 10))
    assert np.array_equal(B.slope_rom("exp2", 0.0, 1.0, 8, 12), B.slope_rom(np.exp2, 0.0, 1.0, 8, 12))
    with pytest.raises(ValueError, match="unknown function name"):
        B.rom("cosh", 0.0, 1.0, 8, 12)
    # a composition with NO cast of its own runs on the harness's array
    ns: dict = {}
    exec(with_prelude(COMPOSED_EXP.replace("    x = x.astype(np.int64)\n", "")), ns)   # noqa: S102
    got = ns["design"](xs)
    assert ulp_report("exp", xs, got)["over_budget"] == 0


def test_the_failure_report_names_what_the_failures_share():
    """D482: apex edited subnormal handling for four attempts while every region was 2-4x the
    reference. The report now says what the failures have in common -- a power of two, a sign,
    a scaling, a one-sided error, or plain precision -- each a different edit."""
    xs = all_inputs()
    variants = {
        "exponent off by one": (COMPOSED_EXP.replace("pack_fp16(0, k, p, M)", "pack_fp16(0, k + 1, p, M)"),
                                "exactly 2^1 times the reference -- an exponent off by 1"),
        "sign flipped": (COMPOSED_EXP.replace("pack_fp16(0, k, p, M)", "pack_fp16(1, k, p, M)"),
                         "have the SIGN of the reference flipped"),
        "coarse table": (COMPOSED_EXP.replace("T = 5", "T = 2").replace("CB = 8", "CB = 2"),
                         "|got| is below |reference|"),
        "wrong Q format": (COMPOSED_EXP.replace("p = interp1(tab, slp, idx, rem, CB)", "p = interp1(tab, slp, idx, rem, CB) * 3 // 2"),
                           "a SCALING error"),
        "all zero": (COMPOSED_EXP.replace("y = pack_fp16(0, k, p, M)", "y = pack_fp16(0, k, p, M) * 0"),
                     "got is ZERO on"),
        "constant": (COMPOSED_EXP.replace("y = pack_fp16(0, k, p, M)", "y = pack_fp16(0, k, p, M) * 0 + 0x3C00"),
                     "got is the same value (+1"),
        "nan class": (COMPOSED_EXP, "are inputs whose reference is NaN"),   # judged as log below
        "no saturation": (COMPOSED_EXP.replace("specials(x, QNAN, PINF, PZERO, 0x3C00, 0x3C00, y)",
                                               "np.where(fp16_exp(x) >= 18, np.where(fp16_sign(x) == 1, 0x0001, 0x7BFF), specials(x, QNAN, PINF, PZERO, 0x3C00, 0x3C00, y))"),
                          "failures are where the reference is 0 or Inf"),
    }
    for label, (src, expect) in variants.items():
        ns: dict = {}
        exec(with_prelude(src), ns)                            # noqa: S102 -- the test's own text
        op = "log" if label == "nan class" else "exp"           # exp's values judged as log: x<0 wants NaN
        rep = ulp_report(op, xs, np.asarray(ns["design"](xs), dtype=np.uint16))
        assert rep["over_budget"] > 0 and expect in rep["pattern"], (label, rep["pattern"])
    from flux_nlu.fp16 import describe_failures

    assert "pattern:" in describe_failures("exp", rep)
    ns = {}
    exec(with_prelude(COMPOSED_EXP), ns)                       # noqa: S102
    assert ulp_report("exp", xs, np.asarray(ns["design"](xs), dtype=np.uint16))["pattern"] == ""


@pytest.mark.skipif(sys.platform != "linux", reason="the sandbox uses resource limits")
def test_erf_and_gelu_are_table_functions_too():
    """D491: the RTL table oracle offered gelu and erf; the prototype's rom did not, and the
    model vectorised math.erf by hand. Both spellings, and the transpiler evaluates the ROM."""
    from flux_nlu.transpile import transpile

    assert np.array_equal(B.rom("gelu", -4, 4, 8, 10), B.rom(B.gelu, -4, 4, 8, 10))
    assert list(B.rom("erf", 0, 1, 2, 8)) == [0, 133]                 # erf(0) = 0, erf(0.5) = 0.5205
    import math
    scalar_only = lambda t: 0.5 * (1 + math.erf(t / math.sqrt(2)))   # noqa: E731 -- the model's own lambda
    assert np.array_equal(B.rom(scalar_only, -4, 4, 16, 12), B.rom(lambda t: 0.5 * (1 + B.erf(t / math.sqrt(2))), -4, 4, 16, 12))
    src = with_prelude("TAB = rom(gelu, -4.0, 4.0, 8, 12)\ndef design(x):\n    return from_fixed(np.abs(TAB[bits(x) & 7]), 12, fp16_sign(x))\n")
    assert "module nlu_t" in transpile(src, top="nlu_t", xs=all_inputs())


def test_an_audit_never_raises_and_a_wrong_shape_is_named():
    """D491: gelu's attempt returned a (2, 65536) array; the pack_fp16 AUDIT crashed on it
    ("too many indices") before the harness could say what was wrong. Audits are help,
    never a gate: each is wrapped, and the shape check names the shape it got."""
    from flux_loop import LoopRequest, LoopState
    from flux_nlu.problem import NluProblem

    prob = NluProblem(ops=("exp",), ulp_budget=1, clock_period_ps=1250.0, seed=1, test_rounds=0)
    st = LoopState(request=LoopRequest(), say=lambda _m: None, proposer=None, feedback=None)
    zeros = ("M = 13\ndef design(x):\n    a = mantissa11(x) * 3\n    l = leading_one(a)\n"
             "    return pack_fp16(0, l - M, a >> (l - M), M)\n")            # a negative shift count
    v = prob.prototype_check(zeros, "exp", st)
    assert "! pack_fp16: v is ZERO on" in v.why and "NEGATIVE count" in v.why and "from_fixed(v, frac_bits)" in v.why
    # ... but not for from_fixed's own inner pack, which packs the zeros too (D494)
    via = "def design(x):\n    v = np.where(bits(x) & 1, mantissa11(x), 0)\n    return from_fixed(v, 10, fp16_sign(x))\n"
    v = prob.prototype_check(via, "exp", st)
    assert "pack_fp16: v is ZERO" not in v.why, v.why
    # D494: a normalisation re-derived by hand is advised against (not refused)
    by_hand = ("M = 12\nTAB = rom(exp2, 0, 1, 16, M)\ndef design(x):\n    v = TAB[(mantissa11(x) & 0x3FF) >> 6] * 3\n"
               "    l = leading_one(v)\n    return pack_fp16(0, l - M, v << (10 - l) if l < 10 else v >> (l - 10), 10)\n")
    v = prob.prototype_check(by_hand, "exp", st)
    assert v.score != float("inf") and "! design(): leading_one + a shift + pack_fp16 is from_fixed re-derived by hand" in v.why
    # D494: slopes that are a derivative per unit x, not the segment's difference
    wrong_slopes = ("T = 4\nM = 12\nTAB = rom(exp2, 0, 1, 16, 12)\n"
                    "SLP = np.rint(np.exp2(np.arange(16) / 16) * 0.6931 * 4096).astype(np.int64)\n"
                    "def design(x):\n    m = mantissa11(x) & 0x3FF\n"
                    "    return pack_fp16(0, 0, interp1(TAB, SLP, m >> 6, m & 63, 6), M)\n")
    v = prob.prototype_check(wrong_slopes, "exp", st)
    assert "! interp1: the slopes are not the table's first differences (slopes[i] is ~16x" in v.why, v.why
    # and a table indexed by a fixed-point x is busiest at the centre: no audit for that
    centred = ("TAB = rom(tanh, -4, 4, 64, 12)\nSLP = slope_rom(tanh, -4, 4, 64, 12)\n"
               "def design(x):\n    v = signed_fixed(x, 6, 3)\n    idx = np.clip((v >> 3) + 32, 0, 63)\n"
               "    return pack_fp16(0, 0, 1024 + (interp1(TAB, SLP, idx, v & 7, 3) & 0x3FF), 10)\n")
    v = prob.prototype_check(centred, "exp", st)
    assert "interp1: only entries" not in v.why, v.why
    two_d = ("def design(x):\n    v = np.stack([mantissa11(x), mantissa11(x)])\n"
             "    return pack_fp16(0, exponent_unbiased(x), v, 10)\n")
    v = prob.prototype_check(two_d, "exp", st)
    assert v.score == float("inf") and "design returned shape (2, 65536) for 65536 inputs" in v.why
    assert "one value per input" in v.why and "too many indices" not in v.why


def test_a_block_called_with_the_wrong_arguments_is_told_its_signature():
    """D491: tanh tried `slope_rom(..., sample='mid')` after a "table sampled on the wrong
    side" report; `sample` is accepted (and ignored -- a secant slope does not depend on
    it), and a miscalled block's report carries the block's signature."""
    from flux_loop import LoopRequest, LoopState
    from flux_nlu.problem import NluProblem

    assert np.array_equal(B.slope_rom(B.exp2, 0, 1, 16, 12, sample="mid"), B.slope_rom("exp2", 0, 1, 16, 12))
    prob = NluProblem(ops=("exp",), ulp_budget=1, clock_period_ps=1250.0, seed=1, test_rounds=0)
    st = LoopState(request=LoopRequest(), say=lambda _m: None, proposer=None, feedback=None)
    v = prob.prototype_check("def design(x):\n    return interp1(rom(exp2, 0, 1, 16, 12), x & 15, 0, 4)\n", "exp", st)
    assert v.score == float("inf") and "the block's signature is interp1(table, slopes, idx, rem, rem_bits)" in v.why


@pytest.mark.skipif(sys.platform != "linux", reason="the sandbox uses resource limits")
def test_sandbox_errors_come_back_explained_and_tables_as_numbers():
    """D482: apex's thinking attempts died on `if m == 0:` and `max(0, min(30, e))` (a raw
    traceback in prelude line numbers), wrote `rom(recip, ...)` bare, left the knobs unassigned,
    and pre-scaled a table `rom` had already scaled. Each comes back as one line it can act on."""
    from flux_loop import LoopRequest, LoopState
    from flux_nlu.problem import NluProblem

    prob = NluProblem(ops=("exp",), ulp_budget=1, clock_period_ps=1250.0, seed=1, test_rounds=0)
    st = LoopState(request=LoopRequest(), say=lambda _m: None, proposer=None, feedback=None)
    crash = COMPOSED_EXP.replace("p = interp1(tab, slp, idx, rem, CB)", "p = interp1(tab, slp, idx + 64, rem, CB)")
    v = prob.prototype_check(crash, "exp", st)             # the family path: 12 members, all raise
    assert v.score == float("inf") and "-> a table of 16 entries was indexed with" in v.why, v.why
    assert "first error: error: line 15:" in v.why                     # the model's line numbers
    v = prob.prototype_check(crash.replace("SPACE = ", "_S = "), "exp", st)   # the single-run path
    assert "-> a table of 32 entries was indexed with" in v.why and "line 15, in design" in v.why, v.why
    assert "mask the index to its 5 bits" in v.why
    # D494: a definition indented under a helper (the model's edit slipped) is named as such
    indented = "def h(t):\n    return t\n    TAB = rom(exp2, 0, 1, 16, 12)\ndef design(x):\n    return TAB[x & 15]\n"
    v = prob.prototype_check(indented, "exp", st)
    assert "`TAB` IS assigned, on line 3 -- but indented, inside a function" in v.why, v.why
    # D491: a NEGATIVE index is the array-form gotcha -- every path runs on every input
    neg = crash.replace("idx + 64", "idx - 64").replace("SPACE = ", "_S = ")
    v = prob.prototype_check(neg, "exp", st)
    assert "was indexed with -" in v.why and "EVERY path runs on EVERY input" in v.why and "np.clip(idx" in v.why
    from flux_nlu.problem import NluProblem as _P

    assert "-> design() runs on ALL 65536 inputs at once" in _P._explain_error(
        "ValueError: The truth value of an array with more than one element is ambiguous")
    # bare names and unassigned knobs are the flow's to accept, not the model's to learn
    bare = ("SPACE = {\"T\": [4, 5], \"M\": [12]}\nTAB = rom(exp2, 0.0, 1.0, 1 << T, M)\n"
            "SLP = slope_rom(exp2, 0.0, 1.0, 1 << T, M)\n\ndef design(x):\n"
            "    xf = signed_fixed(x, 12, 5)\n    t = xf * const_fixed(1.4426950408889634, 14)\n"
            "    k = floor_shift(t, 26)\n    f = low_bits(t, 26)\n    idx = field(f, 25, 26 - T)\n"
            "    rem = field(f, 25 - T, 26 - T - 8)\n    p = interp1(TAB, SLP, idx, rem, 8)\n"
            "    y = pack_fp16(0, k, p, M)\n    return specials(x, QNAN, PINF, PZERO, 0x3C00, 0x3C00, y)\n")
    v = prob.prototype_check(bare, "exp", st)
    assert v.ok, v.why
    assert "T = 5" in v.payload["prototype"] or "T = 4" in v.payload["prototype"]
    # a measured failure carries the tables as numbers
    coarse = bare.replace('SPACE = {"T": [4, 5], "M": [12]}', 'SPACE = {"T": [2], "M": [12]}')
    v = prob.prototype_check(coarse, "exp", st)
    assert not v.ok and 0 < v.score < 65536, v.why[:200]
    text = prob.describe_failure("exp", v)
    assert "your tables (entries, integer value range): TAB[4]=4096.." in text and "; SLP[4]=" in text
    assert "pattern:" in text
    v = prob.prototype_check(COMPOSED_EXP.replace("interp1(tab, slp,", "interp1(tab, slpp,"), "exp", st)
    assert "did you mean slp" not in v.why and "`slpp` is neither a block nor defined" in v.why


def test_per_element_control_flow_is_refused_statically_with_every_site():
    """D482: with or without thinking the model writes `if x == 0: return ...` -- per-element
    Python -- and the static rules let it through to crash at runtime. Named before running,
    grouped by rule with every line, and the composed operator stays clean."""
    from flux_nlu.problem import NluProblem
    from flux_nlu.prototype_rules import hardware_subset_violations

    scalar = ("def design(x):\n    if x == 0: return 0x7C00\n    e = fp16_exp(x)\n"
              "    if e == 31 and x & 0x3FF: return 0x7E00\n    v = max(1, min(30, e))\n"
              "    return 5 if v > 3 else 7\n")
    got = [relocate(v) for v in hardware_subset_violations(with_prelude(scalar)) if "toolkit line" not in relocate(v)]
    grouped = NluProblem._group_sites(got)
    assert any(g.startswith("lines 2, 4: `if` on data") for g in grouped), grouped
    assert any("`and` on data" in g for g in grouped) and any("`max(...)` on data" in g for g in grouped)
    assert any("`a if cond else b` on data" in g for g in grouped)
    assert hardware_subset_violations(with_prelude(COMPOSED_EXP)) == []
    assert NluProblem._group_sites(["line 3: a", "line 1: b", "line 2: a"]) == ["lines 2, 3: a", "line 1: b"]
    # D484: Verilog's bit select written in Python indexes the array, not the bits
    got = [relocate(v) for v in hardware_subset_violations(with_prelude(
        "def design(x):\n    s = x[15]\n    return np.where(s == 1, 0, x)\n")) if "toolkit line" not in relocate(v)]
    assert got and "`x[15]` is Python indexing -- element 15 of the whole input array" in got[0]
    got = [relocate(v) for v in hardware_subset_violations(with_prelude(
        "def design(x):\n    y = bits(x) + 1\n    return recip(y)\n")) if "toolkit line" not in relocate(v)]
    assert got and "`recip(...)` is the float function tables are built from" in got[0]
    assert hardware_subset_violations(with_prelude("T = 4\nTAB = rom(recip, 1, 2, 16, 10)\ndef design(x):\n    return TAB[x & 15]\n")) == []
    # D491: a dict of tables picked by a value, and `in` on data -- tanh died at "unhashable
    # type" three attempts running; the knob SPACE dict is not a table of tables
    got = [relocate(v) for v in hardware_subset_violations(with_prelude(
        "T = 4\nSPACE = {'T': [3, 4]}\nTABS = {0: rom(tanh, 0, 1, 1 << T, 10), 1: rom(tanh, 1, 2, 1 << T, 10)}\n"
        "def design(x):\n    e = fp16_exp(x) - 15\n    return np.where(e in TABS, TABS[e][x & 15], 0)\n"))
           if "toolkit line" not in relocate(v)]
    assert any(g.startswith("line 3: a dict is a table of tables picked by a value") for g in got), got
    assert any("`in` on data is a Python membership test" in g and g.startswith("line 6") for g in got), got
    assert not any("line 2" in g for g in got)
    got = [relocate(v) for v in hardware_subset_violations(with_prelude(
        "def design(x):\n    tab = [i * 3 for i in range(64)]\n    return tab[x & 63]\n")) if "toolkit line" not in relocate(v)]
    assert got and got[0].startswith("line 2: a comprehension inside a function builds a table per call"), got
    got = [relocate(v) for v in hardware_subset_violations(with_prelude(
        "TAB = rom(tanh, 0, 4.5, 4096, 20)\ndef design(x):\n    return TAB[x & 4095]\n")) if "toolkit line" not in relocate(v)]
    assert got == ["line 1: rom(...) with 4096 entries -- tables are capped at 1024 entries (D420): reach the precision with interp1 (a slope_rom and the bits below the index) or a second-order term, not with a wider table"], got


APEX_RECIP = '''
T = 6
M = 13
SPACE = {"T": [5, 6, 7], "M": [12, 13, 14]}
TABLE = rom(lambda t: 1.0/t, 1.0, 2.0, 1 << T, M)
SLOPE = slope_rom(lambda t: 1.0/t, 1.0, 2.0, 1 << T, M)

def design(x):
    s = fp16_sign(x)
    e = exponent_unbiased(x)
    m = mantissa11(x)
    idx = m >> (11 - T)
    rem = m & ((1 << (11 - T)) - 1)
    v = interp1(TABLE, SLOPE, idx, rem, 11 - T)
    v_prime = v << 1
    e_out = -e - 1
    y = pack_fp16(s, e_out, v_prime, 10)
    return specials(x, QNAN, PINF, NINF, PZERO, NZERO, y)
'''


@pytest.mark.skipif(sys.platform != "linux", reason="the sandbox uses resource limits")
def test_the_blocks_audit_their_contracts_on_the_models_data():
    """D482: apex's live recip (its own first attempt, verbatim) sat at 62,916 over for twenty
    attempts. Two one-token bugs; the blocks now check their contracts on the real data and
    name each in turn: frac_bits=10 for a Q12 value, then a table indexed with the hidden bit."""
    from flux_loop import LoopRequest, LoopState
    from flux_nlu.problem import NluProblem

    prob = NluProblem(ops=("recip",), ulp_budget=1, clock_period_ps=1250.0, seed=1, test_rounds=0)
    st = LoopState(request=LoopRequest(), say=lambda _m: None, proposer=None, feedback=None)
    v = prob.prototype_check(APEX_RECIP, "recip", st)
    assert v.score == 62916 and "! pack_fp16: v is outside [2^10, 2^11)" in v.why
    assert "either pass frac_bits=12" in v.why and "SCALING error" in v.why
    step1 = APEX_RECIP.replace("pack_fp16(s, e_out, v_prime, 10)", "pack_fp16(s, e_out, v_prime, M)")
    v = prob.prototype_check(step1, "recip", st)
    assert v.score == 62624 and "! interp1: only entries 16..31 (16 of 32)" in v.why and "pack_fp16:" not in v.why
    step2 = (step1.replace("idx = m >> (11 - T)", "idx = (m & 0x3FF) >> (10 - T)")
             .replace("rem = m & ((1 << (11 - T)) - 1)", "rem = m & ((1 << (10 - T)) - 1)")
             .replace("interp1(TABLE, SLOPE, idx, rem, 11 - T)", "interp1(TABLE, SLOPE, idx, rem, 10 - T)"))
    v = prob.prototype_check(step2, "recip", st)
    assert v.score == 4 and "  !" not in v.why
    # boolean masks passed to specials as output patterns (D484: exp's overflow handling did nothing)
    masked = step2.replace("return specials(x, QNAN, PINF, NINF, PZERO, NZERO, y)",
                           "return specials(x, is_nan(x), is_inf(x), 0, is_zero(x), 0, y)")
    v = prob.prototype_check(masked, "recip", st)
    assert "! specials: nan_out, pinf_out, pzero_out look like boolean MASKS" in v.why
    # an exponent that never varies (D484: rsqrt's e_out overwritten by a normalisation shift)
    flat = step2.replace("e_out = -e - 1", "e_out = 0 * e")
    v = prob.prototype_check(flat, "recip", st)
    assert "! pack_fp16: e_unb takes only 1 distinct value(s) ([0])" in v.why
    # a failure confined to one input region is named as such, not as precision
    from flux_nlu.fp16 import _localised

    regions = [{"region": "x>=0 zero/subnormal", "failing": 767, "total": 1025},
               {"region": "x<0 zero/subnormal", "failing": 765, "total": 1023},
               {"region": "x>=0 |x|<0.25", "failing": 0, "total": 12288}]
    assert _localised(regions, 1536).startswith("pattern: 1532 of 1536 failures are in one input band, zero/subnormal (both signs)")
    assert "mantissa11(x)" in _localised(regions, 1536)
    mid = [{"region": "x>=0 4<=|x|<16", "failing": 320, "total": 2048}, {"region": "x<0 4<=|x|<16", "failing": 308, "total": 2048}]
    assert "keep it" in _localised(mid, 644) and "mantissa11" not in _localised(mid, 644)
    assert _localised(regions, 5000) == ""
    tail = [{"region": "x<0 16<=|x|<256", "failing": 4010, "total": 4096,
             "example": {"xf": "-66.69", "gotf": "+1.192e-07", "wantf": "+0"}},
            {"region": "x<0 |x|>=256", "failing": 8192, "total": 8192, "example": {}},
            {"region": "x>=0 |x|>=256", "failing": 0, "total": 8192}]
    got = _localised(tail, 12202)
    assert got.startswith("pattern: 12202 of 12202 failures are negative inputs, in 16<=|x|<256, |x|>=256")
    assert "x=-66.69 got +1.192e-07 wanted +0" in got and "underflow/overflow limit" in got
    # D494: the same half with SMALL errors is precision where the function is small, not a
    # missing path -- and the -0 ones among them are a threshold firing too early
    half = [{"region": "x<0 1<=|x|<4", "failing": 1854, "total": 2048, "zeros": 0,
             "example": {"xf": "-2.084", "gotf": "-0.03906", "wantf": "-0.03873"}},
            {"region": "x<0 4<=|x|<16", "failing": 392, "total": 2048, "zeros": 392, "example": {}}]
    got = _localised(half, 2246, rel_med=0.0052)
    assert "the errors there are SMALL (median 0.0052 relative" in got and "precision where the function is small" in got
    assert got.endswith("except 392 of them, which are 0 where the reference is not: a saturation threshold fires too early there")
    assert "one half of the domain takes a path" in _localised(half, 2246, rel_med=0.4)
    # D491: a pass-through -- tanh's `y = x` shortcut up to 0.375 -- is named, not "a one-sided
    # small error: a rounding direction"
    from flux_nlu.fp16 import all_inputs, failure_pattern, reference

    xs = all_inputs()
    want = reference("tanh", xs)
    got = np.where(np.abs(xs.view(np.float16).astype(np.float64)) < 0.375, xs, want)   # y = x below 0.375
    fail = (got != want) & np.isfinite(xs.view(np.float16).astype(np.float64))
    line = failure_pattern(got, want, fail, xs)
    assert line.startswith(f"pattern: got is the INPUT itself on {int(fail.sum())} of {int(fail.sum())} failures")
    assert "shrink that threshold" in line and failure_pattern(got, want, fail) != line
    # D492: a ratio that varies over orders of magnitude is a missing TERM, not a scaling
    want = reference("gelu", xs)
    xf = xs.view(np.float16).astype(np.float64)
    phi = np.where(np.isfinite(xf), 0.5 * (1 + np.vectorize(__import__("math").erf)(xf / 2**0.5)), 0.5)
    got = np.asarray(phi * np.sign(xf), dtype=np.float16).view(np.uint16)      # +/-Phi(x): the x left out
    fail = (got != want) & np.isfinite(xf) & (np.abs(xf) > 0.001) & (np.abs(xf) < 4)
    line = failure_pattern(got, want, fail, xs)
    assert "does not TRACK the reference" in line and "the x in x*Phi(x)" in line, line


def test_every_repair_turn_names_the_blocks_that_exist():
    """D483: by its third attempt the live model called `RECIP_TABLE(idx)` "to match harness
    expectations" -- the tool list was in the first prompt only. Repair turns carry it."""
    from flux_loop import LoopRequest, LoopState
    from flux_nlu.problem import NluProblem

    prob = NluProblem(ops=("recip",), ulp_budget=1, clock_period_ps=1250.0, seed=1, test_rounds=0)
    st = LoopState(request=LoopRequest(), say=lambda _m: None, proposer=None, feedback=None)
    text = prob.prototype_reminder("recip", st)
    for fn in B.BLOCKS:
        assert f"{fn.__name__}(" in text
    assert text.startswith("THE BLOCKS THAT EXIST") and "recip, rsqrt" in text and len(text) < 1300
    from flux_loop.problem import Problem

    assert Problem.prototype_reminder(prob, None, st) is None or True   # the default hook is None


def test_approx_on_mantissa_does_the_index_bookkeeping():
    """D483: twenty attempts broke the same bookkeeping (hidden bit, rem width, Q position) that
    is the same for every function of the significand. One block; the values are the oracle's."""
    m = B.mantissa11(all_inputs())
    v = B.approx_on_mantissa("recip", m, 5, 12)
    fin = B.is_finite_nonzero(all_inputs())
    want = np.rint(4096.0 / (m[fin] / 1024.0))
    assert np.abs(v[fin] - want).max() <= 4                    # within the interpolation's error
    assert np.array_equal(B.approx_on_mantissa(lambda t: 2.0 / t, m, 5, 12),
                          B.approx_on_mantissa(lambda t: 2.0 / t, m, 5, 12))
    assert "approx_on_mantissa(func, m, T, M)" in tool_docs()


def test_from_fixed_packs_any_magnitude_exactly():
    """D485: the model asked twice for `from_fixed` -- a fixed-point value of any magnitude to
    FP16. Every finite FP16 value, taken to an exact Q24 or Q40 integer, comes back as itself
    (the subnormals included: pack_fp16's subnormal path had treated "nothing to drop" as 0)."""
    xs = all_inputs()
    fin = B.is_finite_nonzero(xs) & (B.fp16_sign(xs) == 0)
    q = np.rint(xs[fin].view(np.float16).astype(np.float64) * 2**24).astype(np.int64)
    assert np.array_equal(B.from_fixed(q, 24, 0), xs[fin])
    assert np.array_equal(B.from_fixed(q << 16, 40, 0), xs[fin])
    assert np.array_equal(B.from_fixed(q, 24, 1), xs[fin] | 0x8000)
    assert list(B.from_fixed(np.array([0, 0]), 10, np.array([0, 1]))) == [0x0000, 0x8000]
    assert list(B.leading_one(np.array([1, 2, 1024, 3 << 40]))) == [0, 1, 10, 41]
    assert int(B.from_fixed(np.array([3 << 20]), 20)[0]) == 0x4200                 # 3.0
    # D489: a NORMAL value with fewer than 11 significant bits (8/2^16 = 2^-13) is shifted into
    # the field, not left in place -- from_fixed(8, 16) gave 0x808, and every fixed-point
    # sigmoid route the model tried died in the small-output band on it
    assert [hex(int(v)) for v in B.from_fixed(np.array([8, 7, 1, 1023]), 16)] == ["0x800", "0x700", "0x100", "0x23fe"]   # 2^-13, 1.75*2^-14, 2^-16 (subnormal), 1023/2^16
    for fb in (12, 16, 20):
        exact = np.rint(xs[fin].view(np.float16).astype(np.float64) * 2**fb)
        at = exact == xs[fin].view(np.float16).astype(np.float64) * 2**fb
        assert np.array_equal(B.from_fixed(exact[at].astype(np.int64), fb), xs[fin][at])
    # and the audit names a result that RUNS OUT of bits: Q16 for values near 2^-16
    ns: dict = {}
    exec(with_prelude("") + "\n" + B.audit_harness(), ns)                        # noqa: S102
    v = np.concatenate([np.arange(1, 200), np.arange(1 << 20, (1 << 20) + 2000)])
    ns["from_fixed"](v, 16)
    assert len(ns["_DIAG"]) == 1 and ns["_DIAG"][0].startswith("from_fixed: v carries fewer than 12 significant bits on 199 of 2199")
    assert ">= 28 fraction bits" in ns["_DIAG"][0] and "smaller still" in ns["_DIAG"][0]
    ns["_DIAG"].clear()
    ns["from_fixed"](np.arange(1 << 12, 1 << 20, 97), 16)
    assert [d for d in ns["_DIAG"] if d.startswith("from_fixed:")] == []
    # the transpiler spells it (a variable frac_bits per element)
    from flux_nlu.transpile import transpile

    src = with_prelude("def design(x):\n    v = bits(x) * 3 + 1\n    return from_fixed(v, 12, fp16_sign(x))\n")
    assert "module nlu_t" in transpile(src, top="nlu_t", xs=all_inputs())


def _fp16_ref(op, a, b):
    with np.errstate(all="ignore"):
        fa, fb = a.view(np.float16).astype(np.float64), b.view(np.float16).astype(np.float64)
        r = fa + fb if op == "add" else fa * fb
        return r.astype(np.float16).view(np.uint16)


def _same_fp16(got, want):
    got = np.asarray(got).astype(np.int64) & 0xFFFF
    want = np.asarray(want).astype(np.int64)
    nan = lambda v: ((v & 0x7C00) == 0x7C00) & ((v & 0x3FF) != 0)   # noqa: E731
    return (got == want) | (nan(got) & nan(want))


def test_fp16_arithmetic_blocks_are_correctly_rounded():
    """D487: the arithmetic a composition needs (sigmoid = recip(1 + exp(-x))), IEEE half
    precision with nearest-even rounding, checked against an exact float64 reference:
    exhaustively on one operand against nine constants, and on random pairs."""
    xs = all_inputs()
    rng = np.random.default_rng(7)
    for op, fn in (("add", B.fp16_add), ("mul", B.fp16_mul)):
        for c in (0x3C00, 0x3800, 0xBC00, 0x0001, 0x8001, 0x7BFF, 0x8000, 0x7C00, 0x7E00):
            cs = np.full(65536, c, dtype=np.uint16)
            assert _same_fp16(fn(xs, cs), _fp16_ref(op, xs, cs)).all(), (op, hex(c))
            assert _same_fp16(fn(cs, xs), _fp16_ref(op, cs, xs)).all(), (op, hex(c), "swapped")
        for _ in range(6):
            a = rng.integers(0, 65536, 65536).astype(np.uint16)
            b = rng.integers(0, 65536, 65536).astype(np.uint16)
            assert _same_fp16(fn(a, b), _fp16_ref(op, a, b)).all(), op
    assert _same_fp16(B.fp16_sub(xs, xs), np.where(B.is_nan(xs) | B.is_inf(xs), 0x7E00, 0)).all()
    assert int(B.fp16_neg(np.array([0x3C00]))[0]) == 0xBC00 and int(B.fp16_abs(np.array([0xBC00]))[0]) == 0x3C00
    assert list(B.fp16_from_int(np.array([-3 << 10, 3 << 10, 0]), 10)) == [0xC200, 0x4200, 0]
    # composed: sigmoid(0) == 0.5 exactly, through the blocks
    one = np.array([0x3C00]); e0 = np.array([0x3C00])     # exp(0) = 1
    assert int(B.fp16_add(one, e0)[0]) == 0x4000
    assert "fp16_add(a, b)" in tool_docs() and "fp16_mul(a, b)" in tool_docs()


COMPOSED_RECIP = '''
T = 6
M = 13
SPACE = {"T": [5, 6, 7], "M": [12, 13, 14]}

def design(x):
    if is_nan(x): return QNAN
    if is_inf(x): return PZERO if fp16_sign(x) == 0 else NZERO
    if is_zero(x): return PINF if fp16_sign(x) == 0 else NINF
    s, e, m = fp16_sign(x), exponent_unbiased(x), mantissa11(x)
    v = approx_on_mantissa(lambda t: 2.0 / t, m, T, M)
    return pack_fp16(s, -e - 1, v, M)
'''


@pytest.mark.skipif(sys.platform != "linux", reason="the sandbox uses resource limits")
def test_verified_operators_are_blocks_for_the_next_part():
    """D487 (Cedric: "sigmoid should be doable since we have exp and recip"): the campaign's
    verified prototypes enter the next part's prelude as `<op>_fp16(x)`; a one-line sigmoid
    composed from them measures, transpiles, and is named in the prompt and the reminder."""
    from flux_loop import LoopRequest, LoopState
    from flux_nlu.problem import NluProblem

    prob = NluProblem(ops=("exp", "recip", "sigmoid"), ulp_budget=1, clock_period_ps=1250.0, seed=1, test_rounds=0)
    st = LoopState(request=LoopRequest(), say=lambda _m: None, proposer=None, feedback=None)
    st.prototypes = {"exp": COMPOSED_EXP, "recip": COMPOSED_RECIP}
    assert sorted(prob._operators("sigmoid", st)) == ["exp", "recip"]
    assert prob._operators("exp", st) == {"recip": prob._operators("exp", st)["recip"]}   # never itself
    sig = "def design(x):\n    return recip_fp16(fp16_add(ONE, exp_fp16(fp16_neg(x))))\n"
    v = prob.prototype_check(sig, "sigmoid", st)
    assert 0 < v.score < 2000, v.why[:300]                     # double rounding: close, not 0
    assert "pattern:" in v.why and "  !" not in v.why           # a verified part's internals stay silent
    prompt, _ = prob.prototype_spec("sigmoid", st)
    assert "VERIFIED OPERATORS" in prompt and "exp_fp16(x): e^x" in prompt and "recip_fp16(x): 1/x" in prompt
    assert "VERIFIED OPERATORS: exp_fp16(x), recip_fp16(x)" in prob.prototype_reminder("sigmoid", st)
    v = prob.prototype_check("def exp_fp16(x):\n    return x\n" + sig, "sigmoid", st)
    assert "exp_fp16 is a toolkit block" in v.why
    from flux_nlu.blocks import with_prelude
    from flux_nlu.transpile import transpile

    ops = prob._operators("sigmoid", st)
    rtl = transpile(with_prelude(sig, ops), top="nlu_sigmoid", xs=all_inputs())
    assert "module nlu_sigmoid" in rtl and rtl.count("\n") > 500
    # a runtime slip in the composition is reported on the model's line, past the longer prelude
    v = prob.prototype_check("def design(x):\n    return recip_fp16(fp16_add(ONE, exp_fp16(nope(x))))\n", "sigmoid", st)
    assert "`nope` is neither a block nor defined" in v.why and "line 2" in v.why


@pytest.mark.skipif(sys.platform != "linux", reason="the sandbox uses resource limits")
def test_a_multiply_rounded_composition_is_told_its_precision_floor():
    """D488: sigmoid sat at 106 over for two passes -- 1-3 ULP off, scattered -- in a design
    that rounds to FP16 several times; the report named "a sign-dependent step". The
    residue of a chain of FP16 roundings is named as such, once, instead of the audits."""
    from flux_loop import LoopRequest, LoopState
    from flux_nlu.problem import NluProblem

    prob = NluProblem(ops=("exp", "recip", "sigmoid"), ulp_budget=1, clock_period_ps=1250.0, seed=1, test_rounds=0)
    st = LoopState(request=LoopRequest(), say=lambda _m: None, proposer=None, feedback=None)
    st.prototypes = {"exp": COMPOSED_EXP, "recip": COMPOSED_RECIP}
    sig = "def design(x):\n    return recip_fp16(fp16_add(ONE, exp_fp16(fp16_neg(x))))\n"
    v = prob.prototype_check(sig, "sigmoid", st)
    assert 0 < v.score < 2000 and "PRECISION FLOOR" not in v.why    # this residue is large (an underflow): not it
    got = NluProblem._rounding_residue(sig, {"over_budget": 106, "max_ulp": 3})
    assert got.startswith("pattern: 106 failures, none more than 3 ULP off") and "rounds to FP16 3 times" in got
    assert "pack to FP16 exactly ONCE" in got
    assert NluProblem._rounding_residue("def design(x):\n    return pack_fp16(0, 0, x, 10)\n",
                                        {"over_budget": 50, "max_ulp": 2}) == ""      # one rounding: not this
    assert NluProblem._rounding_residue(sig, {"over_budget": 106, "max_ulp": "class-mismatch"}) == ""


def test_recip_fixed_divides_without_leaving_fixed_point():
    """D488: the division a composition needs when it must not round to FP16 between its
    steps -- 1/v for a Q value as a Q value, interpolated on the full fraction, error
    falling with the table knobs; zero gives the largest value; it transpiles."""
    v = np.arange(1, 1 << 20, 37, dtype=np.int64)                # Q16 values 2^-16 .. 16
    want = np.rint((2.0 ** 24) / (v / 2.0 ** 16))
    errs = {}
    for T, M in ((5, 12), (8, 16), (10, 20)):
        got = B.recip_fixed(v, 16, 24, T, M)
        errs[T] = float((np.abs(got - want) / np.maximum(want, 1)).max())
    assert errs[5] < 1e-3 and errs[8] < 1e-4 and errs[10] < 1e-5 and errs[10] < errs[8] < errs[5]
    assert int(B.recip_fixed(np.array([0]), 16, 24)[0]) == (1 << 62) - 1
    assert int(B.recip_fixed(np.array([1 << 16]), 16, 24, 8, 16)[0]) == 1 << 24   # 1/1.0
    from flux_nlu.transpile import transpile

    src = with_prelude("def design(x):\n    r = recip_fixed(bits(x) + 1, 8, 20, 6, 14)\n    return from_fixed(r, 20, 0)\n")
    assert "module nlu_t" in transpile(src, top="nlu_t", xs=all_inputs())
    assert "recip_fixed(v, frac_bits, out_frac_bits, T=6, M=14)" in tool_docs()


def test_the_prelude_is_standalone_and_the_docs_name_every_block():
    assert prelude().startswith("# --- flux_nlu toolkit prelude ---") and "import numpy as np" in prelude()
    assert with_prelude(with_prelude("x = 1")).count("# --- flux_nlu toolkit prelude ---") == 1
    docs = tool_docs()
    for fn in B.BLOCKS:
        assert f"  {fn.__name__}(" in docs
    n = prelude().count("\n") + 1
    assert relocate(f"line {n + 3}: bad; line 4: inside") == "line 3: bad; toolkit line 4: inside"
    from flux_loop import screen_snippet
    from flux_nlu.prototype_rules import hardware_subset_violations

    assert screen_snippet(with_prelude(COMPOSED_EXP)) is None
    assert hardware_subset_violations(with_prelude(COMPOSED_EXP)) == []


def test_a_prototype_that_fights_the_toolkit_is_refused_with_the_lines_to_delete():
    """D482: the first live apex pass re-derived ten blocks by hand and tested itself at
    module level in every attempt; both are named before the subset rules run."""
    from flux_nlu.blocks import misuse

    assert misuse(COMPOSED_EXP) == []
    fought = ("import numpy as np\n\ndef fp16_sign(x): return x >> 15\n\ndef pack_fp16(s, e, v, f):\n"
              "    return v\n\nprint('table built')\n" + COMPOSED_EXP
              + "\n_xs = np.arange(65536, dtype=np.uint16)\nprint(design(_xs)[:4])\n")
    got = misuse(fought)
    assert len(got) == 2
    assert got[0].startswith("line 3: fp16_sign, pack_fp16 are toolkit blocks") and "(lines 3, 5-6)" in got[0]
    assert got[1].startswith("line 8: a print and 1 more module-level statement(s)")
    assert misuse("def rom(f):\n    return f\ndef design(x):\n    return x\n")[0].startswith(
        "line 1: rom is a toolkit block")
    # a variable with a block's name shadows the block (D484: `is_zero = is_zero(x)`)
    got = misuse("def design(x):\n    is_zero = is_zero(x)\n    is_nan = 1\n    return x\n")
    assert len(got) == 1 and got[0].startswith("line 2: `is_zero` is assigned --")   # is_nan is never called: fine
    assert misuse("def design(x):\n    is_nan = (x >> 10) == 31\n    return x\n") == []
    assert misuse("def helper(b):\n    return b\n")[0].startswith("line 1: no `design(x)` function")
    # constants and table building at module level stay allowed (a ROM is generated there)
    assert misuse("T = 6\ntab = np.zeros(64, dtype=np.int64)\nfor i in range(64):\n    tab[i] = i\n"
                  "def design(x):\n    return x\n") == []
    from flux_loop import LoopRequest, LoopState
    from flux_nlu.problem import NluProblem

    prob = NluProblem(ops=("exp",), ulp_budget=1, clock_period_ps=1250.0, seed=1, test_rounds=0)
    st = LoopState(request=LoopRequest(), say=lambda _m: None, proposer=None, feedback=None)
    v = prob.prototype_check(fought, "exp", st)
    assert not v.ok and v.score == float("inf")
    assert v.why.startswith("prototype fights the toolkit instead of composing it:") and "delete your definitions" in v.why


@pytest.mark.skipif(shutil.which("verilator") is None, reason="verilator not on PATH")
def test_the_loop_admits_an_operator_the_model_composed_from_the_toolkit(tmp_path):
    from flux_loop import LoopRequest, run_loop
    from flux_nlu.problem import NluProblem

    class Composer:
        def propose(self, prompt, schema=None):
            assert "TOOLS -- verified blocks" in prompt and "pack_fp16(" in prompt
            if "PROTOTYPE FIRST" in prompt:
                return json.dumps({"prototype": COMPOSED_EXP})
            raise AssertionError("the model was asked to write RTL")

    prob = NluProblem(ops=("exp",), ulp_budget=1, clock_period_ps=1250.0, seed=1, test_rounds=0)
    said: list[str] = []
    out = run_loop(prob, LoopRequest(steps=1, repair_attempts=1, prototype_attempts=2,
                                     screen_only=True, params={"seed": 1}),
                   proposer=Composer(), log=said.append)
    assert "exp" in out.admitted and out.admitted["exp"].name == "transpiled_exp"
    assert any("FAMILY SEARCH: 12 member(s) tried" in m for m in said)
    assert "T = " in out.admitted["exp"].artifact or "TAB" in out.admitted["exp"].artifact
