"""Unit tests for flux_codegen_rtl_harness.keywords (docs/decisions.md D51): the real reserved-
word check found necessary by the first genuine multi-module composition demo built with this
framework (an accumulator ALU whose register instance was named "reg" — a reserved word since
Verilog-1995 — and Verilator rejected it with a raw syntax error before this check existed).
"""

from __future__ import annotations

import pytest
from flux_codegen_rtl_harness import InvalidSpecError, VERILOG_RESERVED_WORDS, check_not_reserved


def test_the_real_bug_that_motivated_this_module_is_caught():
    with pytest.raises(InvalidSpecError, match="reg.*reserved"):
        check_not_reserved("reg", context="instance_name")


def test_ordinary_identifiers_are_accepted():
    for name in ["adder", "acc_reg", "my_module_2", "result", "clk_gen"]:
        check_not_reserved(name, context="test")  # must not raise


def test_common_keywords_are_all_caught():
    for kw in ["module", "wire", "always", "input", "output", "begin", "end", "if", "case"]:
        with pytest.raises(InvalidSpecError):
            check_not_reserved(kw, context="test")


def test_error_message_names_the_real_context():
    with pytest.raises(InvalidSpecError, match="module_name='wire'"):
        check_not_reserved("wire", context="module_name")


def test_reserved_word_set_is_reasonably_sized():
    """A real, checked sanity bound — not exhaustive (~250 real SystemVerilog keywords exist),
    but this should be a substantial, real list, not a stub of two or three words."""
    assert len(VERILOG_RESERVED_WORDS) > 150
