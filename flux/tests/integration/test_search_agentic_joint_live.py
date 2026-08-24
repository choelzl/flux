"""`AgenticJointStrategy` against a real local Ollama model (`qwen2.5-coder:7b`, no API
credentials — docs/decisions.md D9) and real ZigZag, over the real, pinned joint (width, gbuf
size) landscape (docs/decisions.md D26/D28): widths {4, 32} x sizes {1.0, 1.25, 64.0} KiB for
`mlp-gemm0.yaml` + `simple-npu-1d-v1.yaml`. 1.0 KiB is infeasible at *both* widths (a real
evaluator rejection, not a low score); among the four feasible points, energy falls with width
and rises with size, so width=32/size_kb=1.25 is the unambiguous joint winner —
193018.0081255918 pJ.

**The first agentic axis over a genuinely two-dimensional candidate space** — every other
strategy in this package proposes a single scalar or a single named variant per round; this one
proposes a (width, size_kb) pair, so the LLM has to track a 2D grid, not a 1D list, and still
correctly discount the infeasible-at-both-widths size the same way `AgenticMemorySizeStrategy`'s
own live test already establishes for the single-axis case. Running for exactly
`max_iterations=6` (the full 2x3 grid) guarantees every pair is tried regardless of what the LLM
itself contributes — the same deterministic-despite-a-real-LLM argument every other axis's own
live test uses — so the true minimum is a certainty, not a hope. What *is* a real, non-guaranteed
check on the LLM's contribution is `fallback_count < 6`.

Requires a real local Ollama server with `qwen2.5-coder:7b` pulled, the real `chia` package (for
`chia.models.ollama.OllamaLLM`), and `flux-evaluator-zigzag`.
"""

from __future__ import annotations

import logging
from pathlib import Path

import flux_ir
import pytest
import _helpers

# Guard added by the D246 review: this file drove the nightly sweep red on every
# runner without an Ollama server — an unguarded failure, not a skip.
pytestmark = _helpers.requires_ollama
from flux_evaluator_zigzag import ZigZagEvaluator
from flux_search_agentic import run_agentic_joint_search

logging.getLogger("zigzag").setLevel(logging.WARNING)

FLUX_ROOT = Path(__file__).resolve().parents[2]
GEMM_WORKLOAD = FLUX_ROOT / "core/ir/workload/examples/mlp-gemm0.yaml"
SIMPLE_NPU_1D = FLUX_ROOT / "core/ir/architecture/examples/simple-npu-1d-v1.yaml"
_VALID_WIDTHS = [4, 32]
_VALID_SIZES_KB = [1.0, 1.25, 64.0]
_KNOWN_MINIMUM_WIDTH = 32
_KNOWN_MINIMUM_SIZE_KB = 1.25  # proven by test_architecture_memory_dse_live.py's real ZigZag sweep
_KNOWN_MINIMUM_ENERGY_PJ = 193018.0081255918  # same source, exact pinned value


class _OllamaProposer:
    """Same CHIA-specific adapter every other agentic live test uses — kept out of the
    flux_search_agentic package itself so it stays CHIA-agnostic.
    """

    def __init__(self, model: str | None = None) -> None:
        from chia.models.ollama import OllamaLLM

        self._llm = OllamaLLM(model=model)

    def propose(self, prompt: str) -> str:
        return self._llm.prompt(prompt).result


@pytest.fixture
def workload_and_arch():
    return (
        flux_ir.load_document(GEMM_WORKLOAD),
        flux_ir.load_document(SIMPLE_NPU_1D),
    )


def test_agentic_joint_search_covers_the_full_grid_and_finds_the_known_minimum(workload_and_arch):
    workload, base_arch = workload_and_arch
    report = run_agentic_joint_search(
        workload, base_arch, ZigZagEvaluator(), _OllamaProposer(),
        metric="energy_pj", level="gbuf", valid_widths=_VALID_WIDTHS,
        valid_sizes_kb=_VALID_SIZES_KB, max_iterations=6, seed=0,
    )

    assert report.iterations == 6
    assert report.skipped_infeasible == 2  # size_kb=1.0 at both widths
    assert report.best is not None
    assert report.best.width == _KNOWN_MINIMUM_WIDTH
    assert report.best.size_kb == _KNOWN_MINIMUM_SIZE_KB
    assert report.best_result.metrics["energy_pj"].value == pytest.approx(_KNOWN_MINIMUM_ENERGY_PJ)
    assert report.fallback_count < report.iterations  # the LLM contributed a real proposal


def test_agentic_joint_search_never_beats_the_known_minimum(workload_and_arch):
    """With only two infeasible pairs out of six, any 3 distinct attempts must include at least
    one feasible pair, so `report.best` is guaranteed non-None here — a real property of this
    specific landscape, not a defensive guess."""
    workload, base_arch = workload_and_arch
    report = run_agentic_joint_search(
        workload, base_arch, ZigZagEvaluator(), _OllamaProposer(),
        metric="energy_pj", level="gbuf", valid_widths=_VALID_WIDTHS,
        valid_sizes_kb=_VALID_SIZES_KB, max_iterations=3, seed=1,
    )
    assert report.iterations == 3
    assert report.best_result is not None
    assert report.best_result.metrics["energy_pj"].value >= _KNOWN_MINIMUM_ENERGY_PJ - 1e-3
