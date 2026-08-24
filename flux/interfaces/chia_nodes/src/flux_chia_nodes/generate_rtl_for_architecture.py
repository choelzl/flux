"""`flux_generate_rtl_for_architecture` — the architecture→RTL bridge as a node (docs/decisions.md
D100): derive a real `DesignSpec` from an accepted Architecture IR + Workload IR pair
(`flux_generation.derive_design_spec` — deterministic ports from the candidate's own compute
width, golden vectors computed in Python before any LLM sees anything), then run the real,
existing RTL-generation loop (`flux_generate_rtl_module`, D44) to implement and harness-verify
it. From candidate architecture to verified RTL in one call, no caller-authored spec.
"""

from __future__ import annotations

from flux_llm import default_local_model
from dataclasses import dataclass
from typing import Any

from chia.base.ChiaFunction import ChiaFunction
from flux_generation import DerivedSpec, derive_design_spec

from .generate_rtl import GenerationResult, flux_generate_rtl_module

_DEFAULT_MODEL = default_local_model()


@dataclass(frozen=True, slots=True)
class ArchitectureRtlReport:
    """All three verdicts reported separately, per this repo's no-opaque-ok-flag convention: the
    deterministic derivation, the LLM generation's own full `GenerationResult` (against the shown
    vectors), and the HOLDOUT verification (docs/decisions.md D223) — fresh golden vectors the
    LLM never saw, same module, same golden model. `overfitted_repair` is the case the holdout
    exists to catch: a repair loop that converged onto the disclosed vectors without implementing
    the behavior."""

    derived: DerivedSpec
    generation: GenerationResult
    holdout: Any | None = None  # HarnessRunResult when generation succeeded, else None
    # Rounds of holdout-driven regeneration (D234): a holdout failure re-prompts generation with
    # only the failure COUNT — the held-out values never travel into any prompt.
    holdout_regens: int = 0

    @property
    def overfitted_repair(self) -> bool:
        return (
            self.generation.success
            and self.holdout is not None
            and not self.holdout.all_passed
        )

    @property
    def success(self) -> bool:
        if not self.generation.success:
            return False
        return self.holdout is None or self.holdout.all_passed

    def to_dict(self) -> dict[str, Any]:
        return {
            "derived": self.derived.to_dict(),
            "generation": self.generation.to_dict(),
            "holdout": self.holdout.to_dict() if self.holdout is not None else None,
            "holdout_regens": self.holdout_regens,
            "overfitted_repair": self.overfitted_repair,
            "success": self.success,
        }


@ChiaFunction()
def flux_generate_rtl_for_architecture(
    workload: dict[str, Any],
    arch: dict[str, Any],
    *,
    n_vectors: int = 4,
    model: str = _DEFAULT_MODEL,
    max_repair_attempts: int = 3,
    max_holdout_regens: int = 1,
    llm: Any | None = None,
) -> ArchitectureRtlReport:
    """Derive a `DesignSpec` for `arch`'s own compute width (deterministic — a `DerivationError`
    for anything outside the bridge's named scope costs no LLM spend), then generate and
    harness-verify a real Verilog implementation of it via `flux_generate_rtl_module`'s existing
    generate-verify-repair loop. The returned report carries the derived spec itself, so the
    verification is independently reproducible: same (workload, arch) pair, same golden vectors,
    every time.
    """
    from flux_codegen_rtl_harness import compile_and_run, design_spec_from_dict

    derived = derive_design_spec(workload, arch, n_vectors=n_vectors)
    holdout_spec = derive_design_spec(
        workload, arch, n_vectors=max(8, 2 * n_vectors), vector_seed_salt="holdout"
    )

    spec = derived.spec
    generation = None
    holdout = None
    regens = 0
    for round_index in range(max_holdout_regens + 1):
        generation = flux_generate_rtl_module(
            spec, model=model, max_repair_attempts=max_repair_attempts, llm=llm,
        )
        if not generation.success:
            break
        # Held-out verification (docs/decisions.md D223): the repair loop feeds failing vectors
        # back to the LLM, so the vectors it was graded on are DISCLOSED — a repair can converge
        # onto them without implementing the behavior. The final verdict re-runs the real
        # harness on fresh golden vectors that no prompt ever contained.
        holdout = compile_and_run(
            generation.final_source, design_spec_from_dict(holdout_spec.spec)
        )
        if holdout.all_passed or round_index == max_holdout_regens:
            break
        # Holdout-driven regeneration (docs/decisions.md D234): the failure COUNT drives another
        # generation round, but the held-out vectors themselves stay undisclosed — telling the
        # LLM which inputs failed would convert the holdout into more shown vectors and revive
        # exactly the overfit D223 exists to catch. Only the fact of generalization failure
        # travels.
        regens += 1
        import copy

        spec = copy.deepcopy(derived.spec)
        spec["behavior"] += (
            f" NOTE: a previous implementation passed all listed test vectors but failed "
            f"{holdout.total_vectors - holdout.passed_vectors} additional unseen vectors drawn "
            "from this same behavior — it fit the examples instead of implementing the rule. "
            "Implement the general behavior exactly as described; do not special-case any "
            "specific input values."
        )
    return ArchitectureRtlReport(
        derived=derived, generation=generation, holdout=holdout, holdout_regens=regens
    )
