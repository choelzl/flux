"""How a `DesignSpec` spells in C++ (docs/decisions.md D39, split in D453).

The spec itself -- ports, dtypes, widths, array dims, test vectors and the parser that
validates them -- is `flux_codegen_harness_spec`, shared with the RTL harness because nothing
in it is about a language. What is C++'s and stays here is the SPELLING: which type an
`sc_in`/`sc_out` is instantiated with.
"""

from __future__ import annotations

from flux_codegen_harness_spec import (VALID_DIRS, VALID_DTYPES, DesignSpec, Port, TestVector,
                                       design_spec_from_dict, parse_bits)

#: The C++ type a plain (unsized) port of each dtype becomes.
DTYPE_TO_CPP = {"int": "int", "bool": "bool"}


def cpp_type(port: Port) -> str:
    """C++ type for `port`. A sized `int` port becomes `sc_int<N>` (docs/decisions.md D203); an
    unsized one stays plain `int`, so every spec written before widths existed generates
    byte-identical code.

    `sc_int` rather than a wider native type because SystemC's own fixed-width integer is what
    truncates at N bits -- the point of declaring a width is that the reference model and the
    DUT agree on overflow, which a plain `long long` would not give.
    """
    if port.bits is None:
        return DTYPE_TO_CPP[port.dtype]
    return f"sc_int<{port.bits}>"


__all__ = ["DTYPE_TO_CPP", "VALID_DIRS", "VALID_DTYPES", "DesignSpec", "Port", "TestVector",
           "cpp_type", "design_spec_from_dict", "parse_bits"]
