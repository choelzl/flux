"""What a generated-design harness is told and what it reports, for any target language (D453).

The spec a driver is generated from, the netlist a composite is wired from, the run result a
harness returns, and the reserved-identifier check -- with no language in any of it.
`flux_codegen_rtl_harness` and `flux_codegen_systemc_harness` each own their own emission and
import this; before it existed, the RTL harness imported the SystemC package to get its spec
types and the two composition parsers had drifted apart (see `compose.py`).
"""

from .compose import CompositionSpec, Instance, composition_spec_from_dict
from .errors import InvalidSpecError
from .keywords import reserved_check
from .run import HarnessRunResult
from .spec import (VALID_DIRS, VALID_DTYPES, DesignSpec, Port, TestVector, design_spec_from_dict,
                   parse_bits)

__all__ = [
    "VALID_DIRS",
    "VALID_DTYPES",
    "CompositionSpec",
    "DesignSpec",
    "HarnessRunResult",
    "Instance",
    "InvalidSpecError",
    "Port",
    "TestVector",
    "composition_spec_from_dict",
    "design_spec_from_dict",
    "parse_bits",
    "reserved_check",
]
