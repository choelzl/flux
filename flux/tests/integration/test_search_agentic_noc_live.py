"""`AgenticNocTopologyStrategy` against a real local Ollama model (`qwen2.5-coder:7b`, no API
credentials — docs/decisions.md D9) and real Booksim2, over a combined mesh+torus,
four-dimensionality (1D/2D/3D/6D), equal-64-node candidate set — the full space
`ir/architecture/examples/noc-mesh-2d-v1.yaml`'s `routing_function: dim_order` (fixed in D15)
makes both topologies usable for.

**Real, pinned measurements (docs/decisions.md D16, corrected in D25 — see below)**:

| dimensionality | mesh    | torus       |
|-----------------|---------|-------------|
| 1D (`[64]`)      | 522.709 | 409.518     |
| 2D (`[8,8]`)     | 66.196  | 58.5376     |
| 3D (`[4,4,4]`)   | 53.1183 | **49.6749** |
| 6D (`[2,...,2]`) | 52.2727 | 50.2134     |

**D25 correction**: the original D16 numbers above were biased low by a Booksim2 output-parsing
bug in `BooksimEvaluator` (it read the *first* "Packet latency average" line Booksim2 prints —
an unconverged intermediate sample-period value — instead of the last, converged one). Fixed in
`evaluators/booksim/src/flux_evaluator_booksim/adapter.py`; the table above reflects the corrected
values. Every *qualitative* D14/D15/D16 finding still holds exactly as before: torus beats mesh at
every dimensionality, mesh is monotonically decreasing in dimensionality, and torus/3D remains the
global minimum despite not being monotonic itself — only the absolute cycle counts shifted (most
for the 1D case, ~2.2-2.5x, plausibly because more congested/higher-diameter topologies take
longer to converge, so the buggy "first sample" was proportionally further from the true value).

**Unlike the architecture-width axis (D13) and the mesh-only NoC axis (D14), this landscape is
genuinely non-monotonic**: torus's 3D point is the global minimum — lower than torus's own 6D
point, even though 6D torus has marginally *fewer* average hops than 3D torus (4.05376 vs
4.03391 — this hop-count claim is unaffected by the D25 bug, since Booksim2 only ever prints one
"Hops average" line). "More dimensions is better" and "torus beats mesh" are both true in general
here, but neither predicts the actual optimum on its own — an LLM proposer genuinely has something
non-trivial to find on this axis, unlike D13/D14's strictly monotonic ones.

Requires a real local Ollama server with `qwen2.5-coder:7b` pulled, the real `chia` package, real
`flux-evaluator-booksim` (which needs `flex`/`bison` to build Booksim2 —
`nix shell nixpkgs#flex nixpkgs#bison`, see `evaluators/booksim/README.md`), and a working `g++`.
"""

from __future__ import annotations

from pathlib import Path

import flux_ir
import pytest
import _helpers

# Guard added by the D246 review: this file drove the nightly sweep red on every
# runner without an Ollama server — an unguarded failure, not a skip.
pytestmark = _helpers.requires_ollama
from flux_evaluator_booksim import BooksimEvaluator
from flux_search_agentic import run_agentic_noc_topology_search

FLUX_ROOT = Path(__file__).resolve().parents[2]
GEMM_WORKLOAD = FLUX_ROOT / "core/ir/workload/examples/mlp-gemm0.yaml"
NOC_MESH_2D = FLUX_ROOT / "core/ir/architecture/examples/noc-mesh-2d-v1.yaml"
_DIMENSIONALITIES = [[64], [8, 8], [4, 4, 4], [2, 2, 2, 2, 2, 2]]
_VALID_VARIANTS = [(topology, dims) for topology in ("mesh", "torus") for dims in _DIMENSIONALITIES]
_KNOWN_BEST_TOPOLOGY = "torus"
_KNOWN_BEST_DIMENSIONS = (4, 4, 4)
# Real, pinned Booksim2 measurement for the global minimum across all eight candidates —
# confirmed deterministic across repeated runs of the same config before relying on exact
# equality here, not assumed. Corrected in D25 (was 49.5155 under the pre-fix latency-parsing
# bug); the winning candidate (torus, [4,4,4]) is unchanged.
_KNOWN_MINIMUM = 49.6749


class _OllamaProposer:
    """Same CHIA-specific adapter the other agentic live tests use — kept out of the
    flux_search_agentic package itself so it stays CHIA-agnostic.
    """

    def __init__(self, model: str | None = None) -> None:
        from chia.models.ollama import OllamaLLM

        self._llm = OllamaLLM(model=model)

    def propose(self, prompt: str) -> str:
        return self._llm.prompt(prompt).result


@pytest.fixture
def workload_and_base_arch():
    return (
        flux_ir.load_document(GEMM_WORKLOAD),
        flux_ir.load_document(NOC_MESH_2D),
    )


def test_agentic_noc_search_covers_the_full_space_and_finds_the_real_global_minimum(
    workload_and_base_arch,
):
    """Full coverage of all 8 candidates guarantees the true minimum is found (same
    deterministic-despite-a-real-LLM argument D12/D13/D14 already use) — the genuinely interesting
    check here is *which* candidate that is: torus at 3D, not the most-dimensional option, and not
    the mesh screening a naive "wider/more-dimensional is always better" heuristic would predict.
    """
    workload, base_arch = workload_and_base_arch
    report = run_agentic_noc_topology_search(
        workload, base_arch, BooksimEvaluator(), _OllamaProposer(),
        metric="latency_cycles", valid_variants=_VALID_VARIANTS, max_iterations=8, seed=0,
    )

    assert report.iterations == 8
    assert report.skipped_not_expressible == 0  # all eight are real, expressible k-ary n-cube configs
    assert report.best is not None
    assert report.best.topology == _KNOWN_BEST_TOPOLOGY
    assert report.best.dimensions == _KNOWN_BEST_DIMENSIONS
    assert report.best_result.metrics["latency_cycles"].value == pytest.approx(_KNOWN_MINIMUM)
    assert report.fallback_count < report.iterations  # the LLM contributed a real proposal


def test_agentic_noc_search_never_beats_the_real_global_minimum(workload_and_base_arch):
    workload, base_arch = workload_and_base_arch
    report = run_agentic_noc_topology_search(
        workload, base_arch, BooksimEvaluator(), _OllamaProposer(),
        metric="latency_cycles", valid_variants=_VALID_VARIANTS, max_iterations=3, seed=1,
    )
    assert report.iterations == 3
    assert report.best_result.metrics["latency_cycles"].value >= _KNOWN_MINIMUM - 1e-3
