"""Real architecture-candidate generation (docs/decisions.md D91, docs/roadmap.md Phase 3.5).
See `architecture.generate_architecture_candidate`'s own docstring for the real entry point.
"""

from .architecture import GenerationError, GenerationResult, LLMProposer, generate_architecture_candidate
from .rtl_bridge import (DerivationError, DerivedGemmDesign, DerivedSequentialDesign,
                         DerivedSpec, derive_design_spec, derive_gemm_design,
                         derive_sequential_design)

__all__ = [
    "DerivationError",
    "DerivedSpec",
    "DerivedSequentialDesign",
    "DerivedGemmDesign",
    "derive_gemm_design",
    "derive_design_spec",
    "derive_sequential_design",
    "GenerationError",
    "GenerationResult",
    "LLMProposer",
    "generate_architecture_candidate",
]
