"""Design-agnostic build/trace/verify harness for generated SystemC modules (docs/decisions.md
D39). See `build.compile_and_run`'s docstring for the real entry point.

What a spec IS, what a composition IS and what a run REPORTS are `flux_codegen_harness_spec`'s
(D453), shared with the RTL harness and re-exported here so every caller of this package is
unchanged. What is SystemC's and lives here: the generated driver, the g++ build, the composite's
C++ emission, the C++ reserved words and how a port spells as a C++ type.
"""

from flux_codegen_harness_spec import (CompositionSpec, DesignSpec, HarnessRunResult, Instance,
                                       InvalidSpecError, Port, TestVector, design_spec_from_dict)

from .build import compile_and_run
from .compose import (compile_and_run_composite, composition_spec_from_dict,
                      generate_composite_module_cpp)
from .errors import CompileError
from .keywords import CPP_RESERVED_WORDS, check_not_reserved
from .spec import cpp_type

__all__ = [
    "CompositionSpec",
    "HarnessRunResult",
    "Instance",
    "compile_and_run",
    "compile_and_run_composite",
    "composition_spec_from_dict",
    "generate_composite_module_cpp",
    "CompileError",
    "InvalidSpecError",
    "DesignSpec",
    "Port",
    "TestVector",
    "cpp_type",
    "design_spec_from_dict",
    "CPP_RESERVED_WORDS",
    "check_not_reserved",
]
