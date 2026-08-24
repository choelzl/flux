"""`AgenticArchitectureWidthStrategy` against a real local Ollama model (`qwen2.5-coder:7b`, no
API credentials — docs/decisions.md D9) and real ZigZag, over the real, pinned architecture-
width landscape `tests/integration/test_architecture_dse_live.py` already established for
`mlp-gemm0.yaml` + `simple-npu-1d-v1.yaml`: widths {4, 8, 16, 32} give latencies
{3106, 1554, 778, 263} cycles — strictly monotonic, width=32 the unambiguous winner.

**Honestly, this landscape has no inversion to discover** (see `architecture_strategy.py`'s
module docstring) — unlike the mapping axis's `test_search_agentic_live.py`, "wider is faster"
is the whole story here. Running for exactly `max_iterations=4` (the full candidate set)
guarantees every width is tried, the same deterministic-despite-a-real-LLM argument the mapping
axis test uses, so the true minimum (263.0 cycles) is a certainty, not a hope. What *is* a real,
non-guaranteed check on the LLM's contribution is `fallback_count < 4`.

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
from flux_search_agentic import run_agentic_architecture_search

logging.getLogger("zigzag").setLevel(logging.WARNING)

FLUX_ROOT = Path(__file__).resolve().parents[2]
GEMM_WORKLOAD = FLUX_ROOT / "core/ir/workload/examples/mlp-gemm0.yaml"
SIMPLE_NPU_1D = FLUX_ROOT / "core/ir/architecture/examples/simple-npu-1d-v1.yaml"
_VALID_WIDTHS = [4, 8, 16, 32]
_KNOWN_MINIMUM = 263.0  # proven by test_architecture_dse_live.py's real ZigZag sweep


class _OllamaProposer:
    """Same CHIA-specific adapter test_search_agentic_live.py uses for the mapping axis — kept
    out of the flux_search_agentic package itself so it stays CHIA-agnostic.
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


def test_agentic_architecture_search_covers_the_full_space_and_finds_the_known_minimum(
    workload_and_arch,
):
    workload, base_arch = workload_and_arch
    report = run_agentic_architecture_search(
        workload, base_arch, ZigZagEvaluator(), _OllamaProposer(),
        metric="latency_cycles", valid_widths=_VALID_WIDTHS, max_iterations=4, seed=0,
    )

    assert report.iterations == 4
    assert report.skipped_not_expressible == 0  # every one of these 4 real IR docs is expressible
    assert report.best is not None
    assert report.best.width == 32
    assert report.best_result.metrics["latency_cycles"].value == pytest.approx(_KNOWN_MINIMUM)
    assert report.fallback_count < report.iterations  # the LLM contributed a real proposal


def test_agentic_architecture_search_never_beats_the_known_minimum(workload_and_arch):
    workload, base_arch = workload_and_arch
    report = run_agentic_architecture_search(
        workload, base_arch, ZigZagEvaluator(), _OllamaProposer(),
        metric="latency_cycles", valid_widths=_VALID_WIDTHS, max_iterations=2, seed=1,
    )
    assert report.iterations == 2
    assert report.best_result.metrics["latency_cycles"].value >= _KNOWN_MINIMUM
