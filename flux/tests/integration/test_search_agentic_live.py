"""`flux_search_agentic` against a real local Ollama model (`qwen2.5-coder:7b`, no API
credentials — docs/decisions.md D9) and real ZigZag, validated against exhaustive search's
*proven* true optimum for `mlp-gemm0.yaml` + `simple-npu-1d-v1.yaml` (1554 cycles, established by
`tests/integration/test_search_exhaustive_live.py`'s real, exhaustive run of all 18
configurations) — the same discipline `test_search_annealing_live.py` already uses for its own
strategy.

**Why 1554.0 is a deterministic, not flaky, assertion even with a real LLM in the loop**: the
18-combination space is small enough that `AgenticMappingStrategy`'s fallback-to-random-unvisited
behaviour (triggered by an invalid or already-visited LLM proposal — see `strategy.py`) guarantees
every combination gets visited exactly once within 18 rounds, regardless of how many rounds the
LLM itself contributes a valid, non-repeating proposal versus how many fall back. Running for
exactly `max_iterations=18` therefore guarantees full coverage of the same space
`test_search_exhaustive_live.py` covers — so the best value found is guaranteed to be 1554.0, not
a matter of the LLM's judgement quality. What *is* a genuine test of the LLM's contribution is
`fallback_count < 18` — proof the LLM produced at least one valid, useful, non-repeating proposal,
not that the deterministic fallback net did all the work alone.

Requires a real local Ollama server with `qwen2.5-coder:7b` pulled (`ollama pull qwen2.5-coder:7b`
— no API key), the real `chia` package (for `chia.models.ollama.OllamaLLM`; see
`flows/chia_nodes/README.md` for the submodule gotcha), and `flux-evaluator-zigzag`.
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
from flux_search_agentic import run_agentic_search

logging.getLogger("zigzag").setLevel(logging.WARNING)

FLUX_ROOT = Path(__file__).resolve().parents[2]
GEMM_WORKLOAD = FLUX_ROOT / "core/ir/workload/examples/mlp-gemm0.yaml"
SIMPLE_NPU_1D = FLUX_ROOT / "core/ir/architecture/examples/simple-npu-1d-v1.yaml"
_KNOWN_TRUE_OPTIMUM = 1554.0  # proven by test_search_exhaustive_live.py's exhaustive real run


class _OllamaProposer:
    """Adapts `chia.models.ollama.OllamaLLM` (CHIA's real, credential-free local-inference
    backend, docs/decisions.md D9) onto `flux_search_agentic.LLMProposer`'s one-method
    interface — kept out of the `flux_search_agentic` package itself so it stays CHIA-agnostic
    (docs/architecture.md's L5/L6 layering), matching how `flows/chia_nodes/parallel.py`'s
    `ChiaParallelEvaluator` is the CHIA-specific adapter for the Evaluator ABI rather than
    `search/architecture` importing Ray directly.
    """

    def __init__(self, model: str | None = None) -> None:
        from chia.models.ollama import OllamaLLM

        self._llm = OllamaLLM(model=model)

    def propose(self, prompt: str) -> str:
        result = self._llm.prompt(prompt)
        return result.result


@pytest.fixture
def workload_and_arch():
    return (
        flux_ir.load_document(GEMM_WORKLOAD),
        flux_ir.load_document(SIMPLE_NPU_1D),
    )


def test_agentic_search_covers_the_full_space_and_finds_the_proven_optimum(workload_and_arch):
    workload, arch = workload_and_arch
    report = run_agentic_search(
        workload, arch, ZigZagEvaluator(), _OllamaProposer(),
        for_op="mlp.gemm0", metric="latency_cycles", max_iterations=18, seed=0,
    )

    assert report.iterations == 18
    # The 6 candidates that spatially split B hit a real zigzag-dse==3.8.5 bug (a size-1 temporal
    # loop) — test_search_exhaustive_live.py already established this for the same 18-candidate
    # space; this strategy must record the same 6 as skipped, not crash on them.
    assert report.skipped_not_expressible == 6
    assert all(
        e.candidate.spatial_dim == "B" for e in report.evaluated if e.error is not None
    )
    assert report.best is not None
    assert report.best_result.metrics["latency_cycles"].value == pytest.approx(_KNOWN_TRUE_OPTIMUM)

    # The LLM must have contributed at least one real, valid, non-repeating proposal — otherwise
    # this "agentic" search is just the deterministic fallback net doing all 18 evaluations.
    assert report.fallback_count < report.iterations


def test_agentic_search_never_reports_a_value_below_the_proven_optimum(workload_and_arch):
    """Whatever it finds in fewer than the full 18 rounds, it can never beat the true minimum —
    a real evaluator sanity check, not just a search-quality one.
    """
    workload, arch = workload_and_arch
    report = run_agentic_search(
        workload, arch, ZigZagEvaluator(), _OllamaProposer(),
        for_op="mlp.gemm0", metric="latency_cycles", max_iterations=6, seed=1,
    )
    assert report.iterations == 6
    assert report.best_result.metrics["latency_cycles"].value >= _KNOWN_TRUE_OPTIMUM


def test_a_real_wall_clock_budget_stops_a_real_llm_driven_search_early(workload_and_arch):
    """docs/decisions.md D73: a real, enforced wall-clock budget against a real local LLM +
    real ZigZag — a real local Ollama round trip (proposal generation) dominates each iteration's
    own cost far more than the fast ZigZag evaluation, confirmed directly: a 3s budget stops
    after just 1 real iteration here, well short of the full 18-candidate space.
    """
    workload, arch = workload_and_arch
    report = run_agentic_search(
        workload, arch, ZigZagEvaluator(), _OllamaProposer(),
        for_op="mlp.gemm0", metric="latency_cycles", max_iterations=18, seed=0,
        wall_clock_budget_s=3.0,
    )
    assert report.stopped_early is True
    assert report.iterations < 18
