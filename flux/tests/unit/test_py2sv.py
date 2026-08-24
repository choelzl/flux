"""D611: a verified python prototype spelled as SystemVerilog by the loop, bit for bit -- each
construct of the subset through Verilator against a golden model on every input."""

from __future__ import annotations

import pytest

from flux_codegen_rtl_harness import Golden, check_rtl
from flux_loop.golden_proto import exhaustive, table_functions
from flux_loop.py2sv import Unsupported, spell

PORTS8 = ({"name": "a", "dir": "in", "bits": 8, "unsigned": True},
          {"name": "y", "dir": "out", "bits": 16, "unsigned": True})


def _spelled_and_checked(code: str, golden, ports=PORTS8) -> str:
    g = Golden(ports=tuple(ports), fn=golden, count=64)
    sv = spell(code, list(ports), exhaustive(g), "m", table_functions)
    got = check_rtl(sv, g, module="m")
    assert got.ok, (got.error, got.lines[:3], sv[-1500:])
    return sv


CASES = {
    "branches and early returns": (
        "def design(a):\n"
        "    if a == 0:\n        return {'y': 7}\n"
        "    if a > 200:\n        v = a - 200\n    elif a > 100:\n        v = (a * 3) >> 1\n    else:\n        v = ~a & 0xFF\n"
        "    v += 1\n    return {'y': v}\n",
        lambda a: {"y": 7 if a == 0 else ((a - 200) if a > 200 else ((a * 3) >> 1) if a > 100 else (~a & 0xFF)) + 1}),
    "a loop, a helper, min/max/abs": (
        "def clamp(v):\n    return max(0, min(v, 255))\n"
        "def design(a):\n    s = 0\n    for i in range(4):\n        s += (a >> (2 * i)) & 3\n"
        "    return {'y': clamp(abs(a - 128)) + s}\n",
        lambda a: {"y": max(0, min(abs(a - 128), 255)) + sum((a >> (2 * i)) & 3 for i in range(4))}),
    "a table, bit_length, // and % by constants": (
        "SQ = [i * i for i in range(256)]\n"
        "def design(a):\n    k = a.bit_length()\n    q = a // 10\n    r = a % 10\n"
        "    return {'y': SQ[a] + k * 1000 + (q if r > 4 else 0)}\n",
        lambda a: {"y": a * a + a.bit_length() * 1000 + (a // 10 if a % 10 > 4 else 0)}),
    "signed values, conditional expressions, boolean operators": (
        "def design(a):\n    s = a - 128\n    t = s // 4 if (s < 0 and a & 1) or a == 255 else s * 2\n"
        "    return {'y': t & 0xFFFF}\n",
        lambda a: {"y": (((a - 128) // 4) if ((a - 128) < 0 and a & 1) or a == 255 else (a - 128) * 2) & 0xFFFF}),
    # D618: coefficients per segment as a table of rows, a row taken by index and read at
    # constant positions, in a branch; tuple and chained assignments
    "a table of rows, tuple and chained assignments": (
        "C = [(k, 2 * k + 1, 3) for k in range(8)]\n"
        "def design(a):\n    lo, hi = a & 31, a >> 5\n    p = q = 0\n"
        "    if hi > 3:\n        c = C[hi]\n    else:\n        c = C[7 - hi]\n"
        "    p, q = c[1] * lo, c[0] + c[-1]\n    return {'y': p + q}\n",
        lambda a: {"y": (lambda hi, lo: (2 * (hi if hi > 3 else 7 - hi) + 1) * lo + (hi if hi > 3 else 7 - hi) + 3)(a >> 5, a & 31)}),
}


@pytest.mark.parametrize("case", sorted(CASES))
def test_each_construct_is_spelled_bit_for_bit(case):
    code, golden = CASES[case]
    _spelled_and_checked(code, golden)


def test_what_is_not_spelled_says_why():
    g = Golden(ports=PORTS8, fn=lambda a: {"y": a})
    rows = exhaustive(g)
    with pytest.raises(Unsupported, match="While"):
        spell("def design(a):\n    while a > 3:\n        a -= 1\n    return {'y': a}\n", list(PORTS8), rows, "m", table_functions)
    with pytest.raises(Unsupported, match="negative"):
        spell("def design(a):\n    return {'y': ((a - 128) // 10) & 0xFFFF}\n", list(PORTS8), rows, "m", table_functions)
    with pytest.raises(Unsupported, match="does not take"):
        spell("def design(x):\n    return {'y': x}\n", list(PORTS8), rows, "m", table_functions)
