"""C++/SystemC reserved-identifier checking (docs/decisions.md D55) — the SystemC sibling of
`flux_codegen_rtl_harness.keywords` (D51), applied at the same identifier-introduction points
`compose.py` controls: `top_module_name`, `instance_name`, and net names. Same motivating class of
bug as D51's, checked proactively before composition ever tried a design that would trigger it
(D51 itself was found reactively, from a real Verilator failure) — a real C++ keyword (e.g.
`"class"`, `"new"`, `"template"`) or a SystemC macro/type name a composite's own generated code
already uses verbatim (e.g. `"SC_MODULE"`, `"sc_signal"`, `"wait"`, `"sensitive"`) as an instance
or net name would produce the same class of raw, hard-to-diagnose compiler error deep in a
generated file the caller never wrote by hand.

Deliberately C++-specific, not shared with `flux_codegen_rtl_harness.keywords` (Verilog-specific,
D51's own docstring already explains why the two must stay separate): a Verilog `DesignSpec` may
legally use identifiers C++ reserves (e.g. `"wire"` is a real Verilog keyword but an unremarkable
C++ identifier), and vice versa (`"reg"` is fine in C++).
"""

from __future__ import annotations

from .errors import InvalidSpecError

# ISO C++ (C++17, the standard this harness compiles against) reserved keywords — not exhaustive
# of every alternative-operator spelling (`bitand`, `compl`, ...), but covers every keyword a real
# design/instance/net identifier is plausibly chosen from.
CPP_RESERVED_WORDS = frozenset({
    "alignas", "alignof", "and", "and_eq", "asm", "auto",
    "bitand", "bitor", "bool", "break",
    "case", "catch", "char", "char8_t", "char16_t", "char32_t", "class", "compl", "concept",
    "const", "consteval", "constexpr", "constinit", "const_cast", "continue", "co_await",
    "co_return", "co_yield",
    "decltype", "default", "delete", "do", "double", "dynamic_cast",
    "else", "enum", "explicit", "export", "extern",
    "false", "float", "for", "friend",
    "goto",
    "if", "inline", "int",
    "long",
    "mutable",
    "namespace", "new", "noexcept", "not", "not_eq", "nullptr",
    "operator", "or", "or_eq",
    "private", "protected", "public",
    "register", "reinterpret_cast", "requires", "return",
    "short", "signed", "sizeof", "static", "static_assert", "static_cast", "struct", "switch",
    "template", "this", "thread_local", "throw", "true", "try", "typedef", "typeid", "typename",
    "union", "unsigned", "using",
    "virtual", "void", "volatile",
    "wchar_t", "while",
    "xor", "xor_eq",
    # Real, checked SystemC macro/type identifiers this harness's own generated code depends on
    # verbatim — an instance or net sharing one of these names would collide with the generated
    # driver/composite's own declarations, not just be poor style.
    "SC_MODULE", "SC_CTOR", "SC_METHOD", "SC_THREAD", "sensitive", "wait",
    "sc_in", "sc_out", "sc_signal", "sc_in_clk", "sc_clock", "sc_module", "sc_main",
})


def check_not_reserved(name: str, *, context: str) -> None:
    """Raise `InvalidSpecError` if `name` is a real C++ keyword or a SystemC macro/type identifier
    this harness's own generated code already uses — `context` is a short human description (e.g.
    "instance name", "top_module_name") used in the error message, so a caller sees exactly which
    identifier and role triggered it.
    """
    if name in CPP_RESERVED_WORDS:
        raise InvalidSpecError(
            f"{context}={name!r} is a reserved C++ keyword or SystemC macro/type identifier — "
            "choose a different identifier (this is caught here, before g++, on purpose: a real "
            "reserved-word collision otherwise surfaces as a raw compiler error deep in a "
            "generated file the caller never wrote by hand, docs/decisions.md D55, mirroring D51)."
        )
