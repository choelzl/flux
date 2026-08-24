"""D483: per-element Python is accepted -- `if`/`return`/`and`/`min` on one input become
np.where and friends before the rules, the sandbox and the transpiler see the text."""

from __future__ import annotations

import sys

import numpy as np
import pytest

from flux_nlu.blocks import with_prelude
from flux_nlu.fp16 import all_inputs, ulp_report
from flux_loop.pyint.vectorize import VectorizeError, relocate_lines, vectorize

SCALAR_EXP = '''
XF = 12; LB = 14; T = 5; M = 12; CB = 8
SPACE = {"T": [4, 5], "M": [12, 13], "CB": [6, 8]}
TAB = rom("exp2", 0.0, 1.0, 1 << T, M)
SLP = slope_rom("exp2", 0.0, 1.0, 1 << T, M)

def design(x):
    if is_nan(x):
        return QNAN
    if is_inf(x):
        return PINF if fp16_sign(x) == 0 else PZERO
    if is_zero(x):
        return 0x3C00
    xf = signed_fixed(x, XF, 5)
    t = xf * const_fixed(1.4426950408889634, LB)
    k = floor_shift(t, XF + LB)
    f = low_bits(t, XF + LB)
    idx = field(f, XF + LB - 1, XF + LB - T)
    rem = field(f, XF + LB - T - 1, XF + LB - T - CB)
    p = interp1(TAB, SLP, idx, rem, CB)
    if k > 16:
        return PINF
    return pack_fp16(0, k, p, M)
'''


def _run(code: str, x):
    ns: dict = {}
    exec(with_prelude(vectorize(code)[0]), ns)                  # noqa: S102 -- the test's own text
    return ns["design"](x)


def test_branches_merge_and_early_returns_fold_in_order():
    src = ("def design(x):\n    if x == 0:\n        return 100\n    if x < 0 or x > 10:\n"
           "        return 200\n    v = x * 2\n    if x > 5:\n        v = v + 1\n        w = 7\n"
           "    elif x > 2:\n        v = min(v, 9)\n    else:\n        w = 3\n"
           "    return v if x > 1 else -v\n")
    text, linemap = vectorize(src)
    assert "if " not in text.split("def design")[1].replace("np.where", "")   # no `if` statement left
    assert " or " not in text and "np.minimum" in text
    ns: dict = {}
    exec("import numpy as np\n" + text, ns)                       # noqa: S102
    xs = np.arange(-3, 13)
    got = ns["design"](xs)

    def ref(x):                                                  # the per-element meaning
        if x == 0:
            return 100
        if x < 0 or x > 10:
            return 200
        v = x * 2
        if x > 5:
            v = v + 1
        elif x > 2:
            v = min(v, 9)
        return v if x > 1 else -v
    assert list(got) == [ref(int(x)) for x in xs]
    # every array-form line maps to a line the model wrote
    assert set(linemap.values()) <= set(range(1, src.count("\n") + 1)) and linemap
    assert relocate_lines("line 3: x", linemap) == f"line {linemap[3]}: x"


def test_truth_tests_of_values_are_not_bit_inversions():
    """D483: `not fp16_sign(x)` became `~fp16_sign(x)` -- -1 and -2, both truthy -- and the live
    model's recip failed on the signed zeros it had handled correctly."""
    src = ("def design(x):\n    s = x & 1\n    if not s:\n        return 10\n"
           "    return 20 if not (x & 2) else 30 if s and x > 4 else 40\n")
    text, _ = vectorize(src)
    assert "~" not in text and "== 0" in text and "!= 0" in text
    ns: dict = {}
    exec("import numpy as np\n" + text, ns)                       # noqa: S102
    xs = np.arange(8)
    ref = [10 if not (x & 1) else 20 if not (x & 2) else 30 if (x & 1) and x > 4 else 40 for x in xs]
    assert list(ns["design"](xs)) == ref
    text, _ = vectorize("def design(x):\n    if not (x > 3):\n        return 1\n    return 2\n")
    assert "~(x > 3)" in text or "~ (x > 3)" in text.replace("(", "( ")   # a boolean keeps its ~


def test_a_scalar_composition_runs_on_the_whole_domain_and_matches_the_array_one():
    xs = all_inputs().astype(np.int64)
    got = np.asarray(_run(SCALAR_EXP, xs), dtype=np.int64) & 0xFFFF
    assert ulp_report("exp", all_inputs(), got.astype(np.uint16))["over_budget"] == 0
    untouched = "T = 5\ndef design(x):\n    return np.where(x > 3, x, 0)\n"
    assert vectorize(untouched) == (untouched, {})            # array form already: unchanged


def test_loops_keep_their_names_and_data_loops_are_refused():
    text, _ = vectorize("def design(x):\n    v = x\n    if x > 0:\n        v = v + 1\n"
                        "    for _ in range(3):\n        v = v * 2\n    return v\n")
    ns: dict = {}
    exec("import numpy as np\n" + text, ns)                       # noqa: S102
    assert list(ns["design"](np.array([-1, 2]))) == [-8, 24]
    # a data `while` is unrolled as predicated steps: a normalisation loop in per-element style
    text, _ = vectorize("def design(x):\n    e = 0\n    v = x\n    while v < 1024:\n        v = v << 1\n"
                        "        e = e - 1\n    return v * 100 + (-e)\n")
    ns = {}
    exec("import numpy as np\n" + text, ns)                       # noqa: S102
    assert list(ns["design"](np.array([1, 3, 1024, 1500]))) == [102400 + 10, 153600 + 9, 102400, 150000]
    with pytest.raises(VectorizeError, match="break/continue"):
        vectorize("def design(x):\n    while x > 1:\n        x = x >> 1\n        if x == 4:\n            break\n    return x\n")
    with pytest.raises(VectorizeError, match="inside a loop"):
        vectorize("def design(x):\n    v = x\n    for i in range(2):\n        if v > 3:\n            v = 1\n    return v\n")


def test_table_indexing_inside_a_branch_is_clipped_for_the_elements_that_never_took_it():
    src = ("TAB = np.arange(8) * 10\n\ndef design(x):\n    if x < 8:\n        v = TAB[x]\n"
           "    else:\n        v = lookup(TAB, x - 8) + 1\n    return v\n")
    text, _ = vectorize(src)
    assert text.count("np.clip(") == 2 and "len(TAB) - 1" in text
    ns: dict = {}
    exec(with_prelude(text), ns)                                  # noqa: S102
    assert list(ns["design"](np.array([0, 7, 8, 15]))) == [0, 70, 1, 71]


@pytest.mark.skipif(sys.platform != "linux", reason="the sandbox uses resource limits")
def test_the_check_accepts_per_element_style_and_transpiles_it():
    from flux_loop import LoopRequest, LoopState
    from nlu_fixtures import nlu_problem

    prob = nlu_problem(ops=("exp",), ulp_budget=1, clock_period_ps=1250.0, seed=1, test_rounds=0)
    st = LoopState(request=LoopRequest(), say=lambda _m: None, proposer=None, feedback=None)
    v = prob.prototype_check(SCALAR_EXP, "exp", st)
    assert v.ok and v.why.startswith("FAMILY SEARCH: 8 member(s) tried")
    cand = prob.transpile(v.payload["prototype"], "exp", st)
    assert cand is not None and cand.name == "transpiled_exp" and "module nlu_exp" in cand.artifact
    # a runtime slip inside per-element text is reported on the model's own line
    broken = SCALAR_EXP.replace("p = interp1(TAB, SLP, idx, rem, CB)", "p = interp1(TAB, SLP, idx + 64, rem, CB)")
    v = prob.prototype_check(broken, "exp", st)
    assert v.score == float("inf") and "-> a table of" in v.why
    model_line = next(i for i, ln in enumerate(broken.splitlines(), 1) if "idx + 64" in ln)
    assert f"line {model_line}" in v.why, v.why[:600]
