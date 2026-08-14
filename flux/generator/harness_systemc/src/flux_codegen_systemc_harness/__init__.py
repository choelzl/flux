"""Design-agnostic build/trace/verify harness for generated SystemC modules (docs/decisions.md
D39). See `build.compile_and_run`'s docstring for the real entry point.
"""

from .build import HarnessRunResult, compile_and_run
from .errors import CompileError, InvalidSpecError
from .keywords import CPP_RESERVED_WORDS, check_not_reserved
from .spec import DesignSpec, Port, TestVector, design_spec_from_dict

__all__ = [
    "HarnessRunResult",
    "compile_and_run",
    "CompileError",
    "InvalidSpecError",
    "DesignSpec",
    "Port",
    "TestVector",
    "design_spec_from_dict",
    "CPP_RESERVED_WORDS",
    "check_not_reserved",
]
