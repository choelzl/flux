"""One einsum grammar in one place (D467).

Pins the parser's behaviour, that every caller uses it, and that each backend keeps its own
refusals (they name its own limits; `NotExpressibleError` belongs to the ABI, above the IR).
"""

from __future__ import annotations

import pathlib

import pytest
from flux_ir import Einsum, Gemm, parse_einsum

FLUX = pathlib.Path(__file__).resolve().parents[2]


def test_the_grammar_and_the_dim_algebra():
    got = parse_einsum(" b c , c k -> b k ")
    assert got == Einsum(in1=("b", "c"), in2=("c", "k"), out=("b", "k"))
    assert got.two_dimensional and got.shared == ("c",)
    assert got.expected_out() == ("b", "k")
    assert got.gemm() == Gemm(batch="b", reduction="c", output="k")


def test_what_is_not_a_plain_gemm_says_which_condition_failed():
    transposed = parse_einsum("b c, c k -> k b")
    assert transposed.gemm() is None, "a transposed output is not this shape"
    assert transposed.expected_out() == ("b", "k"), "and the caller can say what it expected"

    three = parse_einsum("a b c, c k -> a b k")
    assert not three.two_dimensional and three.gemm() is None

    unshared = parse_einsum("a b, c d -> a d")
    assert unshared.shared == () and unshared.gemm() is None

    two_shared = parse_einsum("a b, a b -> a b")
    assert two_shared.shared == ("a", "b") and two_shared.gemm() is None


def test_text_that_is_not_the_grammar_is_none_not_an_exception():
    for text in ("", "nonsense", "b c -> b", "b c, c k", "b c, c k -> ", None):
        assert parse_einsum(text) is None, text


def test_every_caller_uses_it_and_nobody_keeps_a_copy():
    """No module parses the einsum grammar on its own."""
    copies, callers = [], []
    for path in sorted((FLUX).rglob("*.py")):
        if "__pycache__" in str(path) or path.name == "einsum.py":
            continue
        if path.name == "test_shared_einsum.py":
            continue
        text = path.read_text()
        if "->" in text and "re.compile" in text and "\\s*->\\s*" in text:
            copies.append(str(path.relative_to(FLUX)))
        if "parse_einsum" in text:
            callers.append(str(path.relative_to(FLUX)))
    assert not copies, f"a second einsum parser lives in: {copies}"
    assert len(callers) >= 3, callers


def test_each_backend_still_refuses_in_its_own_words():
    from flux_evaluator_abi import NotExpressibleError

    from zigzag_tools.workload_translator import einsum_op_to_zigzag_layer

    with pytest.raises(NotExpressibleError, match="ZigZag's equation grammar is bilinear"):
        einsum_op_to_zigzag_layer({"id": "op0", "kind": "einsum", "expr": "junk"}, 0)
