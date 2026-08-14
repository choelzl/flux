"""`AgenticMemorySizeStrategy` against a real local Ollama model (`qwen2.5-coder:7b`, no API
credentials — docs/decisions.md D9) and real ZigZag, over the real, pinned memory-hierarchy-size
landscape `tests/integration/test_architecture_memory_dse_live.py` already established for
`mlp-gemm0.yaml` + `simple-npu-1d-v1.yaml`'s `gbuf` level (docs/decisions.md D26): 1.0 KiB is
infeasible (the workload's working set doesn't fit — a real evaluator rejection), while 1.25/2.0/
64.0 KiB are all feasible with energy rising monotonically with size — 1.25 KiB is the
unambiguous winner.

**Unlike the architecture-width axis's `test_search_agentic_architecture_live.py`, this landscape
has a real trap for a naive "propose the smallest untried size" heuristic**: the numerically
smallest candidate (1.0 KiB) is infeasible, so an LLM (or any proposer) that only ever tries the
smallest option first will get an honest failure on its first move and must actually use that
observation, not just filesystem-style "smaller is free" intuition, to converge on the true
optimum (1.25 KiB, not 1.0 KiB). Running for exactly `max_iterations=4` (the full candidate set)
guarantees every size is tried regardless of what the LLM itself contributes — the same
deterministic-despite-a-real-LLM argument every other axis's own live test uses — so the true
minimum is a certainty, not a hope. What *is* a real, non-guaranteed check on the LLM's
contribution is `fallback_count < 4`.

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
from flux_search_agentic import run_agentic_memory_size_search

logging.getLogger("zigzag").setLevel(logging.WARNING)

FLUX_ROOT = Path(__file__).resolve().parents[2]
GEMM_WORKLOAD = FLUX_ROOT / "core/ir/workload/examples/mlp-gemm0.yaml"
SIMPLE_NPU_1D = FLUX_ROOT / "core/ir/architecture/examples/simple-npu-1d-v1.yaml"
_VALID_SIZES_KB = [1.0, 1.25, 2.0, 64.0]
_KNOWN_MINIMUM_SIZE_KB = 1.25  # proven by test_architecture_memory_dse_live.py's real ZigZag sweep
_KNOWN_MINIMUM_ENERGY_PJ = 1116618.0081255918  # same source, exact pinned value


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


def test_agentic_memory_search_covers_the_full_space_and_finds_the_known_minimum(workload_and_arch):
    workload, base_arch = workload_and_arch
    report = run_agentic_memory_size_search(
        workload, base_arch, ZigZagEvaluator(), _OllamaProposer(),
        metric="energy_pj", level="gbuf", valid_sizes_kb=_VALID_SIZES_KB, max_iterations=4, seed=0,
    )

    assert report.iterations == 4
    assert report.skipped_infeasible == 1  # exactly the 1.0 KiB candidate, a real rejection
    assert report.best is not None
    assert report.best.size_kb == _KNOWN_MINIMUM_SIZE_KB
    assert report.best_result.metrics["energy_pj"].value == pytest.approx(_KNOWN_MINIMUM_ENERGY_PJ)
    assert report.fallback_count < report.iterations  # the LLM contributed a real proposal


def test_agentic_memory_search_never_beats_the_known_minimum(workload_and_arch):
    """With only one infeasible candidate (1.0 KiB) out of four, any 2 distinct attempts must
    include at least one feasible size, so `report.best` is guaranteed non-None here — a real
    property of this specific landscape, not a defensive guess."""
    workload, base_arch = workload_and_arch
    report = run_agentic_memory_size_search(
        workload, base_arch, ZigZagEvaluator(), _OllamaProposer(),
        metric="energy_pj", level="gbuf", valid_sizes_kb=_VALID_SIZES_KB, max_iterations=2, seed=1,
    )
    assert report.iterations == 2
    assert report.best_result is not None
    assert report.best_result.metrics["energy_pj"].value >= _KNOWN_MINIMUM_ENERGY_PJ - 1e-3
