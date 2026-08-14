"""`flux_generate_architecture_candidate` — the CHIA node surface for
`flux_generation.generate_architecture_candidate` (docs/decisions.md D91, docs/roadmap.md
Phase 3.5): the LLM proposes a *whole* new Architecture IR document, real-verified against the
real exit criterion that section named and left "unchanged, not yet attempted" since the
project's own early design phase.

`_OllamaProposer` is the exact same CHIA-specific `LLMProposer` adapter `agentic.py`'s five
search nodes already use — reused directly (imported, not reimplemented), since it has no
search-specific behavior of its own.
"""

from __future__ import annotations

from flux_llm import default_local_model
from typing import Any

from chia.base.ChiaFunction import ChiaFunction
from flux_generation import GenerationResult, generate_architecture_candidate

from .agentic import _OllamaProposer

_DEFAULT_LLM_MODEL = default_local_model()


@ChiaFunction()
def flux_generate_architecture_candidate(
    workload: dict[str, Any],
    base_arch: dict[str, Any],
    objective_metric: str,
    *,
    minimize: bool = True,
    backend: str = "zigzag",
    reference_backend: str = "rtl",
    model: str = _DEFAULT_LLM_MODEL,
    calibration_db_path: str = "flux_calibration.db",
    result_db_path: str = "flux_generation_results.db",
    max_repair_attempts: int = 3,
    record_residuals: bool = False,
) -> GenerationResult:
    """Propose a new Architecture IR document for `workload` (real LLM, real repair loop on a
    real schema/evaluation error), then real-verify it: independent validity
    (`flux_check_validity`'s own real mechanism), RTL conformance within the calibrated
    uncertainty band (`flux_conformance_check`'s own real mechanism — honestly reported as
    `conformance=None`/`conformance_error=<message>` for a candidate `reference_backend` can't
    express, never a crash), and deterministic replay. See
    `flux_generation.generate_architecture_candidate`'s own docstring for the exact real
    verification steps.
    """
    llm = _OllamaProposer(model)
    return generate_architecture_candidate(
        workload, base_arch, objective_metric, llm,
        minimize=minimize, backend=backend, reference_backend=reference_backend,
        calibration_db_path=calibration_db_path, result_db_path=result_db_path,
        max_repair_attempts=max_repair_attempts, record_residuals=record_residuals,
    )
