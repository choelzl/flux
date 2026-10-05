"""Design-agnostic build/trace/verify harness for generated Verilog modules, via real Verilator
(docs/decisions.md D43). See `build.compile_and_run`'s docstring for the real entry point.

The spec, composition and run-result types live in `flux_codegen_harness_spec` (D453) and are
re-exported here. What is Verilog's and lives here: the generated driver, the Verilator build,
the composite's SystemVerilog emission, Yosys synthesis and the Verilog reserved words.
"""

from flux_codegen_harness_spec import (CompositionSpec, DesignSpec, HarnessRunResult, Instance,
                                       Port, TestVector, design_spec_from_dict)

from .asap7 import Asap7NotAvailableError, Asap7SynthesisResult, synthesize_with_asap7
from .build import compile_and_run
from .compose import (compile_and_run_composite, composition_spec_from_dict,
                      generate_composite_module_sv, synthesize_composite)
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
from .errors import CompileError, InvalidSpecError, explain_diagnostic
from .golden import EXHAUSTIVE_BITS, Check, Golden, check_exhaustive, check_rtl, golden_vectors
from .lint import Lint, lint_rtl
from .reply import LINT_PRAGMA, fenced_module, lint_relaxed, sv_refusal
from .sweep import SweepSim, build_sweep_sim
from .keywords import VERILOG_RESERVED_WORDS, check_not_reserved
from .synth import (SynthesisError, SynthesisResult, UnsupportedForSynthesisError,
                    synthesize_and_measure, unpacked_array_ports)

__all__ = [
    "EXHAUSTIVE_BITS", "Check", "Golden", "Lint", "check_exhaustive", "check_rtl", "golden_vectors", "lint_rtl",
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
    "CompositionSpec",
    "Instance",
    "compile_and_run_composite",
    "composition_spec_from_dict",
    "generate_composite_module_sv",
    "synthesize_composite",
    "compile_and_run",
    "CompileError", "explain_diagnostic", "LINT_PRAGMA", "fenced_module", "lint_relaxed", "sv_refusal",
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
    "SweepSim",
    "build_sweep_sim",
]
