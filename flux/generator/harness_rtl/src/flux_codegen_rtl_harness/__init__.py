"""Design-agnostic build/trace/verify harness for generated Verilog modules, via real Verilator
(docs/decisions.md D43). See `build.compile_and_run`'s docstring for the real entry point.
Shares `DesignSpec`/`Port`/`TestVector`/`design_spec_from_dict` with
`flux_codegen_systemc_harness` — re-exported here, not forked (nothing in that spec is
SystemC-specific).
"""

from flux_codegen_systemc_harness import DesignSpec, Port, TestVector, design_spec_from_dict

from .asap7 import Asap7NotAvailableError, Asap7SynthesisResult, synthesize_with_asap7
from .build import HarnessRunResult, compile_and_run
from .cache import ToolResultCache, content_key
from .gemm_wrapper import (
    gemm_cycles,
    gemm_leaf_port_spec,
    gemm_spec,
    generate_gemm_wrapper,
)
from .sequential_wrapper import (
    generate_sequential_wrapper,
    generate_tiled_wrapper,
    leaf_operand_names,
    leaf_port_spec,
    sequential_spec,
)
from .errors import CompileError, InvalidSpecError
from .keywords import VERILOG_RESERVED_WORDS, check_not_reserved
from .synth import (SynthesisError, SynthesisResult, UnsupportedForSynthesisError,
                    synthesize_and_measure, unpacked_array_ports)

__all__ = [
    "generate_sequential_wrapper",
    "generate_gemm_wrapper",
    "gemm_cycles",
    "gemm_leaf_port_spec",
    "gemm_spec",
    "generate_tiled_wrapper",
    "leaf_operand_names",
    "leaf_port_spec",
    "sequential_spec",
    "HarnessRunResult",
    "compile_and_run",
    "CompileError",
    "InvalidSpecError",
    "DesignSpec",
    "Port",
    "TestVector",
    "design_spec_from_dict",
    "SynthesisResult",
    "SynthesisError",
    "UnsupportedForSynthesisError",
    "unpacked_array_ports",
    "synthesize_and_measure",
    "VERILOG_RESERVED_WORDS",
    "check_not_reserved",
    "ToolResultCache",
    "content_key",
    "Asap7SynthesisResult",
    "Asap7NotAvailableError",
    "synthesize_with_asap7",
]
