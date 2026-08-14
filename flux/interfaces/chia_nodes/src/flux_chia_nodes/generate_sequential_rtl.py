"""`flux_generate_sequential_rtl_for_architecture` — the D117/D118 split as one agent-callable
node: from an accepted (Workload IR, Architecture IR) pair to a verified *sequential* design whose
measured cycle count is checked against a number derived before anything was built.

Three phases, and only the middle one involves an LLM:

1. **Derive** (`flux_generation.derive_sequential_design`, deterministic): the tile's width from
   the architecture's own compute dimension, the cycle count from the workload's reduction length
   as `ceil(C / lanes)`, the wrapper's Verilog emitted here, and the golden dot product computed
   in Python. A pair outside scope raises before any LLM spend.
2. **Generate** the combinational tile only, through D44's existing generate-verify-repair loop.
   The tile's spec contains no `clk`, no `start`, no `done` — the model cannot get the handshake
   wrong because it is never given the handshake (docs/decisions.md D116 measured 0/3 when one
   call had to produce both halves; D117/D118 measure 3/3 once they are separated).
3. **Compose and measure**: the deterministic wrapper plus the generated tile through real
   Verilator, reporting the measured latency next to the predicted one.

The report keeps correctness and latency as separate findings on purpose. A design can pass its
golden vectors while taking the wrong number of cycles — that is precisely the failure a caller
using this as a reference needs to see, so `success` requires both.
"""

from __future__ import annotations

from flux_llm import default_local_model
from dataclasses import dataclass
from typing import Any

from chia.base.ChiaFunction import ChiaFunction
from flux_codegen_rtl_harness import (
    CompileError,
    HarnessRunResult,
    compile_and_run,
    design_spec_from_dict,
)
from flux_generation import (DerivedGemmDesign, DerivedSequentialDesign,
                             derive_gemm_design, derive_sequential_design)

from .generate_rtl import GenerationResult, flux_generate_rtl_module

_DEFAULT_MODEL = default_local_model()


@dataclass(frozen=True, slots=True)
class SequentialRtlReport:
    """Every phase reported separately, per this repo's no-opaque-ok-flag convention: what was
    derived (reproducible by anyone from the same candidate pair), what the LLM produced, and what
    real Verilator measured of the two composed."""

    derived: DerivedSequentialDesign
    generation: GenerationResult
    harness: HarnessRunResult | None      # None when the tile never verified, so nothing composed
    compose_error: str | None = None      # real Verilator stderr, if composition itself failed

    @property
    def predicted_cycles(self) -> int:
        return self.derived.expected_cycles

    @property
    def measured_cycles(self) -> int | None:
        return self.harness.total_cycles if self.harness else None

    @property
    def latency_matches_prediction(self) -> bool:
        return self.measured_cycles == self.predicted_cycles

    @property
    def success(self) -> bool:
        """Both halves, deliberately: a composed design that computes the right answer at the
        wrong latency is not usable as a reference, and reporting it as a pass would hide exactly
        the thing this node exists to check."""
        return bool(
            self.harness
            and self.harness.all_passed
            and self.latency_matches_prediction
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "derived": self.derived.to_dict(),
            "generation": self.generation.to_dict(),
            "harness": self.harness.to_dict() if self.harness else None,
            "compose_error": self.compose_error,
            "predicted_cycles": self.predicted_cycles,
            "measured_cycles": self.measured_cycles,
            "latency_matches_prediction": self.latency_matches_prediction,
            "success": self.success,
        }


@ChiaFunction()
def flux_generate_sequential_rtl_for_architecture(
    workload: dict[str, Any],
    arch: dict[str, Any],
    *,
    model: str = _DEFAULT_MODEL,
    max_repair_attempts: int = 3,
) -> SequentialRtlReport:
    """Derive a sequential design from the candidate pair, LLM-implement only its combinational
    tile, compose the two, and measure the result through real Verilator.

    Raises `DerivationError` (before any LLM call) for a pair outside the bridge's named scope.
    Never raises for a *failing* design: a tile that won't verify, or a composition Verilator
    rejects, comes back as a report with `success=False` and the real diagnostics attached.
    """
    derived = derive_sequential_design(workload, arch)
    generation = flux_generate_rtl_module(
        derived.leaf_spec, model=model, max_repair_attempts=max_repair_attempts,
    )
    if not generation.success:
        return SequentialRtlReport(derived=derived, generation=generation, harness=None)

    try:
        harness = compile_and_run(
            derived.wrapper_source,
            design_spec_from_dict(derived.top_spec),
            extra_sources={derived.leaf_module_name: generation.final_source},
        )
    except CompileError as exc:
        # A tile that verified standalone but won't compose is a real, distinct outcome worth
        # naming rather than folding into "generation failed" — it is a wrapper/tile interface
        # mismatch, not a behavioural error.
        return SequentialRtlReport(
            derived=derived, generation=generation, harness=None, compose_error=str(exc),
        )
    return SequentialRtlReport(derived=derived, generation=generation, harness=harness)


@dataclass(frozen=True, slots=True)
class GemmRtlReport:
    """The D121 sibling: same three phases, but the schedule is `mac_array.sv`'s own, so
    `measured_cycles` is directly comparable to what `evaluators/rtl` reports for the same pair —
    which is the whole reason to prefer this shape over the D118 one when that comparison is the
    point."""

    derived: DerivedGemmDesign
    generation: GenerationResult
    harness: HarnessRunResult | None
    compose_error: str | None = None

    @property
    def predicted_cycles(self) -> int:
        return self.derived.expected_cycles

    @property
    def measured_cycles(self) -> int | None:
        return self.harness.total_cycles if self.harness else None

    @property
    def latency_matches_prediction(self) -> bool:
        return self.measured_cycles == self.predicted_cycles

    @property
    def success(self) -> bool:
        return bool(self.harness and self.harness.all_passed and self.latency_matches_prediction)

    def to_dict(self) -> dict[str, Any]:
        return {
            "derived": self.derived.to_dict(),
            "generation": self.generation.to_dict(),
            "harness": self.harness.to_dict() if self.harness else None,
            "compose_error": self.compose_error,
            "predicted_cycles": self.predicted_cycles,
            "measured_cycles": self.measured_cycles,
            "latency_matches_prediction": self.latency_matches_prediction,
            "success": self.success,
        }


@ChiaFunction()
def flux_generate_gemm_rtl_for_architecture(
    workload: dict[str, Any],
    arch: dict[str, Any],
    *,
    model: str = _DEFAULT_MODEL,
    max_repair_attempts: int = 3,
) -> GemmRtlReport:
    """Derive the reference-dataflow GEMM design for this candidate pair (docs/decisions.md D121),
    LLM-implement only its combinational broadcast-MAC step, compose, and measure through real
    Verilator.

    Unlike `flux_generate_sequential_rtl_for_architecture`, the schedule here is the one
    `evaluators/rtl`'s own `mac_array.sv` runs — so the measured cycle count is comparable to that
    evaluator's for the same (workload, architecture), rather than merely internally consistent.
    """
    derived = derive_gemm_design(workload, arch)
    generation = flux_generate_rtl_module(
        derived.leaf_spec, model=model, max_repair_attempts=max_repair_attempts,
    )
    if not generation.success:
        return GemmRtlReport(derived=derived, generation=generation, harness=None)
    try:
        harness = compile_and_run(
            derived.wrapper_source,
            design_spec_from_dict(derived.top_spec),
            extra_sources={derived.leaf_module_name: generation.final_source},
            timeout_s=300.0,
        )
    except CompileError as exc:
        return GemmRtlReport(
            derived=derived, generation=generation, harness=None, compose_error=str(exc),
        )
    return GemmRtlReport(derived=derived, generation=generation, harness=harness)
